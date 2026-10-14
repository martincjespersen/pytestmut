"""Shared helpers: generated fixture projects and a way to run the tool on them."""

from __future__ import annotations

import os
import subprocess
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pytestmut.cli import app
from pytestmut.config.loader import parse_config
from pytestmut.config.model import Config
from pytestmut.mutation.generator import FileMutants, Generator
from pytestmut.mutation.model import Mutant, Status
from pytestmut.policy.matcher import PolicyMatcher
from pytestmut.report.model import RunResult, from_json
from pytestmut.source.discovery import TargetFile

ProjectFactory = Callable[[Mapping[str, str]], Path]


@pytest.fixture
def make_project(tmp_path: Path) -> ProjectFactory:
    """Create a project directory from a mapping of relative path to content."""

    def build(files: Mapping[str, str]) -> Path:
        root = tmp_path / "project"
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content), encoding="utf-8", newline="\n")
        return root.resolve()

    return build


@dataclass(frozen=True)
class ToolRun:
    """Outcome of running the command line tool on a project.

    Attributes:
        exit_code: Process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.
        root: The project that was tested.
        result_text: Content of the result file right after the run.
    """

    exit_code: int
    stdout: str
    stderr: str
    root: Path
    result_text: str = ""

    @property
    def result(self) -> RunResult:
        """The result the run wrote."""
        return from_json(self.result_text)

    def status(
        self,
        line: int,
        operator: str,
        replacement: str | None = None,
        path: str | None = None,
    ) -> str:
        """Final status of the one mutant matching the description."""
        result = self.result
        found = [
            mutant
            for mutant in result.mutants
            if mutant.span.line == line
            and mutant.operator == operator
            and (replacement is None or mutant.replacement == replacement)
            and (path is None or mutant.path == path)
        ]
        assert len(found) == 1, [
            (m.span.line, m.operator, m.replacement) for m in found
        ]
        return result.status_of(found[0]).value

    def statuses(self) -> dict[str, int]:
        """Number of mutants per status."""
        return dict(self.result.counts())


def run_tool(root: Path, *args: str, env: Mapping[str, str] | None = None) -> ToolRun:
    """Run the ``pytestmut`` command line in a project.

    The command runs in this process, with the working directory and the
    environment switched for the duration, so the orchestrator is measured by
    coverage. The test processes it starts are real subprocesses either way.

    Arguments:
        root: Project directory.
        args: Command line arguments after ``pytestmut``.
        env: Extra environment variables.

    Returns:
        The outcome of the run.
    """
    saved_directory = os.getcwd()
    saved_environment = dict(os.environ)
    os.chdir(root)
    os.environ.update(env or {})
    try:
        outcome = CliRunner().invoke(app, list(args))
    finally:
        os.chdir(saved_directory)
        os.environ.clear()
        os.environ.update(saved_environment)
    try:
        stderr = outcome.stderr
    except ValueError:
        stderr = ""
    if outcome.exception is not None and not isinstance(outcome.exception, SystemExit):
        raise outcome.exception
    results = root / ".pytestmut" / "results.json"
    text = results.read_text(encoding="utf-8") if results.is_file() else ""
    return ToolRun(outcome.exit_code, outcome.stdout, stderr, root, text)


def git(root: Path, *args: str) -> str:
    """Run git in a project with a fixed identity."""
    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def generate(
    tmp_path: Path, source: str, settings: Mapping[str, object] | None = None
) -> FileMutants:
    """Generate the mutants of one module written to a temp directory."""
    path = tmp_path / "module.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8", newline="\n")
    config: Config = parse_config(
        dict(settings or {"operators": {"level": "all"}}), tmp_path, None, "test"
    )
    generator = Generator(config, PolicyMatcher(config))
    return generator.generate(TargetFile(path=path, relpath="module.py"))


def changes(found: FileMutants, operator: str) -> list[tuple[str, str]]:
    """``(original, replacement)`` of every runnable mutant of an operator."""
    return [
        (mutant.original, mutant.replacement)
        for mutant in found.mutants
        if mutant.operator == operator and mutant.runnable
    ]


def suppressed(found: FileMutants) -> dict[str, list[Mutant]]:
    """Suppressed candidates grouped by reason code."""
    grouped: dict[str, list[Mutant]] = {}
    for mutant in found.mutants:
        if mutant.pre_status is Status.SUPPRESSED:
            grouped.setdefault(mutant.reason, []).append(mutant)
    return grouped
