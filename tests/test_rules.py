"""Built-in suppression rules and inline directives."""

from __future__ import annotations

from pathlib import Path

import pytest

from pytestmut.mutation.model import Status
from tests.conftest import generate, suppressed

EXTENDED = {"operators": {"level": "all"}}

# (source, reason code, operators expected to be suppressed under that code)
RULES = [
    ('def f():\n    """Doc."""\n', "docstring", {"str.empty"}),
    (
        "def f(x: 'int | None' = None) -> 'list[int]':\n    pass\n",
        "annotation",
        {"str.empty"},
    ),
    ("x: Optional[1 + 2] = None\n", "annotation", {"arith.add_sub", "num.shift"}),
    ("T = TypeVar('T', bound='Base')\n", "annotation", {"str.empty"}),
    (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    x = 1 + 2\n",
        "type-checking",
        {"arith.add_sub", "num.shift", "assign.none", "cond.negate", "cond.const"},
    ),
    (
        "__all__ = ['a', 'b']\n",
        "module-metadata",
        {"str.empty", "collection.empty", "assign.none"},
    ),
    ("__version__ = '1.0'\n", "module-metadata", {"str.empty", "assign.none"}),
    (
        "if __name__ == '__main__':\n    main(1)\n",
        "main-guard",
        {"cmp.negate", "str.empty", "stmt.call_remove", "num.shift", "cond.const"},
    ),
    (
        "import sys\nif sys.version_info >= (3, 11):\n    pass\n",
        "platform-check",
        {"cmp.boundary", "cmp.negate", "num.shift", "collection.empty", "cond.const"},
    ),
    (
        (
            "import logging\n"
            "logger = logging.getLogger()\n"
            "def f(n):\n"
            "    logger.info('n=%s', n + 1)\n"
        ),
        "logging",
        {"stmt.call_remove", "str.empty", "arith.add_sub", "num.shift"},
    ),
    (
        "def f(n):\n    print('value', n * 2)\n",
        "logging",
        {"stmt.call_remove", "str.empty", "arith.mul_div", "num.shift"},
    ),
    (
        "def f(self, n):\n    self._log.warning('n=%s', n)\n",
        "logging",
        {"stmt.call_remove", "str.empty"},
    ),
    (
        "def f(n):\n    raise ValueError('bad ' + str(n))\n",
        "exception-message",
        {"str.empty"},
    ),
    (
        "def f(n):\n    assert n > 0, 'positive'\n",
        "assert",
        {"cmp.boundary", "cmp.negate", "num.shift", "str.empty"},
    ),
    (
        "def f():\n    connect(timeout=30, retries=3, max_size=10)\n",
        "tunable",
        {"num.shift"},
    ),
    ("import time\ndef f():\n    time.sleep(5)\n", "tunable", {"num.shift"}),
    ("def f(timeout=30, verbose=True):\n    pass\n", "tunable", {"arg.default"}),
    (
        (
            "import shutil, os\n"
            "def f(base, n):\n"
            "    shutil.rmtree(os.path.join(base, 'tmp' + str(n + 1)))\n"
        ),
        "hazard",
        {"str.empty", "arith.add_sub", "num.shift"},
    ),
    (
        (
            "from subprocess import run as launch\n"
            "def f(n):\n"
            "    launch(['cmd', n + 1], check=True)\n"
        ),
        "hazard",
        {"str.empty", "arith.add_sub", "num.shift", "collection.empty", "bool.literal"},
    ),
    (
        (
            "def f(cursor, n):\n"
            "    cursor.execute('DELETE FROM t WHERE id < ?', [n + 1])\n"
        ),
        "hazard",
        {"str.empty", "arith.add_sub", "num.shift", "collection.empty"},
    ),
    (
        (
            "import shutil\n"
            "def f(path, dry_run):\n"
            "    if not dry_run:\n"
            "        shutil.rmtree(path)\n"
        ),
        "hazard-guard",
        {"unary.not", "cond.const"},
    ),
    (
        (
            "from pathlib import Path\n"
            "def f(p, keep, n):\n"
            "    if n > 3 and not keep:\n"
            "        Path(p).unlink()\n"
        ),
        "hazard-guard",
        {
            "cmp.boundary",
            "cmp.negate",
            "num.shift",
            "bool.and_or",
            "unary.not",
            "cond.negate",
            "cond.const",
        },
    ),
    (
        "def f(handle, pool):\n    handle.close()\n    pool.shutdown()\n",
        "cleanup-call",
        {"stmt.call_remove"},
    ),
    (
        "@retry(times=3 + 1)\ndef f():\n    pass\n",
        "decorator",
        {"arith.add_sub", "num.shift"},
    ),
    (
        (
            "class A:\n"
            "    def __repr__(self):\n"
            "        return 'A(' + str(self.x + 1) + ')'\n"
        ),
        "repr",
        {"return.none", "arith.add_sub", "str.empty", "num.shift"},
    ),
    (
        (
            "def f(n):\n"
            "    if n < 0:  # pragma: no cover\n"
            "        return n + 1\n"
            "    return n\n"
        ),
        "no-cover",
        {
            "cmp.boundary",
            "cmp.negate",
            "num.shift",
            "return.none",
            "arith.add_sub",
            "cond.const",
        },
    ),
]


@pytest.mark.parametrize("source, reason, operators", RULES)
def test_builtin_rule(
    tmp_path: Path, source: str, reason: str, operators: set[str]
) -> None:
    found = generate(tmp_path, source, EXTENDED)
    assert not found.error
    grouped = suppressed(found)
    assert reason in grouped, sorted(grouped)
    assert {mutant.operator for mutant in grouped[reason]} == operators


def test_rules_leave_ordinary_code_alone(tmp_path: Path) -> None:
    source = (
        "def price(net, vat, items):\n"
        "    total = net + net * vat\n"
        "    if total > 100 and items:\n"
        "        return total - 5\n"
        "    return total\n"
    )
    found = generate(tmp_path, source)
    assert not suppressed(found)
    assert all(mutant.runnable for mutant in found.mutants)


def test_a_rule_can_be_switched_off(tmp_path: Path) -> None:
    source = (
        "import logging\n"
        "log = logging.getLogger()\n"
        "def f(n):\n"
        "    log.info('x', n + 1)\n"
    )
    on = generate(tmp_path, source, EXTENDED)
    off = generate(
        tmp_path,
        source,
        {"operators": {"level": "all"}, "suppress": {"builtin_disable": ["logging"]}},
    )
    assert "logging" in suppressed(on)
    assert "logging" not in suppressed(off)
    assert any(m.operator == "arith.add_sub" and m.runnable for m in off.mutants)


def test_hazard_list_can_be_extended(tmp_path: Path) -> None:
    source = "def f(bucket, n):\n    bucket.purge(n + 1)\n"
    plain = generate(tmp_path, source, EXTENDED)
    extended = generate(
        tmp_path,
        source,
        {"operators": {"level": "all"}, "suppress": {"hazardous_calls": ["*.purge"]}},
    )
    assert "hazard" not in suppressed(plain)
    assert {m.operator for m in suppressed(extended)["hazard"]} >= {"arith.add_sub"}


def test_config_lines_and_symbols(tmp_path: Path) -> None:
    source = (
        "def keep(a):\n"
        "    return a + 1\n"
        "def skip_me(a):\n"
        "    return a + 2\n"
        "class Legacy:\n"
        "    def run(self, a):\n"
        "        return a + 3  # legacy\n"
    )
    found = generate(
        tmp_path,
        source,
        {
            "suppress": {
                "lines": ["# legacy$"],
                "symbols": ["skip_*", "module.py::Nope"],
            }
        },
    )
    grouped = suppressed(found)
    assert {m.span.line for m in grouped["config-line"]} == {7}
    assert {m.scope for m in grouped["config-symbol"]} == {"skip_me"}
    assert any(m.scope == "keep" and m.runnable for m in found.mutants)


def test_inline_skip_forms(tmp_path: Path) -> None:
    source = (
        "def a(x):\n"
        "    return x + 1  # pytestmut: skip\n"
        "def b(x):\n"
        "    return x + 1  # pytestmut: skip[num.*] -- constant is arbitrary\n"
        "def c(x):  # pytestmut: skip -- generated code\n"
        "    y = x + 1\n"
        "    return y * 2\n"
        "# pytestmut: off\n"
        "def d(x):\n"
        "    return x + 1\n"
        "# pytestmut: on\n"
        "def e(x):\n"
        "    return x + 1  # pragma: no mutate\n"
        "def f(x):\n"
        "    return x + 1\n"
    )
    found = generate(tmp_path, source)
    by_scope: dict[str, set[str]] = {}
    for mutant in found.mutants:
        if mutant.runnable:
            by_scope.setdefault(mutant.scope, set()).add(mutant.operator)
    assert by_scope == {
        "b": {"return.none", "arith.add_sub"},
        "f": {"return.none", "arith.add_sub", "num.shift"},
    }
    assert not found.issues


def test_directive_problems_are_fatal(tmp_path: Path) -> None:
    source = (
        "def a(x):\n"
        "    return x + 1  # pytestmut: skip[no.such.operator]\n"
        "def b(x):  # pytestmut: policy=missing\n"
        "    return x + 1\n"
        "x = 1  # pytestmut: frobnicate\n"
        "# pytestmut: on\n"
    )
    found = generate(tmp_path, source)
    messages = [issue.message for issue in found.issues if issue.fatal]
    assert any("no.such.operator" in message for message in messages)
    assert any("policy=missing" in message for message in messages)
    assert any("unrecognised directive" in message for message in messages)
    assert any("without a matching 'off'" in message for message in messages)


def test_unused_skip_is_reported(tmp_path: Path) -> None:
    found = generate(tmp_path, "import os  # pytestmut: skip\n")
    assert [issue.message for issue in found.issues] == [
        "skip comment suppresses no mutant"
    ]
    assert not found.issues[0].fatal
    strict = generate(
        tmp_path,
        "import os  # pytestmut: skip\n",
        {"suppress": {"report_unused": "error"}},
    )
    assert strict.issues[0].fatal


def test_static_mutants_can_be_skipped(tmp_path: Path) -> None:
    source = "LIMIT = 10\ndef f(a):\n    return a + LIMIT\n"
    found = generate(tmp_path, source, {"run": {"static_mutants": "skip"}})
    grouped = suppressed(found)
    assert {m.span.line for m in grouped["static-skip"]} == {1}
    assert any(m.runnable and m.span.line == 3 for m in found.mutants)


def test_empty_scopes_are_listed(tmp_path: Path) -> None:
    source = (
        "def stub():\n"
        "    raise NotImplementedError\n"
        "def real(a):\n"
        "    return a + 1\n"
        "def logs(a):\n"
        "    print(a)\n"
    )
    found = generate(tmp_path, source)
    assert found.empty_scopes == ("stub", "logs")
    statuses = {m.pre_status for m in found.mutants if m.scope == "logs"}
    assert statuses == {Status.SUPPRESSED}
