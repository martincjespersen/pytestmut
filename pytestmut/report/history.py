"""How a mutant's verdict compares with earlier runs.

When a verdict changes between runs, the cause is the code, the tests, the
configuration, the environment or the choice of tests. Every run records a
digest of each of them, so the difference can be named instead of guessed:
an agent can tell "my new test kills it" from "different tests ran".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pytestmut.exceptions import ConfigError
from pytestmut.mutation.model import Mutant, MutantResult, Status
from pytestmut.report.model import RunResult, load_result

_SHORT = 8


@dataclass(frozen=True)
class Comparison:
    """A mutant in an earlier run, and what changed since.

    Attributes:
        run_id: Id of the earlier run.
        started_at: When the earlier run started.
        status: The mutant's status in the earlier run.
        changes: What differs between that run and the current one; empty
            when nothing recorded changed.
    """

    run_id: str
    started_at: str
    status: Status
    changes: tuple[str, ...]


def earlier_runs(history_dir: Path, current: RunResult) -> list[RunResult]:
    """Readable results of runs before the current one, most recent first.

    Arguments:
        history_dir: Directory holding one result file per run.
        current: The run to compare against; it is left out.

    Returns:
        The earlier results. Files written by another schema version, or
        unreadable, are skipped.
    """
    found: list[RunResult] = []
    for path in history_dir.glob("*.json"):
        try:
            result = load_result(path)
        except ConfigError:
            continue
        if result.provenance.run_id == current.provenance.run_id:
            continue
        if result.started_at > current.started_at:
            continue
        found.append(result)
    return sorted(found, key=lambda result: result.started_at, reverse=True)


def _file_digest(result: RunResult, path: str) -> str:
    return next((item.sha256 for item in result.files if item.path == path), "")


def _rechecked(now: MutantResult, then: MutantResult) -> bool:
    """True for a cached kill confirmed by running only its killing test."""
    return (
        now.status is Status.KILLED
        and now.tests_selected == 1
        and now.killing_test is not None
        and now.killing_test == then.killing_test
    )


def compare(
    mutant: Mutant, current: RunResult, earlier: RunResult
) -> Comparison | None:
    """Compare one mutant with the same mutant in an earlier run.

    Arguments:
        mutant: The mutant, from the current run.
        current: The current run.
        earlier: An earlier run.

    Returns:
        The comparison, or None when the earlier run had no such mutant.
    """
    before = next((item for item in earlier.mutants if item.id == mutant.id), None)
    if before is None:
        return None
    now, then = current.provenance, earlier.provenance
    changes: list[str] = []
    if now.commit and then.commit and now.commit != then.commit:
        changes.append(f"commit {then.commit[:_SHORT]} -> {now.commit[:_SHORT]}")
    if _file_digest(current, mutant.path) != _file_digest(earlier, mutant.path):
        changes.append(f"{mutant.path} changed")
    if now.tests != then.tests:
        changes.append("test files changed")
    if now.config != then.config:
        changes.append("configuration changed")
    if now.environment != then.environment:
        changes.append("environment changed (pytestmut, Python, packages or pytest)")
    result_now = current.results.get(mutant.id)
    result_then = earlier.results.get(mutant.id)
    if (
        result_now is not None
        and result_then is not None
        and result_now.selection
        and result_then.selection
        and result_now.selection != result_then.selection
    ):
        if _rechecked(result_now, result_then):
            changes.append("re-checked with just the test that killed it before")
        else:
            changes.append(
                f"different tests selected ({result_then.tests_selected} -> "
                f"{result_now.tests_selected})"
            )
    return Comparison(
        run_id=then.run_id,
        started_at=earlier.started_at,
        status=earlier.status_of(before),
        changes=tuple(changes),
    )


def history_lines(mutant: Mutant, current: RunResult, history_dir: Path) -> list[str]:
    """Describe the mutant's verdict in the most recent earlier run.

    Arguments:
        mutant: The mutant, from the current run.
        current: The current run.
        history_dir: Directory holding one result file per run.

    Returns:
        Lines for the ``show`` command; empty when no earlier run has it.
    """
    for earlier in earlier_runs(history_dir, current):
        found = compare(mutant, current, earlier)
        if found is None:
            continue
        status = current.status_of(mutant)
        if found.status is status:
            verdict = f"also {status.value} in run {found.run_id}"
        else:
            verdict = f"{found.status.value} in run {found.run_id}, now {status.value}"
        lines = [f"previously: {verdict} ({found.started_at})"]
        if found.changes:
            lines.append("changed since: " + "; ".join(found.changes))
        elif found.status is not status:
            lines.append(
                "changed since: nothing recorded, so the different verdict "
                "points at flaky or order-dependent tests"
            )
        else:
            lines.append("changed since: nothing recorded")
        return lines
    return []
