"""Golden tests: what each operator produces for a given snippet."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pytestmut.mutation.model import Status
from pytestmut.mutation.registry import (
    OPERATORS,
    Level,
    expand_operators,
    operators_at,
    resolve_operators,
)
from tests.conftest import changes, generate

CASES = [
    ("x = a < b", "cmp.boundary", [("<", "<=")]),
    ("x = a >= b", "cmp.boundary", [(">=", ">")]),
    ("x = a < b", "cmp.negate", [("<", ">=")]),
    ("x = a == b", "cmp.negate", [("==", "!=")]),
    ("x = a != b", "cmp.negate", [("!=", "==")]),
    ("x = a is None", "cmp.identity", [("is", "is not")]),
    ("x = a is not None", "cmp.identity", [("is not", "is")]),
    ("x = a in b", "cmp.membership", [("in", "not in")]),
    ("x = a not in b", "cmp.membership", [("not in", "in")]),
    ("x = 0 < a <= 9", "cmp.boundary", [("<", "<="), ("<=", "<")]),
    ("x = (a) < (b)", "cmp.negate", [("<", ">=")]),
    ("x = a and b", "bool.and_or", [("and", "or")]),
    ("x = a or b or c", "bool.and_or", [("or", "and"), ("or", "and")]),
    ("x = True", "bool.literal", [("True", "False")]),
    ("x = False", "bool.literal", [("False", "True")]),
    ("if ready:\n    pass", "cond.negate", [("ready", "not (ready)")]),
    ("if a and b:\n    pass", "cond.negate", [("a and b", "not (a and b)")]),
    ("while busy():\n    pass", "cond.negate", [("busy()", "not (busy())")]),
    ("x = 1 if ok else 2", "cond.negate", [("ok", "not (ok)")]),
    ("x = [i for i in y if keep(i)]", "cond.negate", [("keep(i)", "not (keep(i))")]),
    ("if a < b:\n    pass", "cond.negate", []),
    ("if not a:\n    pass", "cond.negate", []),
    ("if ok:\n    pass", "cond.const", [("ok", "False"), ("ok", "True")]),
    ("while ok:\n    pass", "cond.const", []),
    ("x = [i for i in y if keep(i)]", "comp.filter_true", [("keep(i)", "True")]),
    ("x = not a", "unary.not", [("not ", "")]),
    ("x = -a", "unary.sign", [("-", "")]),
    ("x = ~a", "unary.sign", [("~", "")]),
    ("x = -1", "unary.sign", []),
    ("x = a + b", "arith.add_sub", [("+", "-")]),
    ("x = a - b", "arith.add_sub", [("-", "+")]),
    ("x = (a) + (b)", "arith.add_sub", [("+", "-")]),
    ("x = a +\\\n    b", "arith.add_sub", [("+", "-")]),
    ("x = 'a' + b", "arith.add_sub", []),
    ("x = [1] + b", "arith.add_sub", []),
    ("x = a * b", "arith.mul_div", [("*", "/")]),
    ("x = a / b", "arith.mul_div", [("/", "*")]),
    ("x = a // b", "arith.mul_div", [("//", "*")]),
    ("x = '-' * 10", "arith.mul_div", []),
    ("x = a / b", "arith.floor_true", [("/", "//")]),
    ("x = a // b", "arith.floor_true", [("//", "/")]),
    ("x = a % b", "arith.mod_pow", [("%", "*")]),
    ("x = a ** b", "arith.mod_pow", [("**", "*")]),
    ("x = '%s' % b", "arith.mod_pow", []),
    ("x = a & b", "bit.and_or", [("&", "|")]),
    ("x = a | b", "bit.and_or", [("|", "&")]),
    ("x = a ^ b", "bit.and_or", [("^", "&")]),
    ("x = a << 1", "bit.shift", [("<<", ">>")]),
    ("x += y", "aug.swap", [("+=", "-=")]),
    ("x //= y", "aug.swap", [("//=", "*=")]),
    ("x += 'a'", "aug.swap", []),
    ("x += y", "aug.assign", [("+=", "=")]),
    ("x = 0", "num.shift", [("0", "1")]),
    ("x = 1.5", "num.shift", [("1.5", "2.5")]),
    ("x = 0x10", "num.shift", [("0x10", "17")]),
    ("x = y[0]", "num.shift", [("0", "1")]),
    ("x = 'abc'", "str.empty", [("'abc'", '""')]),
    ("x = ''", "str.empty", [("''", '"mutant"')]),
    ("x = b'ab'", "str.empty", [("b'ab'", 'b""')]),
    ("x = 'a' 'b'", "str.empty", [("'a' 'b'", '""')]),
    ("x = f'a{b}'", "str.empty", []),
    ("def f():\n    return total", "return.none", [("total", "None")]),
    ("def f():\n    return None", "return.none", []),
    ("def f() -> None:\n    return g()", "return.none", []),
    (
        "def f():\n    return a < b",
        "return.bool",
        [("a < b", "False"), ("a < b", "True")],
    ),
    (
        "def f() -> bool:\n    return g()",
        "return.bool",
        [("g()", "False"), ("g()", "True")],
    ),
    ("def f():\n    return g()", "return.bool", []),
    ("def f() -> str:\n    return g()", "return.empty", [("g()", '""')]),
    ("def f() -> list[int]:\n    return g()", "return.empty", [("g()", "[]")]),
    ("def f() -> int:\n    return 0", "return.empty", []),
    ("x = min(a, b)", "call.minmax", [("min", "max")]),
    ("x = max(a, b)", "call.minmax", [("max", "min")]),
    ("def min(a, b):\n    pass\nx = min(1, 2)", "call.minmax", []),
    ("x = any(a)", "call.anyall", [("any", "all")]),
    ("x = s.lstrip()", "call.method_swap", [("lstrip", "rstrip")]),
    ("x = s.startswith('a')", "call.method_swap", [("startswith", "endswith")]),
    ("audit(x)", "stmt.call_remove", [("audit(x)", "pass")]),
    ("audit(\n    x,\n)", "stmt.call_remove", [("audit(\n    x,\n)", "pass\n\n")]),
    ("a(); b()", "stmt.call_remove", []),
    (
        "class A(B):\n    def __init__(self):\n        super().__init__()",
        "stmt.super_remove",
        [("super().__init__()", "pass")],
    ),
    (
        "class A(B):\n    def __init__(self):\n        super().__init__()",
        "stmt.call_remove",
        [],
    ),
    (
        "def f():\n    raise ValueError(1)",
        "stmt.raise_remove",
        [("raise ValueError(1)", "pass")],
    ),
    ("def f():\n    raise NotImplementedError", "stmt.raise_remove", []),
    ("try:\n    pass\nexcept E:\n    raise", "stmt.raise_remove", []),
    ("x = [1, 2]", "collection.empty", [("[1, 2]", "[]")]),
    ("x = (1, 2)", "collection.empty", [("(1, 2)", "()")]),
    ("x = {1: 2}", "collection.empty", [("{1: 2}", "{}")]),
    ("x = {1, 2}", "collection.empty", [("{1, 2}", "set()")]),
    ("x = []", "collection.empty", []),
    ("a, b = c", "collection.empty", []),
    ("x = d[1, 2]", "collection.empty", []),
    ("x = lambda v: v + 1", "lambda.none", [("v + 1", "None")]),
    ("x = f()", "assign.none", [("f()", "None")]),
    ("x: int = f()", "assign.none", [("f()", "None")]),
    ("x = None", "assign.none", []),
    ("a, b = f()", "assign.none", []),
    (
        "def f(n=10, *, flag=True, name='x'):\n    pass",
        "arg.default",
        [
            ("10", "11"),
            ("True", "False"),
            ("'x'", '""'),
        ],
    ),
    ("def f(n=10):\n    pass", "num.shift", []),
    ("for i in x:\n    break", "loop.break_continue", [("break", "continue")]),
    ("for i in x:\n    continue", "loop.break_continue", [("continue", "break")]),
    ("@cache\ndef f():\n    pass", "decorator.remove", [("@cache", "")]),
]


@pytest.mark.parametrize("source, operator, expected", CASES)
def test_operator(
    tmp_path: Path, source: str, operator: str, expected: list[tuple[str, str]]
) -> None:
    found = generate(tmp_path, source)
    assert not found.error
    assert changes(found, operator) == expected


def test_every_operator_has_a_case() -> None:
    covered = {operator for _, operator, expected in CASES if expected}
    covered.add("fstring.expr")
    assert covered == set(OPERATORS)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="needs PEP 701 tokens")
def test_fstring_fields_are_mutated(tmp_path: Path) -> None:
    found = generate(tmp_path, "x = f'{name}: {count + 1}'")
    assert changes(found, "fstring.expr") == [("name", "''"), ("count + 1", "''")]
    assert changes(found, "arith.add_sub") == [("+", "-")]


def test_every_mutant_compiles_or_is_marked(tmp_path: Path) -> None:
    source = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    found = generate(tmp_path, source)
    assert len(found.mutants) > 30
    for mutant in found.mutants:
        if not mutant.runnable:
            continue
        data = (tmp_path / "module.py").read_bytes()
        mutated = (
            data[: mutant.splice.start]
            + mutant.splice.replacement
            + data[mutant.splice.end :]
        )
        compile(mutated, "module.py", "exec")


def test_same_bytecode_is_reported_once(tmp_path: Path) -> None:
    # Flipping the literal and "return a constant boolean" produce the same
    # code here; the second one is a duplicate, not a second mutant.
    found = generate(tmp_path, "def f():\n    return True\n")
    flipped = [m for m in found.mutants if m.replacement == "False"]
    assert [m.pre_status for m in flipped] == [None, Status.DUPLICATE]
    assert flipped[1].duplicate_of == flipped[0].id


def test_unchanged_bytecode_is_equivalent(tmp_path: Path) -> None:
    # `0x10 + 1` written as `17` would differ, but a number shifted to itself
    # cannot happen; a statement removed where it was already `pass` can.
    found = generate(tmp_path, "def f(a):\n    return a\n\n\nx = (1, 2)[0:]\n")
    assert Status.INVALID not in {m.pre_status for m in found.mutants}
    found = generate(tmp_path, "def f(n):\n    while n:\n        continue\n")
    swapped = [m for m in found.mutants if m.operator == "loop.break_continue"]
    assert swapped and all(m.runnable for m in swapped)


def test_ids_survive_line_shifts(tmp_path: Path) -> None:
    first = generate(tmp_path, "def f(a, b):\n    return a + b\n")
    second = generate(tmp_path, "import os\n\n\ndef f(a, b):\n    return a + b\n")
    assert {m.id for m in first.mutants} == {m.id for m in second.mutants}
    lines = {m.span.line for m in second.mutants}
    assert lines == {5}


def test_static_classification(tmp_path: Path) -> None:
    source = (
        "LIMIT = 1 + 2\n"
        "class A:\n"
        "    size = 3 + 4\n"
        "    def m(self, n=5 + 6):\n"
        "        return n + 7\n"
        "def g():\n"
        "    class Local:\n"
        "        inner = 8 + 9\n"
        "    return lambda: 10 + 11\n"
    )
    found = generate(tmp_path, source)
    static = {
        mutant.span.line: mutant.static
        for mutant in found.mutants
        if mutant.operator == "arith.add_sub"
    }
    assert static == {1: True, 3: True, 4: True, 5: False, 8: False, 9: False}


def test_memoised_classification(tmp_path: Path) -> None:
    source = (
        "import functools\n"
        "@functools.lru_cache(maxsize=None)\n"
        "def a(n):\n"
        "    return n + 1\n"
        "@functools.cache\n"
        "def b(n, k=2 + 3):\n"
        "    def inner():\n"
        "        return n + 4\n"
        "    return inner()\n"
        "class C:\n"
        "    @functools.cached_property\n"
        "    def c(self):\n"
        "        return 5 + 6\n"
        "def d(n):\n"
        "    return n + 7\n"
    )
    found = generate(tmp_path, source)
    memoised = {
        mutant.span.line: mutant.memoised
        for mutant in found.mutants
        if mutant.operator == "arith.add_sub"
    }
    # The default value is evaluated once at definition, not per call.
    assert memoised == {4: True, 6: False, 8: True, 13: True, 15: False}


def test_levels_nest() -> None:
    core, extended, everything = (operators_at(level) for level in Level)
    assert core < extended < everything == frozenset(OPERATORS)
    assert "cmp.negate" in core and "str.empty" not in core
    assert expand_operators(["cmp.*"], "x") == {
        "cmp.boundary",
        "cmp.negate",
        "cmp.identity",
        "cmp.membership",
    }
    enabled = resolve_operators(Level.CORE, ["str.empty"], ["cmp.*"], "x")
    assert "str.empty" in enabled and "cmp.negate" not in enabled
