"""End-to-end: verdicts on small projects, one gotcha per test."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.conftest import ProjectFactory, run_tool

pytestmark = pytest.mark.e2e

PRICING = """\
    TAX_RATE = 0.25


    def total(price, quantity):
        if quantity < 0:
            raise ValueError("negative")
        return price * quantity


    def with_tax(amount):
        return amount + amount * TAX_RATE


    def untested(value):
        return value + 1
"""
PRICING_TESTS = """\
    import pytest
    from shop.pricing import total, with_tax


    def test_total():
        assert total(2.0, 3) == 6.0


    def test_negative():
        with pytest.raises(ValueError):
            total(1.0, -1)


    def test_with_tax():
        assert with_tax(100.0) == 125.0
"""


def _flat(make_project: ProjectFactory, extra: str = "") -> Path:
    return make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["shop"]\n' + extra,
            "shop/__init__.py": "",
            "shop/pricing.py": PRICING,
            "tests/test_pricing.py": PRICING_TESTS,
        }
    )


def test_flat_layout_gives_expected_verdicts(make_project: ProjectFactory) -> None:
    root = _flat(make_project)
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    assert run.status(7, "arith.mul_div") == "killed"
    assert run.status(11, "arith.add_sub") == "killed"
    assert run.status(5, "cmp.negate") == "killed"
    # Nothing checks quantity == 0, so the boundary really is untested.
    assert run.status(5, "cmp.boundary") == "survived"
    assert run.status(15, "arith.add_sub") == "no_coverage"
    # The module-level constant is evaluated at import and still mutated.
    assert run.status(1, "num.shift") == "killed"


def test_selection_modes_agree(make_project: ProjectFactory) -> None:
    root = _flat(make_project)
    selected = run_tool(root, "run", "--no-cache")
    everything = run_tool(root, "run", "--no-cache", "--selection", "all")
    by_id_selected = {
        m.id: selected.result.status_of(m).value for m in selected.result.mutants
    }
    by_id_all = {
        m.id: everything.result.status_of(m).value for m in everything.result.mutants
    }
    assert by_id_selected.keys() == by_id_all.keys()
    for identifier, status in by_id_selected.items():
        # Without a line map an unreached mutant can only be seen surviving.
        expected = "survived" if status == "no_coverage" else status
        assert by_id_all[identifier] == expected


def test_serial_and_parallel_agree(make_project: ProjectFactory) -> None:
    root = _flat(make_project)
    serial = run_tool(root, "run", "--no-cache", "--workers", "1")
    parallel = run_tool(root, "run", "--no-cache", "--workers", "4")
    assert serial.statuses() == parallel.statuses()


def test_nothing_is_written_into_the_project(make_project: ProjectFactory) -> None:
    root = _flat(make_project)
    before = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    run_tool(root, "run", "--no-cache")
    after = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and ".pytestmut" not in path.parts
    }
    assert after == before


def test_src_layout(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytest.ini_options]
                pythonpath = ["src"]

                [tool.pytestmut]
                paths = ["src"]
            """,
            "src/shop/__init__.py": "",
            "src/shop/pricing.py": PRICING,
            "tests/test_pricing.py": PRICING_TESTS,
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    assert run.statuses()["killed"] >= 6
    assert "error" not in run.statuses()


def test_class_bodies_and_defaults_are_mutated(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytestmut]
                paths = ["conf.py"]

                [tool.pytestmut.operators]
                level = "extended"
            """,
            "conf.py": """\
                class Limits:
                    retries_allowed = 3
                    enabled = True


                def scale(value, factor=2):
                    return value * factor
            """,
            "tests/test_conf.py": """\
                from conf import Limits, scale


                def test_limits():
                    assert Limits.retries_allowed == 3
                    assert Limits.enabled is True


                def test_scale():
                    assert scale(4) == 8
            """,
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    assert run.status(2, "num.shift") == "killed"
    assert run.status(3, "bool.literal") == "killed"
    assert run.status(6, "arg.default") == "killed"


def test_mutant_is_active_in_child_processes(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["worker.py"]\n',
            "worker.py": """\
                def double(value):
                    return value * 2


                if __name__ == "__main__":
                    print(double(21))
            """,
            "tests/test_worker.py": """\
                import multiprocessing
                import subprocess
                import sys

                CODE = "import worker; print(worker.double(21))"


                def test_subprocess():
                    out = subprocess.run(
                        [sys.executable, "-c", CODE],
                        capture_output=True, text=True, check=True,
                    ).stdout
                    assert out.strip() == "42"


                def test_spawned_process():
                    import worker

                    context = multiprocessing.get_context("spawn")
                    with context.Pool(1) as pool:
                        assert pool.apply(worker.double, (4,)) == 8
            """,
        }
    )
    run = run_tool(root, "run", "--no-cache", "--selection", "all")
    assert run.exit_code == 0, run.stderr
    assert run.status(2, "arith.mul_div") == "killed"
    assert run.status(2, "num.shift") == "killed"


def test_encodings_and_line_endings_survive(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["texts"]\n',
            "texts/__init__.py": "",
            "tests/test_texts.py": """\
                from texts import bom, crlf, latin


                def test_all():
                    assert crlf.add(2, 3) == 5
                    assert bom.add(2, 3) == 5
                    assert latin.add(2, 3) == 5
                    assert latin.NAME == "caf\\xe9"
            """,
        }
    )
    body = "def add(a, b):\n    return a + b\n"
    crlf = root / "texts" / "crlf.py"
    crlf.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    bom = root / "texts" / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    latin = root / "texts" / "latin.py"
    latin.write_bytes(
        b"# -*- coding: latin-1 -*-\nNAME = 'caf\xe9'\n" + body.encode("latin-1")
    )
    originals = {path: path.read_bytes() for path in (crlf, bom, latin)}
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    counts = run.statuses()
    assert "error" not in counts and "invalid" not in counts
    killed = [
        mutant.path
        for mutant in run.result.mutants
        if mutant.operator == "arith.add_sub"
        and run.result.status_of(mutant).value == "killed"
    ]
    assert sorted(killed) == ["texts/bom.py", "texts/crlf.py", "texts/latin.py"]
    assert {path: path.read_bytes() for path in originals} == originals


def test_infinite_loop_is_a_confirmed_timeout(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytestmut]
                paths = ["loop.py"]

                [tool.pytestmut.timeout]
                floor_seconds = 2
                grace_seconds = 1
                factor = 1.5
            """,
            "loop.py": """\
                def countdown(n):
                    steps = 0
                    while n > 0:
                        n -= 1
                        steps += 1
                    return steps
            """,
            "tests/test_loop.py": """\
                from loop import countdown


                def test_countdown():
                    assert countdown(3) == 3
            """,
        }
    )
    run = run_tool(root, "run", "--no-cache", "--workers", "2")
    assert run.exit_code == 0, run.stderr + run.stdout
    assert run.status(4, "aug.swap") == "timeout"
    assert "error" not in run.statuses()


def test_async_and_parametrized_tests(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytest.ini_options]
                asyncio_mode = "auto"

                [tool.pytestmut]
                paths = ["aio.py"]
            """,
            "aio.py": """\
                async def fetch(value):
                    return value + 1


                def sign(value):
                    if value < 0:
                        return -1
                    return 1
            """,
            "tests/test_aio.py": """\
                import pytest
                from aio import fetch, sign


                async def test_fetch():
                    assert await fetch(1) == 2


                @pytest.mark.parametrize("value, expected", [(-5, -1), (0, 1), (7, 1)])
                def test_sign(value, expected):
                    assert sign(value) == expected
            """,
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr + run.stdout
    assert run.status(2, "arith.add_sub") == "killed"
    assert run.status(6, "cmp.boundary") == "killed"
    assert "error" not in run.statuses()


def test_mutants_in_memoised_functions_face_every_test(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["calc.py"]\n',
            "calc.py": """\
                import functools


                @functools.lru_cache(maxsize=None)
                def square(n):
                    return n * n
            """,
            "tests/test_calc.py": """\
                from calc import square


                def test_first_call_only_warms_the_cache():
                    square(3)


                def test_second_call_reads_the_cache():
                    assert square(3) == 9
            """,
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    # Only the first test executes the body; the second gets the cached
    # value and is the one that notices a wrong result.
    assert run.status(6, "arith.mul_div") == "killed"
    killed = [
        result
        for result in run.result.results.values()
        if result.status.value == "killed"
    ]
    assert any(
        r.killing_test and r.killing_test.endswith("reads_the_cache") for r in killed
    )


def test_selection_misses_are_caught_and_corrected(
    make_project: ProjectFactory,
) -> None:
    # A hand-written cache: nothing marks it, so line-based selection misses
    # the test that depends on it. The survivor check must catch that.
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["calc.py"]\n',
            "calc.py": """\
                _CACHE = {}


                def square(n):
                    if n not in _CACHE:
                        _CACHE[n] = n * n
                    return _CACHE[n]
            """,
            "tests/test_calc.py": """\
                from calc import square


                def test_first_call_only_warms_the_cache():
                    square(3)


                def test_second_call_reads_the_cache():
                    assert square(3) == 9
            """,
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stdout + run.stderr
    assert run.status(6, "arith.mul_div") == "killed"
    assert "survived" not in run.statuses()
    assert any("test selection missed 1 kill" in w for w in run.result.warnings)
