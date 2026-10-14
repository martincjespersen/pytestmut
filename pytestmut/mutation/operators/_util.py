"""Helpers shared by the operator families."""

from __future__ import annotations

import ast

from pytestmut.mutation.model import Site
from pytestmut.source.scopes import ModuleContext, Token

_PARENS = frozenset({"(", ")"})
_SEQUENCE_LITERALS = (ast.List, ast.Tuple, ast.JoinedStr)


def operator_tokens(ctx: ModuleContext, left: ast.AST, right: ast.AST) -> list[Token]:
    """Tokens of the operator written between two operands.

    Syntax nodes carry no position for their operator, and parenthesised
    operands leave ``) + (`` between the two spans, so the operator is whatever
    remains once parentheses are dropped.

    Arguments:
        ctx: The parsed module.
        left: Operand before the operator.
        right: Operand after it.

    Returns:
        The operator tokens in order; two for ``is not`` and ``not in``.
    """
    between = ctx.tokens_between(ctx.end(left), ctx.start(right))
    return [token for token in between if token.string not in _PARENS]


def is_text_literal(node: ast.AST) -> bool:
    """True for string and bytes literals and f-strings."""
    if isinstance(node, ast.JoinedStr):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))


def is_sequence_literal(node: ast.AST) -> bool:
    """True for literals on which ``+`` and ``*`` mean concatenation."""
    return is_text_literal(node) or isinstance(node, _SEQUENCE_LITERALS)


def is_none(node: ast.AST | None) -> bool:
    """True for the literal ``None``."""
    return isinstance(node, ast.Constant) and node.value is None


def is_default_value(ctx: ModuleContext, node: ast.AST) -> bool:
    """True when a node is the default value of a parameter."""
    return isinstance(ctx.parent(node), ast.arguments) and ctx.field_in_parent(
        node
    ) in {"defaults", "kw_defaults"}


def node_site(
    ctx: ModuleContext, operator: str, node: ast.AST, replacement: str
) -> Site:
    """A site replacing the whole text of a node."""
    return Site(
        operator=operator,
        start=ctx.start(node),
        end=ctx.end(node),
        replacement=replacement,
        node=node,
    )


def statement_removal(ctx: ModuleContext, operator: str, node: ast.stmt) -> Site | None:
    """A site replacing a statement by ``pass``.

    The statement must have its physical lines to itself; the replacement
    keeps the line count so everything after it stays on the same line.

    Arguments:
        ctx: The parsed module.
        operator: Operator id to record.
        node: Statement to remove.

    Returns:
        The site, or None when the statement shares a line with other code.
    """
    if not ctx.alone_on_lines(node):
        return None
    lines = (node.end_lineno or node.lineno) - node.lineno
    return node_site(ctx, operator, node, "pass" + "\n" * lines)
