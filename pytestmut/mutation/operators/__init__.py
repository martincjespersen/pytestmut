"""Operator families, grouped by the syntax node type they inspect.

A family is a function ``(ModuleContext, node) -> Iterator[Site]``. One family
may emit sites for several operator ids; the generator filters by the ids that
are enabled where the node sits.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from typing import Any

from pytestmut.mutation.model import Site
from pytestmut.mutation.operators import arithmetic, logic, statements, values
from pytestmut.source.scopes import ModuleContext

Family = Callable[[ModuleContext, Any], Iterator[Site]]


def families_by_node_type() -> dict[type[ast.AST], tuple[Family, ...]]:
    """Build the dispatch table from node type to families.

    Returns:
        Every family that inspects each node type.
    """
    table: dict[type[ast.AST], list[Family]] = {}
    for module in (logic, arithmetic, values, statements):
        for node_type, family in module.FAMILIES:
            table.setdefault(node_type, []).append(family)
    return {node_type: tuple(found) for node_type, found in table.items()}
