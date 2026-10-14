"""Compile every mutant before any test runs.

A mutant that does not compile is ``invalid``. One whose bytecode equals the
original is ``equivalent``, and one whose bytecode equals an earlier mutant of
the same statement is a ``duplicate``. Comparing bytecode with positions
stripped catches only trivial equivalence, but it is cheap and exact.
"""

from __future__ import annotations
import __future__ as future_module

import ast
from types import CodeType

from pytestmut.mutation.model import Site
from pytestmut.source.scopes import ModuleContext

CodeKey = tuple[object, ...]


def _constant_key(value: object) -> object:
    if isinstance(value, CodeType):
        return code_key(value)
    if isinstance(value, tuple):
        return ("tuple", tuple(_constant_key(item) for item in value))
    if isinstance(value, frozenset):
        return ("frozenset", tuple(sorted(repr(_constant_key(v)) for v in value)))
    return (type(value).__name__, repr(value))


def code_key(code: CodeType) -> CodeKey:
    """Reduce a code object to what it does, ignoring where it was written.

    Arguments:
        code: A compiled code object.

    Returns:
        A hashable key equal for two code objects with the same behaviour
        and different line or column tables.
    """
    return (
        code.co_name,
        code.co_code,
        tuple(_constant_key(constant) for constant in code.co_consts),
        code.co_names,
        code.co_varnames,
        code.co_freevars,
        code.co_cellvars,
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        code.co_flags,
        getattr(code, "co_exceptiontable", b""),
    )


class Validator:
    """Compiles mutants one top-level statement at a time.

    Compiling only the enclosing module-level statement, padded with blank
    lines so line numbers stay put, is an order of magnitude cheaper than
    compiling the module once per mutant.

    Arguments:
        ctx: The parsed module.
    """

    def __init__(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        self._flags = self._future_flags(ctx.tree)
        self._original: dict[ast.stmt, CodeKey | None] = {}

    @staticmethod
    def _future_flags(tree: ast.Module) -> int:
        flags = 0
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom) and statement.module == (
                "__future__"
            ):
                for alias in statement.names:
                    feature = getattr(future_module, alias.name, None)
                    flags |= getattr(feature, "compiler_flag", 0)
        return flags

    def _extent(self, statement: ast.stmt) -> tuple[int, int, int]:
        source = self._ctx.source
        first_line = self._ctx.statement_first_line(statement)
        last_line = statement.end_lineno or statement.lineno
        start = source.line_starts[first_line - 1]
        if last_line < source.line_count:
            end = source.line_starts[last_line]
        else:
            end = len(source.text)
        return first_line, start, end

    def _compile(self, first_line: int, segment: str) -> CodeKey | None:
        padded = "\n" * (first_line - 1) + segment
        try:
            code = compile(
                padded,
                str(self._ctx.source.path),
                "exec",
                flags=self._flags,
                dont_inherit=True,
            )
        except (SyntaxError, ValueError, OverflowError, RecursionError, MemoryError):
            return None
        return code_key(code)

    def check(self, site: Site) -> tuple[str, CodeKey | None, ast.stmt | None]:
        """Compile the statement around a site with the mutation applied.

        Arguments:
            site: The candidate mutation.

        Returns:
            ``(verdict, key, statement)`` where the verdict is ``"invalid"``,
            ``"equivalent"`` or ``"ok"``, the key identifies the mutated
            behaviour, and the statement is the module-level statement that
            was compiled.
        """
        statement = self._ctx.top_level_statement(site.node)
        if statement is None:
            return "invalid", None, None
        first_line, start, end = self._extent(statement)
        if site.start < start or site.end > end:
            return "invalid", None, statement
        text = self._ctx.source.text
        if statement not in self._original:
            self._original[statement] = self._compile(first_line, text[start:end])
        mutated = text[start : site.start] + site.replacement + text[site.end : end]
        key = self._compile(first_line, mutated)
        if key is None:
            return "invalid", None, statement
        if key == self._original[statement]:
            return "equivalent", key, statement
        return "ok", key, statement
