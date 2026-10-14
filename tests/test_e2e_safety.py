"""End-to-end: situations where a naive tool reports wrong numbers or does harm."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.conftest import ProjectFactory, git, run_tool

pytestmark = pytest.mark.e2e

CALC = """\
def add(a, b):
    return a + b


def double(a):
    return a * 2
"""
CALC_TESTS = """\
from calc import add, double


def test_add():
    assert add(2, 3) == 5


def test_double():
    assert double(4) == 8
"""
CONFIG = '[tool.pytestmut]\npaths = ["calc.py"]\n'


def test_failing_baseline_stops_the_run(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS
            + "\n\ndef test_broken():\n    assert False\n",
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 3
    assert "test_broken" in run.stderr
    assert "baseline" in run.stderr


def test_no_tests_is_a_baseline_error(make_project: ProjectFactory) -> None:
    root = make_project({"pyproject.toml": CONFIG, "calc.py": CALC})
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 3
    assert "no tests were collected" in run.stderr


def test_quarantined_test_cannot_kill(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG
            + '\n[tool.pytestmut.run]\non_baseline_failure = "quarantine"\n',
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS
            + "\n\ndef test_broken():\n    assert False\n",
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    assert "tests/test_calc.py::test_broken" in run.result.baseline.quarantined
    assert run.status(2, "arith.add_sub") == "killed"
    assert "error" not in run.statuses()


def test_suite_that_is_not_parallel_safe_runs_serially(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS
            + """

import os


def test_needs_slot_zero():
    assert os.environ["PYTESTMUT_WORKER"] == "0"
""",
        }
    )
    run = run_tool(root, "run", "--no-cache", "--workers", "3")
    assert run.exit_code == 0, run.stderr
    assert run.result.baseline.workers == 1
    assert any("one at a time" in warning for warning in run.result.warnings)


def test_importing_another_copy_of_the_code_is_refused(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytest.ini_options]
                pythonpath = ["installed"]

                [tool.pytestmut]
                paths = ["src"]
            """,
            "src/shop/__init__.py": "",
            "src/shop/calc.py": CALC,
            "installed/shop/__init__.py": "",
            "installed/shop/calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS.replace("from calc", "from shop.calc"),
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 3
    assert "installed copy" in run.stderr
    assert "shop.calc" in run.stderr


def test_inherited_options_are_neutralised(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytest.ini_options]
                addopts = [
                    "-n", "2", "--cov=calc", "--cov-fail-under=100", "--lf",
                    "-p", "no:cacheprovider", "--junitxml=report.xml",
                ]
                filterwarnings = ["error"]

                [tool.pytestmut]
                paths = ["calc.py"]
            """,
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS,
        }
    )
    run = run_tool(root, "run", "--no-cache", env={"PYTEST_ADDOPTS": "--ff"})
    assert run.exit_code == 0, run.stderr
    assert run.status(2, "arith.add_sub") == "killed"
    assert "error" not in run.statuses()
    assert not (root / "report.xml").exists()
    assert not (root / ".coverage").exists()
    assert not (root / ".pytest_cache").exists()


def test_order_dependent_test_is_not_trusted_alone(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": CALC,
            "tests/test_calc.py": """\
                from calc import add, double

                STATE = {}


                def test_add():
                    STATE["ready"] = True
                    assert add(2, 3) == 5


                def test_double():
                    assert STATE["ready"]
                    assert double(4) == 8
            """,
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert "error" not in run.statuses()
    # test_double fails on its own, mutant or not, so it must not be credited.
    assert run.status(6, "arith.mul_div") != "killed"
    assert any("test_double" in warning for warning in run.result.warnings)
    everything = run_tool(root, "run", "--no-cache", "--selection", "all")
    assert everything.status(6, "arith.mul_div") == "killed"


def test_writes_to_tracked_files_are_reported(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": "def add(a, b):\n    return a + b\n",
            "snapshot.txt": "5\n",
            "tests/test_calc.py": """\
                from pathlib import Path

                from calc import add


                def test_add_updates_snapshot():
                    Path(__file__).parent.parent.joinpath("snapshot.txt").write_text(
                        f"{add(2, 3)}\\n"
                    )
            """,
        }
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    run = run_tool(root, "run", "--no-cache", "--selection", "all")
    assert run.exit_code == 4
    assert any("tracked files changed" in failure for failure in run.result.failures)


def test_unparseable_file_fails_the_run(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["calc.py", "broken.py"]\n',
            "calc.py": CALC,
            "broken.py": "def oops(:\n",
            "tests/test_calc.py": CALC_TESTS,
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 4
    assert any("broken.py" in failure for failure in run.result.failures)


def test_empty_scope_is_its_own_exit_code(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": '"""Only a docstring."""\n',
            "tests/test_calc.py": "def test_nothing():\n    pass\n",
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 5


def test_state_directory_without_marker_is_refused(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS,
            ".pytestmut/precious.txt": "do not delete",
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 2
    assert (root / ".pytestmut" / "precious.txt").read_text() == "do not delete"
    cleaned = run_tool(root, "clean")
    assert cleaned.exit_code == 2
    assert (root / ".pytestmut" / "precious.txt").exists()


def test_tests_listed_for_quarantine_are_tolerated(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG
            + '\n[tool.pytestmut.run]\nquarantine = ["*::test_known_*"]\n',
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS
            + "\n\ndef test_known_broken():\n    assert add(1, 1) == 3\n",
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    assert run.result.baseline.quarantined == {
        "tests/test_calc.py::test_known_broken": "listed in run.quarantine"
    }
    assert run.status(2, "arith.add_sub") == "killed"
    assert "error" not in run.statuses()


def test_memory_budget_bounds_the_workers(make_project: ProjectFactory) -> None:
    root = make_project(
        {"pyproject.toml": CONFIG, "calc.py": CALC, "tests/test_calc.py": CALC_TESTS}
    )
    # Each worker is allowed at least 512 MiB, so 1 GiB leaves room for two.
    run = run_tool(root, "run", "--no-cache", "--workers", "6", "--max-memory", "1GiB")
    assert run.exit_code == 0, run.stderr
    assert run.result.baseline.workers == 2
    assert "budget 1.0 GiB, limit 512 MiB per worker, 2 worker(s)" in run.stderr
    assert any("memory budget of 1.0 GiB" in w for w in run.result.warnings)
    assert run.status(2, "arith.add_sub") == "killed"
    unbounded = run_tool(
        root, "run", "--no-cache", "--workers", "3", "--max-memory", "off"
    )
    assert unbounded.result.baseline.workers == 3
    assert "budget unlimited" in unbounded.stderr


def test_suite_larger_than_the_budget_is_refused(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS
            + """

def test_hungry():
    import time

    blocks = [bytearray(16 * 1024 * 1024) for _ in range(40)]
    time.sleep(2)
    assert len(blocks) == 40
""",
        }
    )
    run = run_tool(root, "run", "--no-cache", "--max-memory", "200MiB")
    assert run.exit_code == 3
    assert "needs more memory than allowed" in run.stderr


def test_tests_get_temp_directories_outside_the_project(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": CONFIG,
            "calc.py": CALC,
            "tests/test_calc.py": CALC_TESTS
            + """

def test_tmp_path_is_not_inside_a_project(tmp_path):
    import tempfile
    from pathlib import Path

    for start in (tmp_path, Path(tempfile.gettempdir())):
        for directory in (start, *start.parents):
            assert not (directory / "pyproject.toml").exists(), directory
""",
        }
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    assert "error" not in run.statuses()


def test_random_order_from_addopts_is_neutralised(make_project: ProjectFactory) -> None:
    ordered_tests = "STATE = {}\n\n\ndef test_00_setup():\n    STATE['ready'] = True\n"
    for number in range(1, 10):
        ordered_tests += (
            f"\n\ndef test_{number:02d}_uses_setup():\n"
            "    from calc import add\n\n"
            "    assert STATE['ready'] and add(2, 3) == 5\n"
        )
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytest.ini_options]
                addopts = "--random-order-bucket=module --random-order-seed=7"

                [tool.pytestmut]
                paths = ["calc.py"]
            """,
            "calc.py": CALC,
            "tests/test_calc.py": ordered_tests,
        }
    )
    # The premise: with these options pytest itself runs the tests shuffled,
    # and this order-dependent suite fails.
    shuffled = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("PYTEST")},
    )
    assert shuffled.returncode == 1, shuffled.stdout
    run = run_tool(root, "run", "--no-cache", "--selection", "all")
    assert run.exit_code == 0, run.stderr
    assert run.status(2, "arith.add_sub") == "killed"
    assert "error" not in run.statuses()
