"""Operators on calls and whole statements."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from pytestmut.mutation.model import Site
from pytestmut.mutation.operators._util import node_site, statement_removal
from pytestmut.source.scopes import ModuleContext

_BUILTIN_SWAPS = {
    "min": ("call.minmax", "max"),
    "max": ("call.minmax", "min"),
    "any": ("call.anyall", "all"),
    "all": ("call.anyall", "any"),
}
_METHOD_SWAPS = {
    "lstrip": "rstrip",
    "rstrip": "lstrip",
    "startswith": "endswith",
    "endswith": "startswith",
    "lower": "upper",
    "upper": "lower",
    "find": "rfind",
    "rfind": "find",
}


def is_super_call(node: ast.AST) -> bool:
    """True for ``super().method(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "super"
    )


def call_sites(ctx: ModuleContext, node: ast.Call) -> Iterator[Site]:
    """Swap paired builtins and paired string methods."""
    func = node.func
    if isinstance(func, ast.Name) and func.id in _BUILTIN_SWAPS:
        if func.id not in ctx.bound_names():
            operator, replacement = _BUILTIN_SWAPS[func.id]
            yield node_site(ctx, operator, func, replacement)
    elif isinstance(func, ast.Attribute) and func.attr in _METHOD_SWAPS:
        end = ctx.end(func)
        start = end - len(func.attr)
        if ctx.source.text[start:end] == func.attr:
            yield Site("call.method_swap", start, end, _METHOD_SWAPS[func.attr], node)


def expr_statement_sites(ctx: ModuleContext, node: ast.Expr) -> Iterator[Site]:
    """Remove a statement that is only a call."""
    if not isinstance(node.value, ast.Call):
        return
    operator = "stmt.super_remove" if is_super_call(node.value) else "stmt.call_remove"
    site = statement_removal(ctx, operator, node)
    if site is not None:
        yield site


def raise_sites(ctx: ModuleContext, node: ast.Raise) -> Iterator[Site]:
    """Remove a ``raise`` that names an exception."""
    raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    if raised is None:
        return
    if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
        return
    site = statement_removal(ctx, "stmt.raise_remove", node)
    if site is not None:
        yield site


def loop_control_sites(
    ctx: ModuleContext, node: ast.Break | ast.Continue
) -> Iterator[Site]:
    """Swap ``break`` and ``continue``."""
    replacement = "continue" if isinstance(node, ast.Break) else "break"
    yield node_site(ctx, "loop.break_continue", node, replacement)


def decorator_sites(
    ctx: ModuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
) -> Iterator[Site]:
    """Remove one decorator at a time, leaving its lines blank."""
    for decorator in node.decorator_list:
        at_sign = ctx.token_before(ctx.start(decorator))
        if at_sign is None or at_sign.string != "@":
            continue
        end = ctx.end(decorator)
        following = ctx.token_from(end)
        closing = ctx.source.text[end : following.start if following else end]
        if "\n" not in closing and "\r" not in closing:
            continue
        lines = (decorator.end_lineno or decorator.lineno) - decorator.lineno
        yield Site("decorator.remove", at_sign.start, end, "\n" * lines, decorator)


FAMILIES = (
    (ast.Call, call_sites),
    (ast.Expr, expr_statement_sites),
    (ast.Raise, raise_sites),
    (ast.Break, loop_control_sites),
    (ast.Continue, loop_control_sites),
    (ast.FunctionDef, decorator_sites),
    (ast.AsyncFunctionDef, decorator_sites),
    (ast.ClassDef, decorator_sites),
)
