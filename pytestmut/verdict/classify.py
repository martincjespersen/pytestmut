"""Decide a mutant's status from recorded evidence.

The exit code of the test process is never enough. A crash at startup, a
usage error and a killed mutant can all exit non-zero, and tools that treat
them alike report fabricated scores. The rule here is pytest's own: a mutant
is killed if and only if a test that passed in the baseline reports a failure
after the mutated code was loaded. Anything the record cannot prove is an
``error``, which gates the run separately instead of flattering the score.
"""

from __future__ import annotations

from dataclasses import dataclass

from pytestmut.exec.jobs import RunRecord, TestPhase
from pytestmut.exec.supervisor import ProcessOutcome
from pytestmut.mutation.model import Status

# Follow-up a provisional verdict needs before it is final.
CONFIRM_NONE = ""
CONFIRM_RETRY = "retry"
CONFIRM_SOLO = "solo"
CONFIRM_CONTROL = "control"

# Failures that say "this was slow" or "this is flaky", not "this is wrong".
_TIMING_EXCEPTIONS = frozenset(
    {"DeadlineExceeded", "Flaky", "FlakyFailure", "TimeoutError", "Timeout"}
)
_CLOCK_TOLERANCE = 0.05


@dataclass(frozen=True)
class Verdict:
    """A status with the evidence behind it.

    Attributes:
        status: The status the evidence supports.
        reason: Reason code qualifying the status.
        killing_test: Node id of the first failing test, for kills.
        kill_phase: Phase the killing test failed in.
        tests_run: Number of distinct tests that reported an outcome.
        detail: Free text for humans.
        confirm: Follow-up needed before the verdict is final.
    """

    status: Status
    reason: str = ""
    killing_test: str | None = None
    kill_phase: str | None = None
    tests_run: int = 0
    detail: str = ""
    confirm: str = CONFIRM_NONE


def _tail(text: str, limit: int = 1500) -> str:
    stripped = text.strip()
    return stripped if len(stripped) <= limit else "..." + stripped[-limit:]


def _is_timing_failure(phase: TestPhase) -> bool:
    if phase.exception in _TIMING_EXCEPTIONS:
        return True
    return phase.exception == "Failed" and "Timeout" in phase.message


def classify(
    process: ProcessOutcome,
    record: RunRecord,
    passing: frozenset[str],
    mutated: bool = True,
) -> Verdict:
    """Classify one finished job.

    Arguments:
        process: How the test process ended.
        record: What the test processes reported.
        passing: Node ids of tests that passed in the baseline; only these
            can kill.
        mutated: False for control runs, where nothing is expected to load
            mutated and a clean pass is the good outcome.

    Returns:
        The verdict, possibly provisional.
    """
    tests_run = len({phase.nodeid for phase in record.phases})
    if process.cancelled:
        return Verdict(Status.NOT_RUN, "cancelled", tests_run=tests_run)
    if process.oom:
        return Verdict(Status.OOM, "memory-limit", tests_run=tests_run)
    if process.timed_out:
        return Verdict(
            Status.TIMEOUT, "budget", tests_run=tests_run, confirm=CONFIRM_SOLO
        )
    if process.returncode is None:
        return Verdict(Status.ERROR, "not-started", detail=_tail(process.output))
    if record.boot_errors:
        return Verdict(Status.ERROR, "boot", detail="; ".join(record.boot_errors))
    if record.unsafe_options:
        return Verdict(
            Status.ERROR, "unsafe-options", detail=", ".join(record.unsafe_options)
        )
    activation = record.activation_time
    failures = [
        phase
        for phase in record.phases
        if phase.outcome == "failed" and phase.nodeid in passing
    ]
    if failures:
        return _killed_by_test(failures[0], activation, tests_run, mutated)
    if record.collect_errors:
        nodeid, message = record.collect_errors[0]
        if mutated and activation is None:
            return Verdict(
                Status.ERROR,
                "collection-error-without-mutant",
                detail=_tail(f"{nodeid}: {message}"),
                confirm=CONFIRM_RETRY,
            )
        return Verdict(
            Status.KILLED,
            "collection",
            killing_test=nodeid or None,
            tests_run=tests_run,
            detail=_tail(message),
            confirm=CONFIRM_CONTROL,
        )
    if not record.finished:
        return _unfinished(process, record, activation, tests_run)
    if record.exitstatus != process.returncode:
        return Verdict(
            Status.ERROR,
            "exit-status-mismatch",
            tests_run=tests_run,
            detail=f"pytest reported {record.exitstatus}, the process exited "
            f"{process.returncode}",
            confirm=CONFIRM_RETRY,
        )
    if record.exitstatus == 0:
        return _clean_exit(record, activation, tests_run, mutated)
    if record.exitstatus == 5:
        return Verdict(Status.ERROR, "no-tests-collected", confirm=CONFIRM_RETRY)
    if record.exitstatus == 1:
        failed = sorted(
            {phase.nodeid for phase in record.phases if phase.outcome == "failed"}
        )
        examples = failed[:5]  # pytestmut: skip[num.shift] -- display limit
        return Verdict(
            Status.ERROR,
            "failure-outside-baseline",
            tests_run=tests_run,
            detail="failed without having passed in the baseline: "
            + ", ".join(examples),
        )
    detail = "; ".join(record.internal_errors) or _tail(process.output)
    return Verdict(
        Status.ERROR,
        f"pytest-exit-{record.exitstatus}",
        tests_run=tests_run,
        detail=_tail(detail),
        confirm=CONFIRM_RETRY,
    )


def _killed_by_test(
    first: TestPhase, activation: float | None, tests_run: int, mutated: bool
) -> Verdict:
    detail = _tail(first.message)
    if mutated and (activation is None or activation > first.time + _CLOCK_TOLERANCE):
        return Verdict(
            Status.ERROR,
            "failed-without-mutant",
            killing_test=first.nodeid,
            kill_phase=first.when,
            tests_run=tests_run,
            detail=detail,
            confirm=CONFIRM_RETRY,
        )
    if first.exception == "MemoryError":
        return Verdict(
            Status.OOM, "memory-error", killing_test=first.nodeid, tests_run=tests_run
        )
    return Verdict(
        Status.KILLED,
        "test",
        killing_test=first.nodeid,
        kill_phase=first.when,
        tests_run=tests_run,
        detail=detail,
        confirm=CONFIRM_SOLO if _is_timing_failure(first) else CONFIRM_NONE,
    )


def _unfinished(
    process: ProcessOutcome,
    record: RunRecord,
    activation: float | None,
    tests_run: int,
) -> Verdict:
    output = _tail(process.output)
    if not record.configured:
        if activation is not None and process.returncode != 0:
            # The mutated module broke a conftest or plugin import.
            return Verdict(
                Status.KILLED, "startup", detail=output, confirm=CONFIRM_CONTROL
            )
        return Verdict(Status.ERROR, "no-record", detail=output, confirm=CONFIRM_RETRY)
    unfinished = record.unfinished_test()
    if unfinished is not None and activation is not None:
        return Verdict(
            Status.ERROR,
            "crash",
            killing_test=unfinished,
            tests_run=tests_run,
            detail=output,
            confirm=CONFIRM_RETRY,
        )
    return Verdict(
        Status.ERROR,
        "incomplete-record",
        tests_run=tests_run,
        detail=output,
        confirm=CONFIRM_RETRY,
    )


def _clean_exit(
    record: RunRecord, activation: float | None, tests_run: int, mutated: bool
) -> Verdict:
    if mutated and activation is None:
        return Verdict(
            Status.ERROR,
            "not-activated",
            tests_run=tests_run,
            detail="the tests passed but the mutated file was never loaded",
        )
    if record.missing_count:
        examples = record.missing[:3]  # pytestmut: skip[num.shift] -- display limit
        return Verdict(
            Status.ERROR,
            "selected-tests-missing",
            tests_run=tests_run,
            detail=f"{record.missing_count} selected test(s) were not collected, "
            "e.g. " + ", ".join(examples),
        )
    if tests_run == 0:
        return Verdict(
            Status.ERROR,
            "no-tests-ran",
            detail="pytest exited cleanly without running a test",
        )
    passed = any(
        phase.when == "call" and phase.outcome == "passed" for phase in record.phases
    )
    reason = "" if passed else "skipped-under-mutant"
    return Verdict(Status.SURVIVED, reason, tests_run=tests_run)
