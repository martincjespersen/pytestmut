"""Operators on literals, collections, defaults, returns and assignments."""

from __future__ import annotations

import ast
import math
import sys
from collections.abc import Iterator

from pytestmut.mutation.model import Site
from pytestmut.mutation.operators._util import is_default_value, is_none, node_site
from pytestmut.source.scopes import ModuleContext

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_BOOLEAN_SHAPED = (ast.Compare, ast.BoolOp)
# Return annotation -> the empty value of that type.
_EMPTY_BY_ANNOTATION = {
    "str": '""',
    "bytes": 'b""',
    "int": "0",
    "float": "0",
    "list": "[]",
    "List": "[]",
    "dict": "{}",
    "Dict": "{}",
    "set": "set()",
    "Set": "set()",
    "tuple": "()",
    "Tuple": "()",
}


def _number_plus_one(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return repr(value + 1)


def _emptied_text(value: str | bytes) -> str:
    prefix = "b" if isinstance(value, bytes) else ""
    return f'{prefix}""' if value else f'{prefix}"mutant"'


def _in_fstring(ctx: ModuleContext, node: ast.AST) -> bool:
    parent = ctx.parent(node)
    return isinstance(parent, (ast.JoinedStr, ast.FormattedValue)) or type(
        parent
    ).__name__ in {"TemplateStr", "Interpolation"}


def constant_sites(ctx: ModuleContext, node: ast.Constant) -> Iterator[Site]:
    """Shift numbers and empty strings."""
    if is_default_value(ctx, node) or _in_fstring(ctx, node):
        return
    shifted = _number_plus_one(node.value)
    if shifted is not None:
        yield node_site(ctx, "num.shift", node, shifted)
    elif isinstance(node.value, (str, bytes)):
        yield node_site(ctx, "str.empty", node, _emptied_text(node.value))


def default_sites(ctx: ModuleContext, node: ast.arguments) -> Iterator[Site]:
    """Change constant default values of parameters."""
    for default in [*node.defaults, *node.kw_defaults]:
        if not isinstance(default, ast.Constant):
            continue
        value = default.value
        if isinstance(value, bool):
            yield node_site(ctx, "arg.default", default, str(not value))
        elif isinstance(value, (str, bytes)):
            yield node_site(ctx, "arg.default", default, _emptied_text(value))
        else:
            shifted = _number_plus_one(value)
            if shifted is not None:
                yield node_site(ctx, "arg.default", default, shifted)


def collection_sites(
    ctx: ModuleContext, node: ast.List | ast.Tuple | ast.Set | ast.Dict
) -> Iterator[Site]:
    """Empty a non-empty collection display."""
    if isinstance(node, ast.Dict):
        if node.keys:
            yield node_site(ctx, "collection.empty", node, "{}")
        return
    if not node.elts or not isinstance(getattr(node, "ctx", ast.Load()), ast.Load):
        return
    if ctx.field_in_parent(node) == "slice":
        return
    empty = {ast.List: "[]", ast.Tuple: "()", ast.Set: "set()"}[type(node)]
    yield node_site(ctx, "collection.empty", node, empty)


def lambda_sites(ctx: ModuleContext, node: ast.Lambda) -> Iterator[Site]:
    """Make a lambda return None."""
    if not is_none(node.body):
        yield node_site(ctx, "lambda.none", node.body, "None")


def fstring_sites(ctx: ModuleContext, node: ast.FormattedValue) -> Iterator[Site]:
    """Blank the expression of an f-string field."""
    if sys.version_info >= (3, 12):
        yield node_site(ctx, "fstring.expr", node.value, "''")


def _enclosing_function(
    ctx: ModuleContext, node: ast.AST
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for ancestor, _ in ctx.ancestors(node):
        if isinstance(ancestor, _FUNCTIONS):
            return ancestor
    return None


def _annotation_name(annotation: ast.expr | None) -> str:
    if isinstance(annotation, ast.Subscript):
        annotation = annotation.value
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return "None"
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.partition("[")[0].rpartition(".")[2].strip()
    return ""


def return_sites(ctx: ModuleContext, node: ast.Return) -> Iterator[Site]:
    """Replace a returned value by None, a boolean or an empty value."""
    value = node.value
    if value is None or is_none(value):
        return
    function = _enclosing_function(ctx, node)
    annotated = _annotation_name(function.returns) if function else ""
    if annotated == "None":
        return
    written = ctx.text(value)
    yield node_site(ctx, "return.none", value, "None")
    negation = isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not)
    literal_bool = isinstance(value, ast.Constant) and isinstance(value.value, bool)
    if isinstance(value, _BOOLEAN_SHAPED) or negation or literal_bool:
        boolean = True
    else:
        boolean = annotated == "bool"
    if boolean:
        for replacement in ("True", "False"):
            if written != replacement:
                yield node_site(ctx, "return.bool", value, replacement)
    empty = _EMPTY_BY_ANNOTATION.get(annotated)
    if empty is not None and written.replace(" ", "") not in {empty, "''", "b''"}:
        yield node_site(ctx, "return.empty", value, empty)


def assign_sites(
    ctx: ModuleContext, node: ast.Assign | ast.AnnAssign
) -> Iterator[Site]:
    """Assign None instead of the computed value."""
    if node.value is None or is_none(node.value):
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if any(isinstance(target, (ast.Tuple, ast.List)) for target in targets):
        return
    yield node_site(ctx, "assign.none", node.value, "None")


FAMILIES = (
    (ast.Constant, constant_sites),
    (ast.arguments, default_sites),
    (ast.List, collection_sites),
    (ast.Tuple, collection_sites),
    (ast.Set, collection_sites),
    (ast.Dict, collection_sites),
    (ast.Lambda, lambda_sites),
    (ast.FormattedValue, fstring_sites),
    (ast.Return, return_sites),
    (ast.Assign, assign_sites),
    (ast.AnnAssign, assign_sites),
)
