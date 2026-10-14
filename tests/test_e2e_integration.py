"""End-to-end: the pytest flag and a real pre-commit hook run."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.conftest import ProjectFactory, git, run_tool

pytestmark = pytest.mark.e2e

CALC = "def add(a, b):\n    return a + b\n\n\ndef double(a):\n    return a * 2\n"
TESTS = textwrap.dedent("""\
    from calc import add, double


    def test_add():
        assert add(2, 3) == 5


    def test_double():
        assert double(4) == 8
    """)


def _pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_pytest_mutate_flag(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["calc.py"]\n',
            "calc.py": CALC,
            "tests/test_calc.py": TESTS,
        }
    )
    plain = _pytest(root, "-q")
    assert plain.returncode == 0 and "2 passed" in plain.stdout
    mutated = _pytest(root, "--mutate", "--mutate-min-score", "100", "tests")
    assert "Policy" in mutated.stdout and "killed" in mutated.stdout
    assert mutated.returncode == 0, mutated.stdout + mutated.stderr
    assert (root / ".pytestmut" / "results.json").is_file()
    # The gate is real: a threshold nobody can meet fails the pytest run.
    (root / "tests" / "test_calc.py").write_text(
        TESTS.replace("assert double(4) == 8", "double(4)"), encoding="utf-8"
    )
    failing = _pytest(root, "--mutate", "--mutate-min-score", "100")
    assert failing.returncode == 1
    assert "below the minimum 100%" in failing.stdout


def test_mutate_in_addopts_does_not_recurse(make_project: ProjectFactory) -> None:
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytest.ini_options]
                addopts = "--mutate --mutate-level core"

                [tool.pytestmut]
                paths = ["calc.py"]
            """,
            "calc.py": CALC,
            "tests/test_calc.py": TESTS,
        }
    )
    run = _pytest(root)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "killed" in run.stdout
    # The command line tool sees the same project and must not loop either.
    assert run_tool(root, "run", "--no-cache").exit_code == 0


@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="needs pre-commit")
def test_pre_commit_hook_with_staged_and_unstaged_changes(
    make_project: ProjectFactory,
) -> None:
    executable = Path(sys.executable).as_posix()
    root = make_project(
        {
            "pyproject.toml": """\
                [tool.pytestmut]
                paths = ["calc.py"]

                [tool.pytestmut.thresholds]
                max_survivors = 0
                max_no_coverage = 0
            """,
            "calc.py": CALC,
            "tests/test_calc.py": TESTS,
            ".pre-commit-config.yaml": f"""\
                repos:
                  - repo: local
                    hooks:
                      - id: pytestmut
                        name: mutation testing
                        entry: {executable} -m pytestmut run --hook
                        language: system
                        pass_filenames: false
                        require_serial: true
                        types: [python]
            """,
        }
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env["PRE_COMMIT_HOME"] = str(root.parent / "pre-commit-home")

    def hook() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["pre-commit", "run", "pytestmut", "--verbose"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )

    calc = root / "calc.py"
    # Staged: a tested change. Unstaged: an untested one on another line.
    calc.write_text(CALC.replace("a + b", "b + a"), encoding="utf-8")
    git(root, "add", "calc.py")
    calc.write_text(
        CALC.replace("a + b", "b + a") + "\n\ndef untested(a):\n    return a - 1\n",
        encoding="utf-8",
    )
    passed = hook()
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert "staged changes" in passed.stdout
    assert "untested" not in passed.stdout
    # pre-commit stashed the unstaged edit while the hook ran; it is back.
    assert "def untested" in calc.read_text(encoding="utf-8")
    assert git(root, "status", "--porcelain").split() == ["MM", "calc.py"]
    # Now stage the untested function: nothing reaches it and the hook fails.
    git(root, "add", "calc.py")
    failed = hook()
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "untested" in failed.stdout and "FAIL" in failed.stdout
    # A commit that touches no Python is not held up.
    git(root, "commit", "-q", "-m", "wip", "--no-verify")
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    git(root, "add", "notes.txt")
    skipped = hook()
    assert skipped.returncode == 0


def test_pytest_flag_can_judge_selected_tests_only(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["calc.py"]\n',
            "calc.py": CALC,
            "tests/test_calc.py": TESTS,
        }
    )
    run = _pytest(root, "--mutate", "--mutate-reached", "tests/test_calc.py::test_add")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "code reached by the selected tests" in run.stdout
    assert "double" not in run.stdout
