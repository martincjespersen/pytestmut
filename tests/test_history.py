"""Run provenance, the run history and comparing a mutant across runs."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from pytestmut.exec.scheduler import selection_digest
from pytestmut.mutation.model import Mutant, MutantResult, SourceSpan, Splice, Status
from pytestmut.report.history import compare, earlier_runs, history_lines
from pytestmut.report.model import (
    FileReport,
    Provenance,
    RunResult,
    from_json,
    to_json,
)
from pytestmut.state.statedir import StateDir

MUTANT = Mutant(
    id="0195b6974839",
    path="calc.py",
    scope="is_adult",
    operator="cmp.boundary",
    span=SourceSpan(2, 15, 2, 17),
    splice=Splice(30, 32, b">"),
    original=">=",
    replacement=">",
    static=False,
    policy="default",
    cover_lines=(2, 2),
)
PROVENANCE = Provenance(
    run_id="aaaa0001",
    commit="c0ffee" * 6 + "ab",
    sources="s1",
    tests="t1",
    config="c1",
    environment="e1",
)


def _run(
    run_id: str,
    started: str,
    status: Status,
    *,
    file_digest: str = "f1",
    selection: str = "sel1",
    selected: int = 1,
    killing_test: str | None = None,
    **provenance: object,
) -> RunResult:
    return RunResult(
        started_at=started,
        files=(FileReport(path="calc.py", sha256=file_digest),),
        mutants=(MUTANT,),
        results={
            MUTANT.id: MutantResult(
                MUTANT.id,
                status,
                killing_test=killing_test,
                tests_selected=selected,
                selection=selection,
            )
        },
        provenance=dataclasses.replace(
            PROVENANCE, run_id=run_id, **provenance  # type: ignore[arg-type]
        ),
    )


def test_a_new_test_is_named_as_the_reason_a_survivor_died() -> None:
    before = _run("aaaa0001", "2026-01-01T10:00:00", Status.SURVIVED)
    after = _run(
        "aaaa0002",
        "2026-01-01T10:05:00",
        Status.KILLED,
        tests="t2",
        selection="sel2",
        selected=2,
        killing_test="tests/test_calc.py::test_boundary",
    )
    found = compare(MUTANT, after, before)
    assert found is not None
    assert found.status is Status.SURVIVED and found.run_id == "aaaa0001"
    assert found.changes == ("test files changed", "different tests selected (1 -> 2)")


def test_every_recorded_difference_is_named() -> None:
    before = _run("aaaa0001", "2026-01-01T10:00:00", Status.KILLED)
    after = _run(
        "aaaa0002",
        "2026-01-01T10:05:00",
        Status.KILLED,
        file_digest="f2",
        commit="beef" * 10,
        config="c2",
        environment="e2",
    )
    found = compare(MUTANT, after, before)
    assert found is not None
    assert found.changes == (
        "commit c0ffeec0 -> beefbeef",
        "calc.py changed",
        "configuration changed",
        "environment changed (pytestmut, Python, packages or pytest)",
    )


def test_a_cached_kill_rechecked_by_its_killing_test_is_said_so() -> None:
    killer = "tests/test_calc.py::test_boundary"
    before = _run(
        "aaaa0001",
        "2026-01-01T10:00:00",
        Status.KILLED,
        selection="sel2",
        selected=2,
        killing_test=killer,
    )
    after = _run("aaaa0002", "2026-01-01T10:05:00", Status.KILLED, killing_test=killer)
    found = compare(MUTANT, after, before)
    assert found is not None
    assert found.changes == ("re-checked with just the test that killed it before",)


def test_a_verdict_that_changed_with_nothing_else_points_at_flaky_tests(
    tmp_path: Path,
) -> None:
    state = StateDir(tmp_path, ".pytestmut")
    state.ensure()
    before = _run("aaaa0001", "2026-01-01T10:00:00", Status.SURVIVED)
    state.archive(to_json(before), "aaaa0001")
    flipped = _run("aaaa0002", "2026-01-01T10:05:00", Status.KILLED)
    lines = history_lines(MUTANT, flipped, state.history_dir)
    assert lines[0].startswith("previously: survived in run aaaa0001, now killed")
    assert "flaky or order-dependent" in lines[1]
    same = _run("aaaa0003", "2026-01-01T10:10:00", Status.SURVIVED)
    assert history_lines(MUTANT, same, state.history_dir)[1] == (
        "changed since: nothing recorded"
    )


def test_earlier_runs_skip_the_current_later_and_unreadable_ones(
    tmp_path: Path,
) -> None:
    state = StateDir(tmp_path, ".pytestmut")
    state.ensure()
    current = _run("aaaa0002", "2026-01-01T10:05:00", Status.KILLED)
    for run in (
        _run("aaaa0001", "2026-01-01T10:00:00", Status.SURVIVED),
        current,
        _run("aaaa0003", "2026-01-01T10:10:00", Status.KILLED),
    ):
        state.archive(to_json(run), run.provenance.run_id)
    (state.history_dir / "broken.json").write_text("{", encoding="utf-8")
    found = earlier_runs(state.history_dir, current)
    assert [run.provenance.run_id for run in found] == ["aaaa0001"]


def test_a_mutant_missing_from_the_earlier_run_is_not_compared() -> None:
    before = dataclasses.replace(
        _run("aaaa0001", "2026-01-01T10:00:00", Status.KILLED), mutants=()
    )
    after = _run("aaaa0002", "2026-01-01T10:05:00", Status.KILLED)
    assert compare(MUTANT, after, before) is None


def test_the_history_keeps_only_the_most_recent_runs(tmp_path: Path) -> None:
    state = StateDir(tmp_path, ".pytestmut")
    state.ensure()
    for number in range(5):
        path = state.archive("{}", f"run{number}", keep=3)
        # Distinct modification times, oldest first, on every file system.
        os.utime(path, (1_000_000 + number, 1_000_000 + number))
    assert sorted(path.name for path in state.history_dir.iterdir()) == [
        "run2.json",
        "run3.json",
        "run4.json",
    ]


def test_new_runs_leave_the_history_alone(tmp_path: Path) -> None:
    state = StateDir(tmp_path, ".pytestmut")
    state.ensure()
    state.archive("{}", "kept")
    run = state.new_run(str(tmp_path / "scratch"))
    state.remove_scratch(run)
    assert (state.history_dir / "kept.json").is_file()


def test_provenance_survives_the_result_file() -> None:
    run = _run("aaaa0001", "2026-01-01T10:00:00", Status.KILLED, dirty=True)
    again = from_json(to_json(run))
    assert again.provenance == run.provenance
    assert again.results[MUTANT.id].selection == "sel1"
    assert again.run_label() == "run aaaa0001, commit c0ffeec0 with uncommitted changes"


def test_a_selection_digest_ignores_order_and_duplicates() -> None:
    tests = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    assert selection_digest(tests) == selection_digest(list(reversed(tests)) * 2)
    assert selection_digest(tests) != selection_digest(tests[:1])
    assert selection_digest([]) == ""
