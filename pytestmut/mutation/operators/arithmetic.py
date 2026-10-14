"""Operators on arithmetic, bitwise, augmented and unary expressions."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from pytestmut.mutation.model import Site
from pytestmut.mutation.operators._util import (
    is_sequence_literal,
    is_text_literal,
    operator_tokens,
)
from pytestmut.source.scopes import ModuleContext

# Written operator -> (operator id, replacement) pairs.
_BINARY: dict[str, tuple[tuple[str, str], ...]] = {
    "+": (("arith.add_sub", "-"),),
    "-": (("arith.add_sub", "+"),),
    "*": (("arith.mul_div", "/"),),
    "/": (("arith.mul_div", "*"), ("arith.floor_true", "//")),
    "//": (("arith.mul_div", "*"), ("arith.floor_true", "/")),
    "%": (("arith.mod_pow", "*"),),
    "**": (("arith.mod_pow", "*"),),
    "&": (("bit.and_or", "|"),),
    "|": (("bit.and_or", "&"),),
    "^": (("bit.and_or", "&"),),
    "<<": (("bit.shift", ">>"),),
    ">>": (("bit.shift", "<<"),),
}
_AUGMENTED = {
    "+=": "-=",
    "-=": "+=",
    "*=": "/=",
    "/=": "*=",
    "//=": "*=",
    "%=": "*=",
    "**=": "*=",
    "&=": "|=",
    "|=": "&=",
    "^=": "&=",
    "<<=": ">>=",
    ">>=": "<<=",
}
_UNARY = {"not": "unary.not", "-": "unary.sign", "~": "unary.sign"}


def _concatenates(written: str, left: ast.AST, right: ast.AST) -> bool:
    """True when the operator is string or sequence work, not arithmetic."""
    if written in {"+", "*"}:
        return is_sequence_literal(left) or is_sequence_literal(right)
    if written == "%":
        return is_text_literal(left)
    return False


def binop_sites(ctx: ModuleContext, node: ast.BinOp) -> Iterator[Site]:
    """Swap the operator of a binary expression."""
    tokens = operator_tokens(ctx, node.left, node.right)
    if len(tokens) != 1 or tokens[0].string not in _BINARY:
        return
    token = tokens[0]
    if _concatenates(token.string, node.left, node.right):
        return
    for operator, replacement in _BINARY[token.string]:
        yield Site(operator, token.start, token.end, replacement, node)


def augassign_sites(ctx: ModuleContext, node: ast.AugAssign) -> Iterator[Site]:
    """Swap an augmented operator or turn it into a plain assignment."""
    tokens = operator_tokens(ctx, node.target, node.value)
    if len(tokens) != 1 or tokens[0].string not in _AUGMENTED:
        return
    token = tokens[0]
    if not _concatenates(token.string[:-1], node.target, node.value):
        yield Site("aug.swap", token.start, token.end, _AUGMENTED[token.string], node)
    yield Site("aug.assign", token.start, token.end, "=", node)


def unary_sites(ctx: ModuleContext, node: ast.UnaryOp) -> Iterator[Site]:
    """Remove ``not``, unary minus or bitwise invert."""
    token = ctx.token_from(ctx.start(node))
    if token is None or token.start != ctx.start(node) or token.string not in _UNARY:
        return
    operator = _UNARY[token.string]
    if operator == "unary.sign" and isinstance(node.operand, ast.Constant):
        return
    following = ctx.token_from(token.end)
    if following is None:
        return
    yield Site(operator, token.start, following.start, "", node)


FAMILIES = (
    (ast.BinOp, binop_sites),
    (ast.AugAssign, augassign_sites),
    (ast.UnaryOp, unary_sites),
)
