"""Command line interface for pytestmut."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from pytestmut._meta import CONFIG_TABLE, __version__
from pytestmut.config.loader import find_config, load_config, with_overrides
from pytestmut.config.model import Config
from pytestmut.exceptions import ConfigError, PytestmutError
from pytestmut.exec.environment import build_environment
from pytestmut.exec.jobs import MUTATE, Job, identify, write_job
from pytestmut.mutation.model import Mutant, Status
from pytestmut.mutation.registry import OPERATORS
from pytestmut.orchestrator import RunOptions
from pytestmut.report.model import RunResult, load_result
from pytestmut.report.render import render
from pytestmut.report.source import SourceCache
from pytestmut.session import run_and_report
from pytestmut.state.statedir import StateDir


def _examples(*lines: str) -> str:
    """Help text listing example command lines, one per paragraph."""
    return "Examples:\n\n" + "\n\n".join(f"  {line}" for line in lines)


app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Mutation testing for pytest: find the changes to your code that no "
    "test notices.",
    epilog="Documentation: docs/index.md in the pytestmut repository.",
)

STARTER_CONFIG = f"""\
[tool.{CONFIG_TABLE}]
paths = ["src"]                      # files, directories or globs to mutate
exclude = []                         # path globs never mutated

[tool.{CONFIG_TABLE}.operators]
level = "core"                       # "core" | "extended" | "all"
disable = []                         # e.g. ["num.shift", "str.*"]

[tool.{CONFIG_TABLE}.thresholds]
min_score = 80.0                     # percent; the run fails below this
max_survivors = -1                   # -1 = unlimited

[tool.{CONFIG_TABLE}.report]
formats = ["text", "html"]

# Stricter rules for code that must not break:
# [[tool.{CONFIG_TABLE}.policy]]
# name = "critical"
# paths = ["src/mypackage/billing/**"]
# level = "extended"
# min_score = 100.0
# max_survivors = 0
# max_no_coverage = 0
"""


def _fail(error: PytestmutError) -> typer.Exit:
    typer.echo(f"pytestmut: error: {error}", err=True)
    return typer.Exit(code=error.exit_code)


def _load(config: Optional[Path]) -> Config:
    return load_config(Path.cwd(), config)


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool, typer.Option("--version", help="Print the version and exit.")
    ] = False,
) -> None:
    """Mutation testing for pytest."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command(
    help="Mutate your code, run the tests against each change, and report "
    "what they missed.",
    epilog=_examples(
        "pytestmut run",
        "pytestmut run src/app/billing --report html",
        "pytestmut run --tests tests/test_billing.py",
        "pytestmut run --staged",
        "pytestmut run --diff main --max-memory 4GiB",
    ),
)
def run(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="Files, directories or globs to mutate."),
    ] = None,
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Config file to use.")
    ] = None,
    workers: Annotated[
        Optional[int], typer.Option("--workers", "-j", help="Parallel workers.")
    ] = None,
    level: Annotated[
        Optional[str], typer.Option(help="Operator level: core, extended or all.")
    ] = None,
    min_score: Annotated[
        Optional[float], typer.Option(help="Minimum score of the default policy.")
    ] = None,
    report: Annotated[
        Optional[list[str]],
        typer.Option("--report", "-r", help="text, markdown, html or json."),
    ] = None,
    selection: Annotated[
        Optional[str], typer.Option(help="Test selection: coverage or all.")
    ] = None,
    diff: Annotated[
        Optional[str], typer.Option(help="Only mutate lines changed against a ref.")
    ] = None,
    staged: Annotated[
        bool, typer.Option("--staged", help="Only mutate lines staged in git.")
    ] = False,
    hook: Annotated[
        bool, typer.Option("--hook", help="Run as a pre-commit hook.")
    ] = False,
    only: Annotated[
        Optional[list[str]],
        typer.Option(help="Restrict to mutant id prefixes or path globs."),
    ] = None,
    tests: Annotated[
        Optional[list[str]],
        typer.Option(
            "--tests",
            "-t",
            help="Only these tests (files, dirs or node ids); only code they reach "
            "is mutated.",
        ),
    ] = None,
    pytest_args: Annotated[
        Optional[str],
        typer.Option(help="Extra pytest arguments, e.g. \"-k tax -m 'not slow'\"."),
    ] = None,
    max_memory: Annotated[
        Optional[str],
        typer.Option(help="Total memory for all test processes, e.g. 4GiB."),
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Ignore and do not write the cache.")
    ] = False,
    keep_jobs: Annotated[
        bool, typer.Option("--keep-jobs", help="Keep per-job directories.")
    ] = False,
    show_suppressed: Annotated[
        bool, typer.Option("--show-suppressed", help="List suppressed candidates.")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="No progress output.")
    ] = False,
) -> None:
    """Mutate the code, run the tests against each mutant, and report.

    Arguments:
        paths: Files, directories or globs to mutate; overrides the config.
        config: Config file to use instead of searching for one.
        workers: Number of mutants tested at once.
        level: Operator level of the default policy.
        min_score: Minimum mutation score of the default policy.
        report: Report formats; ``text`` goes to the terminal, the others
            to files.
        selection: ``coverage`` runs only the tests that reach a mutant.
        diff: Git ref; only lines changed against it are mutated.
        staged: Only lines staged in the git index are mutated.
        hook: Pre-commit mode: changed lines only, capped and time-boxed.
        only: Mutant id prefixes or path globs to restrict the run to.
        tests: Tests to judge, as pytest paths or node ids. They replace the
            configured pytest arguments, and only mutants in code these tests
            execute are kept, so the result describes these tests alone.
        pytest_args: Extra pytest arguments as one string, added after the
            tests; they also replace the configured arguments.
        max_memory: Memory all test processes together may use; the worker
            count is reduced to fit. Defaults to half of physical memory.
        no_cache: Run every mutant, reusing nothing.
        keep_jobs: Keep per-job directories under the state directory.
        show_suppressed: List suppressed candidates in the text report.
        quiet: Suppress progress output.

    Examples:
        pytestmut run src/mypackage --report html
        pytestmut run --tests tests/test_billing.py --staged
    """
    arguments: tuple[str, ...] | None = None
    if tests or pytest_args:
        extra = shlex.split(pytest_args or "", posix=os.name != "nt")
        arguments = (*(tests or ()), *extra)
    try:
        cfg = with_overrides(
            _load(config),
            paths=tuple(paths or ()),
            pytest_args=arguments,
            workers=workers,
            level=level,
            min_score=min_score,
            formats=tuple(report or ()),
            selection=selection,
            diff_base=diff,
            cache=False if no_cache else None,
            memory_budget=max_memory,
        )
    except PytestmutError as exc:
        raise _fail(exc) from exc
    options = RunOptions(
        staged=staged,
        hook=hook,
        only=tuple(only or ()),
        keep_jobs=keep_jobs,
        reached_only=bool(tests),
    )
    code = run_and_report(cfg, options, quiet=quiet, show_suppressed=show_suppressed)
    raise typer.Exit(code=code)


def _result(config: Optional[Path]) -> tuple[Config, RunResult]:
    cfg = _load(config)
    state = StateDir(cfg.root, cfg.state_dir)
    return cfg, load_result(state.results_file)


@app.command(
    help="Show the last run again in any format, without running tests.",
    epilog=_examples(
        "pytestmut report",
        "pytestmut report --format markdown --output mutation.md",
        "pytestmut report --format json",
    ),
)
def report(
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="text, markdown, html or json.")
    ] = "text",
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Write to this file.")
    ] = None,
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Config file to use.")
    ] = None,
    show_suppressed: Annotated[
        bool, typer.Option("--show-suppressed", help="List suppressed candidates.")
    ] = False,
) -> None:
    """Render the last run again, in any format, without running tests.

    Arguments:
        fmt: Report format.
        output: File to write; the report goes to the terminal without it.
        config: Config file to use instead of searching for one.
        show_suppressed: List suppressed candidates in the text report.

    Example:
        pytestmut report --format markdown --output mutation.md
    """
    try:
        cfg, result = _result(config)
        text = render(
            result,
            fmt,
            show_suppressed=show_suppressed,
            markdown_max_survivors=cfg.report.markdown_max_survivors,
            embed_source=cfg.report.html_embed_source,
        )
    except PytestmutError as exc:
        raise _fail(exc) from exc
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8", newline="\n")
        typer.echo(f"pytestmut: {fmt} report: {output}", err=True)


def _find(result: RunResult, identifier: str) -> Mutant:
    matches = [mutant for mutant in result.mutants if mutant.id.startswith(identifier)]
    if not matches:
        raise ConfigError(f"no mutant with id {identifier!r} in the last run")
    if len(matches) > 1:
        raise ConfigError(
            f"{identifier!r} matches {len(matches)} mutants; use more characters"
        )
    return matches[0]


@app.command(
    help="Show one mutant: the change, what happened, and what test would catch it.",
    epilog=_examples(
        "pytestmut show 60ffdef3",
    ),
)
def show(
    mutant: Annotated[str, typer.Argument(help="Mutant id, or a unique prefix.")],
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Config file to use.")
    ] = None,
) -> None:
    """Show one mutant of the last run: its change and what happened to it.

    Arguments:
        mutant: Mutant id as printed in reports, or a unique prefix of it.
        config: Config file to use instead of searching for one.

    Example:
        pytestmut show 3c494f7a
    """
    try:
        _, result = _result(config)
        found = _find(result, mutant)
    except PytestmutError as exc:
        raise _fail(exc) from exc
    before, after = SourceCache(result).diff(found)
    verdict = result.results.get(found.id)
    status = result.status_of(found)
    lines = [
        f"{found.id}  {found.operator}  {status.value}",
        f"{found.path}:{found.span.line}"
        + (f"  in {found.scope}" if found.scope else "")
        + f"  policy {found.policy}",
        "",
        *[f"- {line}" for line in before],
        *[f"+ {line}" for line in after],
        "",
    ]
    if found.pre_status is not None:
        lines.append(f"reason: {found.reason}")
    if verdict is not None:
        if verdict.killing_test:
            lines.append(f"killed by: {verdict.killing_test} ({verdict.kill_phase})")
        if verdict.reason:
            lines.append(f"reason: {verdict.reason}")
        lines.append(
            f"tests selected: {verdict.tests_selected}, run: {verdict.tests_run}, "
            f"attempts: {verdict.attempts}, {verdict.duration_s:.2f}s"
            + (", from cache" if verdict.from_cache else "")
        )
        if verdict.detail:
            lines += ["", verdict.detail.strip()]
    if verdict is not None and verdict.hint:
        lines += ["", verdict.hint]
    if status is Status.SURVIVED:
        lines += ["", f"reproduce: pytestmut exec {found.id} -- -x"]
    typer.echo("\n".join(lines))


@app.command(
    "exec",
    context_settings={"ignore_unknown_options": True},
    help="Run pytest yourself with one mutant active, to see a test catch it.",
    epilog=_examples(
        "pytestmut exec 60ffdef3 -- tests/test_pricing.py -x",
    ),
)
def exec_mutant(
    mutant: Annotated[str, typer.Argument(help="Mutant id, or a unique prefix.")],
    pytest_args: Annotated[
        Optional[list[str]], typer.Argument(help="Arguments passed to pytest.")
    ] = None,
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Config file to use.")
    ] = None,
) -> None:
    """Run pytest yourself with one mutant active, to debug it.

    Arguments:
        mutant: Mutant id as printed in reports, or a unique prefix of it.
        pytest_args: Arguments passed to pytest, after ``--``.
        config: Config file to use instead of searching for one.

    Example:
        pytestmut exec 3c494f7a -- tests/test_billing.py -x
    """
    try:
        cfg, result = _result(config)
        found = _find(result, mutant)
        if found.pre_status is not None and found.pre_status is not Status.SUPPRESSED:
            raise ConfigError(
                f"mutant {found.id} is {found.pre_status.value} and cannot be run"
            )
        state = StateDir(cfg.root, cfg.state_dir)
        state.ensure()
        digests = {item.path: item.sha256 for item in result.files}
        target = identify(0, found.path, cfg.root / found.path, digests[found.path])
        scratch = state.new_run(cfg.safety.tmp_root)
        job_dir = scratch.job_dir("exec")
        job = Job(id="exec", mode=MUTATE, mutant=found)
        write_job(job_dir, job, (target,), "exec", own_group=False)
        environment = build_environment(
            os.environ,
            cfg.safety,
            "exec",
            state.pycache_prefix(sys.implementation.cache_tag),
            job_dir / "void",
        )
    except (PytestmutError, OSError, KeyError) as exc:
        error = exc if isinstance(exc, PytestmutError) else ConfigError(str(exc))
        raise _fail(error) from exc
    typer.echo(
        f"pytestmut: {found.path}:{found.span.line} {found.operator}: "
        f"{found.original!r} -> {found.replacement!r}",
        err=True,
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *(pytest_args or [])],
        cwd=cfg.root,
        env=environment.for_job(job_dir, 0),
        check=False,
    )
    state.remove_scratch(scratch)
    raise typer.Exit(code=completed.returncode)


@app.command(
    help="Delete .pytestmut/: cache, results and reports.",
    epilog=_examples(
        "pytestmut clean",
    ),
)
def clean(
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Config file to use.")
    ] = None,
) -> None:
    """Delete the state directory: cache, results and reports kept in it.

    Arguments:
        config: Config file to use instead of searching for one.
    """
    try:
        cfg = _load(config)
        state = StateDir(cfg.root, cfg.state_dir)
        removed = state.clean()
    except PytestmutError as exc:
        raise _fail(exc) from exc
    typer.echo(f"removed {state.path}" if removed else "nothing to remove")


@app.command(
    help="Print a starter configuration, or add it to pyproject.toml with --write.",
    epilog=_examples(
        "pytestmut init",
        "pytestmut init --write",
    ),
)
def init(
    write: Annotated[
        bool, typer.Option("--write", help="Append the config to pyproject.toml.")
    ] = False,
) -> None:
    """Print a starter configuration, or add it to pyproject.toml.

    Arguments:
        write: Append to ``pyproject.toml`` in the current directory instead
            of printing.
    """
    if not write:
        typer.echo(STARTER_CONFIG, nl=False)
        return
    try:
        if find_config(Path.cwd()) is not None:
            raise ConfigError("a pytestmut configuration already exists here")
    except PytestmutError as exc:
        raise _fail(exc) from exc
    pyproject = Path.cwd() / "pyproject.toml"
    existing = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    separator = "" if not existing or existing.endswith("\n\n") else "\n"
    if existing and not existing.endswith("\n"):
        separator = "\n\n"
    pyproject.write_text(existing + separator + STARTER_CONFIG, encoding="utf-8")
    typer.echo(f"added [tool.{CONFIG_TABLE}] to {pyproject}")


@app.command(
    help="List the mutation operators, their level and an example of each.",
    epilog=_examples(
        "pytestmut operators",
    ),
)
def operators() -> None:
    """List the mutation operators and the level each belongs to."""
    width = max(len(name) for name in OPERATORS)
    for info in OPERATORS.values():
        level = info.level.value
        typer.echo(
            f"{info.id:<{width}}  {level:<8}  {info.summary:<32}  {info.example}"
        )
