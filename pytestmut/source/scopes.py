"""A parsed module with the lookups mutation operators and rules need.

``ModuleContext`` pairs the syntax tree with a token index, a parent map and
the list of named scopes, and answers positional questions in character
offsets into the original text.
"""

from __future__ import annotations

import ast
import bisect
import io
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass, field

from pytestmut.exceptions import SourceError
from pytestmut.source.text import SourceText

_SKIPPED_TOKENS = frozenset(
    {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        tokenize.COMMENT,
    }
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_NAMED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# Decorators that cache a function's result between calls.
MEMOISING = frozenset(
    {
        "cache",
        "lru_cache",
        "cached_property",
        "cached",
        "cachedmethod",
        "ttl_cache",
        "lfu_cache",
        "fifo_cache",
        "rr_cache",
        "memoize",
        "memoized",
    }
)
# Fields of a function node that are evaluated when the ``def`` statement
# runs, not when the function is called.
_DEF_TIME_FIELDS = frozenset({"decorator_list", "args", "returns", "type_params"})


@dataclass(frozen=True)
class Token:
    """One significant token.

    Attributes:
        type: ``tokenize`` token type.
        string: Token text.
        start: Character index of the first character.
        end: Character index one past the last character.
    """

    type: int
    string: str
    start: int
    end: int


@dataclass(frozen=True)
class Comment:
    """One comment.

    Attributes:
        line: 1-based line the comment is on.
        text: Comment text including the leading ``#``.
        alone: True when nothing but whitespace precedes it on the line.
    """

    line: int
    text: str
    alone: bool


@dataclass(frozen=True)
class Scope:
    """A function or class definition.

    Attributes:
        qualname: Dotted name from the module level, e.g. ``Invoice.total``.
        kind: ``"function"`` or ``"class"``.
        first_line: First line including decorators.
        def_line: Line of the ``def`` or ``class`` keyword.
        last_line: Last line of the body.
    """

    qualname: str
    kind: str
    first_line: int
    def_line: int
    last_line: int


@dataclass
class ModuleContext:
    """A parsed source file.

    Attributes:
        source: The decoded file.
        tree: Its syntax tree.
        tokens: Significant tokens in order.
        comments: Comments in order.
        scopes: Named scopes in source order.
    """

    source: SourceText
    tree: ast.Module
    tokens: tuple[Token, ...]
    comments: tuple[Comment, ...]
    scopes: tuple[Scope, ...]
    _token_starts: list[int] = field(repr=False)
    _parents: dict[ast.AST, tuple[ast.AST, str]] = field(repr=False)
    _qualnames: dict[ast.AST, str] = field(repr=False)
    _bound_names: frozenset[str] | None = field(default=None, repr=False)
    _memoised: frozenset[ast.AST] | None = field(default=None, repr=False)
    _aliases: dict[str, str] | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, source: SourceText) -> ModuleContext:
        """Parse a source file.

        Arguments:
            source: The decoded file.

        Returns:
            The parsed module.

        Raises:
            SourceError: If the file is not valid Python for this interpreter.
        """
        try:
            tree = ast.parse(source.normalized, filename=str(source.path))
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise SourceError(f"{source.path}: cannot parse: {exc}") from exc
        tokens, comments = _tokenize(source)
        parents: dict[ast.AST, tuple[ast.AST, str]] = {}
        for parent in ast.walk(tree):
            for name, value in ast.iter_fields(parent):
                if isinstance(value, ast.AST):
                    parents[value] = (parent, name)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, ast.AST):
                            parents[item] = (parent, name)
        qualnames, scopes = _index_scopes(tree, parents)
        return cls(
            source=source,
            tree=tree,
            tokens=tokens,
            comments=comments,
            scopes=scopes,
            _token_starts=[token.start for token in tokens],
            _parents=parents,
            _qualnames=qualnames,
        )

    def start(self, node: ast.AST) -> int:
        """Character index where a node begins."""
        return self.source.ast_offset(
            getattr(node, "lineno"), getattr(node, "col_offset")
        )

    def end(self, node: ast.AST) -> int:
        """Character index one past where a node ends."""
        return self.source.ast_offset(
            getattr(node, "end_lineno"), getattr(node, "end_col_offset")
        )

    def text(self, node: ast.AST) -> str:
        """Source text of a node."""
        return self.source.text[self.start(node) : self.end(node)]

    def tokens_between(self, low: int, high: int) -> list[Token]:
        """Tokens lying entirely within ``[low, high)``."""
        index = bisect.bisect_left(self._token_starts, low)
        found: list[Token] = []
        while index < len(self.tokens) and self.tokens[index].end <= high:
            found.append(self.tokens[index])
            index += 1
        return found

    def token_from(self, offset: int) -> Token | None:
        """First token starting at or after ``offset``."""
        index = bisect.bisect_left(self._token_starts, offset)
        return self.tokens[index] if index < len(self.tokens) else None

    def token_before(self, offset: int) -> Token | None:
        """Last token starting before ``offset``."""
        index = bisect.bisect_left(self._token_starts, offset) - 1
        return self.tokens[index] if index >= 0 else None

    def bound_names(self) -> frozenset[str]:
        """Names the module binds itself, anywhere in the file.

        Used to tell a builtin such as ``min`` from a local redefinition.

        Returns:
            Assigned, defined, imported and parameter names.
        """
        if self._bound_names is None:
            names: set[str] = set()
            for node in ast.walk(self.tree):
                if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                    names.add(node.id)
                elif isinstance(node, _NAMED_SCOPES):
                    names.add(node.name)
                elif isinstance(node, ast.arg):
                    names.add(node.arg)
                elif isinstance(node, ast.alias):
                    names.add((node.asname or node.name).partition(".")[0])
            self._bound_names = frozenset(names)
        return self._bound_names

    def dotted_name(self, node: ast.AST) -> str:
        """Dotted name of a callable expression, resolved through imports.

        ``from shutil import rmtree as rm`` makes ``rm`` resolve to
        ``shutil.rmtree``. A base that is not a plain name is written ``?``,
        so ``Path(p).unlink`` becomes ``?.unlink``.

        Arguments:
            node: Usually the ``func`` of a call.

        Returns:
            The dotted name.
        """
        if self._aliases is None:
            aliases: dict[str, str] = {}
            for item in ast.walk(self.tree):
                if isinstance(item, ast.Import):
                    for alias in item.names:
                        if alias.asname:
                            aliases[alias.asname] = alias.name
                elif isinstance(item, ast.ImportFrom) and item.module:
                    for alias in item.names:
                        aliases[alias.asname or alias.name] = (
                            f"{item.module}.{alias.name}"
                        )
            self._aliases = aliases
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(self._aliases.get(current.id, current.id))
        else:
            parts.append("?")
        return ".".join(reversed(parts))

    def parent(self, node: ast.AST) -> ast.AST | None:
        """Parent node, or None for the module."""
        entry = self._parents.get(node)
        return entry[0] if entry else None

    def field_in_parent(self, node: ast.AST) -> str:
        """Name of the parent field that holds ``node``."""
        entry = self._parents.get(node)
        return entry[1] if entry else ""

    def ancestors(self, node: ast.AST) -> Iterator[tuple[ast.AST, str]]:
        """Walk outwards from a node.

        Arguments:
            node: Starting node (not yielded).

        Returns:
            Pairs of an ancestor and the name of its field through which the
            walk arrived.
        """
        current = node
        while True:
            entry = self._parents.get(current)
            if entry is None:
                return
            yield entry
            current = entry[0]

    def scope_of(self, node: ast.AST) -> str:
        """Qualified name of the innermost definition around a node.

        Arguments:
            node: Any node in the tree.

        Returns:
            The dotted name, or an empty string at module level.
        """
        if isinstance(node, _NAMED_SCOPES):
            return self._qualnames[node]
        for ancestor, _ in self.ancestors(node):
            if isinstance(ancestor, _NAMED_SCOPES):
                return self._qualnames[ancestor]
        return ""

    def is_static(self, node: ast.AST) -> bool:
        """Tell whether a node is evaluated at import time.

        Module-level code, class bodies, decorators and default values run
        once when the module is imported, so a mutant there must be active
        before the import and cannot be attributed to one test.

        Arguments:
            node: Any node in the tree.

        Returns:
            True when no enclosing function or lambda body contains the node.
        """
        for ancestor, via in self.ancestors(node):
            if isinstance(ancestor, _FUNCTIONS) and via not in _DEF_TIME_FIELDS:
                return False
            if isinstance(ancestor, ast.Lambda) and via == "body":
                return False
        return True

    def is_memoised(self, node: ast.AST) -> bool:
        """Tell whether a node runs inside a function whose result is cached.

        A test that gets a cached result never executes the function body,
        so tracing cannot credit it with those lines although its outcome
        depends on them.

        Arguments:
            node: Any node in the tree.

        Returns:
            True when an enclosing function carries a memoising decorator
            such as ``functools.cache``, or is called from one inside this
            module.
        """
        if self._memoised is None:
            self._memoised = self._find_memoised()
        return any(
            ancestor in self._memoised and via not in _DEF_TIME_FIELDS
            for ancestor, via in self.ancestors(node)
        )

    def _find_memoised(self) -> frozenset[ast.AST]:
        """Cached functions and the module's own functions they call.

        A helper called only from a cached function is just as invisible to
        tests that get the cached result. Calls by bare name to module-level
        functions, and through ``self`` or ``cls`` to methods of the same
        class, are followed; calls into other modules are not.
        """
        functions = [n for n in ast.walk(self.tree) if isinstance(n, _FUNCTIONS)]
        top_level = {
            node.name: node for node in self.tree.body if isinstance(node, _FUNCTIONS)
        }
        methods: dict[ast.AST, dict[str, ast.AST]] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                methods[node] = {
                    item.name: item
                    for item in node.body
                    if isinstance(item, _FUNCTIONS)
                }
        marked: set[ast.AST] = set()
        for function in functions:
            for decorator in function.decorator_list:
                target = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                if self.dotted_name(target).rpartition(".")[2] in MEMOISING:
                    marked.add(function)
        pending = list(marked)
        while pending:
            current = pending.pop()
            owner = self.parent(current)
            for call in ast.walk(current):
                if not isinstance(call, ast.Call):
                    continue
                callee: ast.AST | None = None
                if isinstance(call.func, ast.Name):
                    callee = top_level.get(call.func.id)
                elif (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in {"self", "cls"}
                    and owner in methods
                ):
                    callee = methods[owner].get(call.func.attr)
                if callee is not None and callee not in marked:
                    marked.add(callee)
                    pending.append(callee)
        return frozenset(marked)

    def statement_of(self, node: ast.AST) -> ast.stmt | None:
        """Innermost statement containing a node (the node itself if it is one)."""
        if isinstance(node, ast.stmt):
            return node
        for ancestor, _ in self.ancestors(node):
            if isinstance(ancestor, ast.stmt):
                return ancestor
        return None

    def top_level_statement(self, node: ast.AST) -> ast.stmt | None:
        """Module-level statement containing a node."""
        current: ast.AST = node
        for ancestor, _ in self.ancestors(node):
            if isinstance(ancestor, ast.Module):
                return current if isinstance(current, ast.stmt) else None
            current = ancestor
        return current if isinstance(current, ast.stmt) else None

    def cover_lines(self, node: ast.AST) -> tuple[int, int]:
        """Lines whose execution means a mutant at ``node`` was reached.

        For a simple statement this is the whole statement. For the header
        of a compound statement it is the header expression, so that running
        the body alone does not count.

        Arguments:
            node: The mutated node.

        Returns:
            Inclusive ``(first_line, last_line)``.
        """
        statement = self.statement_of(node)
        own = (getattr(node, "lineno", 1), getattr(node, "end_lineno", 1))
        if statement is None:
            return own
        if not hasattr(statement, "body"):
            return statement.lineno, statement.end_lineno or statement.lineno
        if node is statement:
            return statement.lineno, statement.lineno
        top: ast.AST = node
        for ancestor, _ in self.ancestors(node):
            if ancestor is statement:
                break
            if hasattr(ancestor, "lineno"):
                top = ancestor
        first = getattr(top, "lineno", own[0])
        last = getattr(top, "end_lineno", own[1])
        return min(first, own[0]), max(last, own[1])

    def statement_first_line(self, statement: ast.stmt) -> int:
        """First line of a statement, counting its decorators."""
        decorators: list[ast.expr] = getattr(statement, "decorator_list", [])
        return min([statement.lineno] + [item.lineno for item in decorators])

    def alone_on_lines(self, node: ast.AST) -> bool:
        """Tell whether a node shares its physical lines with nothing else.

        Arguments:
            node: Usually a statement.

        Returns:
            True when only whitespace precedes it and only whitespace or a
            comment follows it.
        """
        start, end = self.start(node), self.end(node)
        line, _ = self.source.position(start)
        end_line, _ = self.source.position(end)
        before = self.source.text[self.source.line_starts[line - 1] : start]
        after = self.source.line_text(end_line)[
            end - self.source.line_starts[end_line - 1] :
        ]
        return not before.strip() and (
            not after.strip() or after.lstrip().startswith("#")
        )


def _tokenize(source: SourceText) -> tuple[tuple[Token, ...], tuple[Comment, ...]]:
    tokens: list[Token] = []
    comments: list[Comment] = []
    reader = io.StringIO(source.normalized).readline
    try:
        for item in tokenize.generate_tokens(reader):
            if item.type == tokenize.COMMENT:
                before = source.line_text(item.start[0])[: item.start[1]]
                comments.append(
                    Comment(
                        line=item.start[0], text=item.string, alone=not before.strip()
                    )
                )
            if item.type in _SKIPPED_TOKENS:
                continue
            tokens.append(
                Token(
                    type=item.type,
                    string=item.string,
                    start=source.offset(*item.start),
                    end=source.offset(*item.end),
                )
            )
    except (tokenize.TokenError, SyntaxError) as exc:
        raise SourceError(f"{source.path}: cannot tokenize: {exc}") from exc
    return tuple(tokens), tuple(comments)


def _index_scopes(
    tree: ast.Module, parents: dict[ast.AST, tuple[ast.AST, str]]
) -> tuple[dict[ast.AST, str], tuple[Scope, ...]]:
    qualnames: dict[ast.AST, str] = {}
    scopes: list[Scope] = []
    # ast.walk is breadth-first, so an enclosing scope is always named first.
    for node in ast.walk(tree):
        if not isinstance(node, _NAMED_SCOPES):
            continue
        prefix = ""
        current: ast.AST = node
        while current in parents:
            current = parents[current][0]
            if isinstance(current, _NAMED_SCOPES):
                prefix = qualnames[current] + "."
                break
        qualname = prefix + node.name
        qualnames[node] = qualname
        first = min([node.lineno] + [item.lineno for item in node.decorator_list])
        scopes.append(
            Scope(
                qualname=qualname,
                kind="class" if isinstance(node, ast.ClassDef) else "function",
                first_line=first,
                def_line=node.lineno,
                last_line=node.end_lineno or node.lineno,
            )
        )
    scopes.sort(key=lambda scope: (scope.first_line, scope.qualname))
    return qualnames, tuple(scopes)
