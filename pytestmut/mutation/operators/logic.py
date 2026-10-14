"""Operators on comparisons, boolean logic and conditions."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from pytestmut.mutation.model import Site
from pytestmut.mutation.operators._util import (
    is_default_value,
    node_site,
    operator_tokens,
)
from pytestmut.source.scopes import ModuleContext

_BOUNDARY = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}
_NEGATE = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}
_IDENTITY = {"is": "is not", "is not": "is"}
_MEMBERSHIP = {"in": "not in", "not in": "in"}
_BOOL_SWAP = {"and": "or", "or": "and"}


def compare_sites(ctx: ModuleContext, node: ast.Compare) -> Iterator[Site]:
    """Mutate each operator of a comparison chain."""
    operands = [node.left, *node.comparators]
    for index in range(len(node.ops)):
        tokens = operator_tokens(ctx, operands[index], operands[index + 1])
        if not tokens:
            continue
        written = " ".join(token.string for token in tokens)
        start, end = tokens[0].start, tokens[-1].end
        for operator, table in (
            ("cmp.boundary", _BOUNDARY),
            ("cmp.negate", _NEGATE),
            ("cmp.identity", _IDENTITY),
            ("cmp.membership", _MEMBERSHIP),
        ):
            if written in table:
                yield Site(operator, start, end, table[written], node)


def boolop_sites(ctx: ModuleContext, node: ast.BoolOp) -> Iterator[Site]:
    """Swap ``and`` and ``or`` between each pair of operands."""
    for left, right in zip(node.values, node.values[1:]):
        tokens = operator_tokens(ctx, left, right)
        if len(tokens) == 1 and tokens[0].string in _BOOL_SWAP:
            token = tokens[0]
            yield Site(
                "bool.and_or", token.start, token.end, _BOOL_SWAP[token.string], node
            )


def bool_literal_sites(ctx: ModuleContext, node: ast.Constant) -> Iterator[Site]:
    """Flip ``True`` and ``False``."""
    if isinstance(node.value, bool) and not is_default_value(ctx, node):
        yield node_site(ctx, "bool.literal", node, str(not node.value))


def _condition_sites(ctx: ModuleContext, test: ast.expr, force: bool) -> Iterator[Site]:
    if isinstance(test, ast.Constant):
        return
    negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
    if not isinstance(test, ast.Compare) and not negated:
        yield node_site(ctx, "cond.negate", test, f"not ({ctx.text(test)})")
    if force:
        yield node_site(ctx, "cond.const", test, "True")
        yield node_site(ctx, "cond.const", test, "False")


def if_sites(ctx: ModuleContext, node: ast.If | ast.IfExp) -> Iterator[Site]:
    """Negate or force the test of an ``if`` or a conditional expression."""
    yield from _condition_sites(ctx, node.test, force=True)


def while_sites(ctx: ModuleContext, node: ast.While) -> Iterator[Site]:
    """Negate the test of a ``while``; forcing it would loop forever."""
    yield from _condition_sites(ctx, node.test, force=False)


def comprehension_sites(ctx: ModuleContext, node: ast.comprehension) -> Iterator[Site]:
    """Negate or drop each filter of a comprehension."""
    for test in node.ifs:
        yield from _condition_sites(ctx, test, force=False)
        if not isinstance(test, ast.Constant):
            yield node_site(ctx, "comp.filter_true", test, "True")


def match_case_sites(ctx: ModuleContext, node: ast.match_case) -> Iterator[Site]:
    """Negate the guard of a ``case``."""
    if node.guard is not None:
        yield from _condition_sites(ctx, node.guard, force=False)


FAMILIES = (
    (ast.Compare, compare_sites),
    (ast.BoolOp, boolop_sites),
    (ast.Constant, bool_literal_sites),
    (ast.If, if_sites),
    (ast.IfExp, if_sites),
    (ast.While, while_sites),
    (ast.comprehension, comprehension_sites),
    (ast.match_case, match_case_sites),
)
