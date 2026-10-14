"""Inline directives written as comments in the code under test.

Supported forms::

    x = a < b  # pytestmut: skip
    x = a < b  # pytestmut: skip[cmp.boundary, num.*] -- off-by-one is harmless here
    # pytestmut: off
    ...
    # pytestmut: on
    def total(self):  # pytestmut: policy=billing-core

``# pragma: no mutate`` (and any other configured foreign pragma) behaves like
a bare ``skip``; ``# pragma: no cover`` is recognised so uncovered-by-design
code can be left alone.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field

from pytestmut.source.scopes import ModuleContext

_DIRECTIVE = re.compile(
    r"#\s*pytestmut:\s*(?P<verb>skip|off|on|policy)"
    r"(?:\s*\[(?P<operators>[^\]]*)\]|\s*=\s*(?P<value>[\w.\-]+))?"
    r"(?:\s*--\s*(?P<reason>.*))?\s*$"
)
_NO_COVER = re.compile(r"#\s*pragma:\s*no\s*cover\b", re.IGNORECASE)
_COMPOUND = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Match,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


@dataclass
class Directive:
    """One suppression written in the source.

    Attributes:
        kind: ``"inline"`` for pytestmut and foreign pragmas, ``"no-cover"``
            for coverage pragmas.
        line: Line the comment is on.
        first_line: First line the directive covers.
        last_line: Last line the directive covers.
        operators: Operator globs it is limited to; empty means all.
        reason: The justification after ``--``, if any.
        hits: How many candidate mutants it suppressed.
    """

    kind: str
    line: int
    first_line: int
    last_line: int
    operators: tuple[str, ...] = ()
    reason: str = ""
    hits: int = 0

    def applies(self, first_line: int, last_line: int, operator: str) -> bool:
        """Tell whether the directive covers a mutation.

        Arguments:
            first_line: First line of the mutated text.
            last_line: Last line of the mutated text.
            operator: Operator id of the mutation.

        Returns:
            True when the line ranges intersect and the operator matches.
        """
        if last_line < self.first_line or first_line > self.last_line:
            return False
        if not self.operators:
            return True
        return any(fnmatch.fnmatchcase(operator, glob) for glob in self.operators)


@dataclass
class DirectiveIndex:
    """All directives of one file.

    Attributes:
        directives: Suppressions in source order.
        policies: Policy name by scope qualified name, from ``policy=`` tags.
        problems: Malformed directives as ``(line, message)``.
    """

    directives: list[Directive] = field(default_factory=list)
    policies: dict[str, str] = field(default_factory=dict)
    problems: list[tuple[int, str]] = field(default_factory=list)

    def match(
        self, first_line: int, last_line: int, operator: str, kind: str
    ) -> Directive | None:
        """Find the first directive of a kind covering a mutation."""
        for directive in self.directives:
            if directive.kind == kind and directive.applies(
                first_line, last_line, operator
            ):
                return directive
        return None

    def policy_for(self, qualname: str) -> str | None:
        """Policy tagged on a scope or on the nearest enclosing scope."""
        current = qualname
        while current:
            if current in self.policies:
                return self.policies[current]
            current = current.rpartition(".")[0]
        return None


def parse_directives(
    ctx: ModuleContext, foreign_pragmas: tuple[str, ...]
) -> DirectiveIndex:
    """Collect the directives of a module.

    Arguments:
        ctx: The parsed module.
        foreign_pragmas: Comment substrings from other tools that should act
            as a bare ``skip``.

    Returns:
        The directive index.
    """
    index = DirectiveIndex()
    headers = _scope_headers(ctx)
    blocks = _compound_extents(ctx)
    last_line = ctx.source.line_count
    open_block: Directive | None = None
    for comment in ctx.comments:
        if _NO_COVER.search(comment.text):
            index.directives.append(
                Directive(
                    kind="no-cover",
                    line=comment.line,
                    first_line=comment.line,
                    last_line=blocks.get(comment.line, comment.line),
                )
            )
        match = _DIRECTIVE.search(comment.text)
        if match is None:
            if "pytestmut:" in comment.text:
                index.problems.append(
                    (comment.line, f"unrecognised directive: {comment.text.strip()}")
                )
            elif any(pragma in comment.text for pragma in foreign_pragmas):
                first, last = headers.get(comment.line, (comment.line, comment.line))[
                    :2
                ]
                index.directives.append(
                    Directive(
                        kind="inline",
                        line=comment.line,
                        first_line=first,
                        last_line=last,
                    )
                )
            continue
        verb = match.group("verb")
        reason = (match.group("reason") or "").strip()
        operators = tuple(
            item.strip()
            for item in (match.group("operators") or "").split(",")
            if item.strip()
        )
        if verb == "policy":
            _tag_policy(index, headers, comment.line, match.group("value"))
        elif verb == "skip":
            first, last = headers.get(comment.line, (comment.line, comment.line))[:2]
            index.directives.append(
                Directive(
                    kind="inline",
                    line=comment.line,
                    first_line=first,
                    last_line=last,
                    operators=operators,
                    reason=reason,
                )
            )
        elif verb == "off":
            if open_block is not None:
                index.problems.append(
                    (comment.line, "'off' while a block is already open")
                )
                continue
            open_block = Directive(
                kind="inline",
                line=comment.line,
                first_line=comment.line,
                last_line=last_line,
                operators=operators,
                reason=reason,
            )
            index.directives.append(open_block)
        elif verb == "on":
            if open_block is None:
                index.problems.append((comment.line, "'on' without a matching 'off'"))
                continue
            open_block.last_line = comment.line
            open_block = None
    return index


def _tag_policy(
    index: DirectiveIndex,
    headers: dict[int, tuple[int, int, str]],
    line: int,
    value: str | None,
) -> None:
    if not value:
        index.problems.append((line, "'policy' needs a name: policy=<name>"))
    elif line not in headers:
        index.problems.append(
            (line, "'policy' must be on the first line of a def or class")
        )
    else:
        index.policies[headers[line][2]] = value


def _scope_headers(ctx: ModuleContext) -> dict[int, tuple[int, int, str]]:
    """Map each header line of a definition to its extent and name."""
    headers: dict[int, tuple[int, int, str]] = {}
    for node in ast.walk(ctx.tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        first = ctx.statement_first_line(node)
        last = node.end_lineno or node.lineno
        body_line = node.body[0].lineno if node.body else node.lineno
        for line in range(first, max(node.lineno, body_line - 1) + 1):
            headers.setdefault(line, (first, last, ctx.scope_of(node)))
    return headers


def _compound_extents(ctx: ModuleContext) -> dict[int, int]:
    """Map the first line of each compound statement to its last line."""
    extents: dict[int, int] = {}
    for node in ast.walk(ctx.tree):
        if isinstance(node, _COMPOUND):
            line = ctx.statement_first_line(node)
            extents[line] = max(extents.get(line, 0), node.end_lineno or node.lineno)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                extents[node.lineno] = max(
                    extents.get(node.lineno, 0), node.end_lineno or node.lineno
                )
        elif isinstance(node, ast.ExceptHandler):
            extents[node.lineno] = max(
                extents.get(node.lineno, 0), node.end_lineno or node.lineno
            )
    return extents
