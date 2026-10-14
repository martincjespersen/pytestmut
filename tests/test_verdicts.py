"""Classification of recorded evidence and evaluation of policies."""

from __future__ import annotations

from typing import Any

import pytest

from pytestmut.config.model import PolicyConfig, Thresholds
from pytestmut.exec.jobs import RunRecord, TestPhase
from pytestmut.exec.supervisor import ProcessOutcome
from pytestmut.mutation.model import Mutant, MutantResult, SourceSpan, Splice, Status
from pytestmut.policy.evaluate import evaluate_policy, overall_scores
from pytestmut.verdict.classify import (
    CONFIRM_CONTROL,
    CONFIRM_NONE,
    CONFIRM_RETRY,
    CONFIRM_SOLO,
    classify,
)

PASSING = frozenset({"t::a", "t::b"})


def _phase(
    nodeid: str, outcome: str, time: float, when: str = "call", **extra: Any
) -> TestPhase:
    return TestPhase(nodeid, when, outcome, 0.01, time, **extra)


def _record(**fields: Any) -> RunRecord:
    record = RunRecord(configured=True, root_seen=True)
    for name, value in fields.items():
        setattr(record, name, value)
    return record


def _loaded(time: float = 1.0, mutated: bool = True) -> list[dict[str, Any]]:
    return [{"t": time, "mutated": mutated, "path": "/p/m.py"}]


PASS_A = _phase("t::a", "passed", 2.0)
PASS_B = _phase("t::b", "passed", 3.0)
FAIL_B = _phase("t::b", "failed", 3.0, exception="AssertionError", message="boom")

CASES = [
    (
        "clean pass with the mutant loaded survives",
        ProcessOutcome(0),
        _record(exitstatus=0, loads=_loaded(), phases=[PASS_A, PASS_B]),
        (Status.SURVIVED, "", CONFIRM_NONE),
    ),
    (
        "a baseline-passing test failing after activation kills",
        ProcessOutcome(1),
        _record(exitstatus=1, loads=_loaded(), phases=[PASS_A, FAIL_B]),
        (Status.KILLED, "test", CONFIRM_NONE),
    ),
    (
        "a failure before the mutant was loaded is not a kill",
        ProcessOutcome(1),
        _record(exitstatus=1, loads=_loaded(time=9.0), phases=[FAIL_B]),
        (Status.ERROR, "failed-without-mutant", CONFIRM_RETRY),
    ),
    (
        "a failure with no activation at all is not a kill",
        ProcessOutcome(1),
        _record(exitstatus=1, phases=[FAIL_B]),
        (Status.ERROR, "failed-without-mutant", CONFIRM_RETRY),
    ),
    (
        "passing tests without activation is an error, never a survivor",
        ProcessOutcome(0),
        _record(exitstatus=0, phases=[PASS_A]),
        (Status.ERROR, "not-activated", CONFIRM_NONE),
    ),
    (
        "loading the file unmutated is not activation",
        ProcessOutcome(0),
        _record(exitstatus=0, loads=_loaded(mutated=False), phases=[PASS_A]),
        (Status.ERROR, "not-activated", CONFIRM_NONE),
    ),
    (
        "zero tests run is an error, never a survivor",
        ProcessOutcome(0),
        _record(exitstatus=0, loads=_loaded()),
        (Status.ERROR, "no-tests-ran", CONFIRM_NONE),
    ),
    (
        "selected tests that were not collected are an error",
        ProcessOutcome(0),
        _record(
            exitstatus=0,
            loads=_loaded(),
            phases=[PASS_A],
            missing=["t::b"],
            missing_count=1,
        ),
        (Status.ERROR, "selected-tests-missing", CONFIRM_NONE),
    ),
    (
        "every test skipped still counts as a survivor, flagged",
        ProcessOutcome(0),
        _record(
            exitstatus=0,
            loads=_loaded(),
            phases=[_phase("t::a", "skipped", 2.0, "setup")],
        ),
        (Status.SURVIVED, "skipped-under-mutant", CONFIRM_NONE),
    ),
    (
        "a collection error after activation is a kill pending a control run",
        ProcessOutcome(2),
        _record(
            exitstatus=2, loads=_loaded(), collect_errors=[("t.py", "ImportError")]
        ),
        (Status.KILLED, "collection", CONFIRM_CONTROL),
    ),
    (
        "a collection error without activation is an error",
        ProcessOutcome(2),
        _record(exitstatus=2, collect_errors=[("t.py", "ImportError")]),
        (Status.ERROR, "collection-error-without-mutant", CONFIRM_RETRY),
    ),
    (
        "a crash before pytest configured, after activation, is a startup kill",
        ProcessOutcome(4, output="ImportError while loading conftest"),
        RunRecord(loads=_loaded()),
        (Status.KILLED, "startup", CONFIRM_CONTROL),
    ),
    (
        "a crash before pytest configured with no activation is an error",
        ProcessOutcome(1, output="SyntaxError in helper script"),
        RunRecord(),
        (Status.ERROR, "no-record", CONFIRM_RETRY),
    ),
    (
        "a process that dies mid-test is an error to retry, not a kill",
        ProcessOutcome(-11),
        _record(loads=_loaded(), started=["t::a"]),
        (Status.ERROR, "crash", CONFIRM_RETRY),
    ),
    (
        "an expired budget is a timeout pending a solo retry",
        ProcessOutcome(-9, timed_out=True),
        _record(loads=_loaded(), started=["t::a"]),
        (Status.TIMEOUT, "budget", CONFIRM_SOLO),
    ),
    (
        "a memory kill is oom",
        ProcessOutcome(-9, oom=True),
        _record(loads=_loaded()),
        (Status.OOM, "memory-limit", CONFIRM_NONE),
    ),
    (
        "a MemoryError failure is oom",
        ProcessOutcome(1),
        _record(
            exitstatus=1,
            loads=_loaded(),
            phases=[_phase("t::a", "failed", 2.0, exception="MemoryError")],
        ),
        (Status.OOM, "memory-error", CONFIRM_NONE),
    ),
    (
        "a deadline failure needs a solo retry before it counts",
        ProcessOutcome(1),
        _record(
            exitstatus=1,
            loads=_loaded(),
            phases=[_phase("t::a", "failed", 2.0, exception="DeadlineExceeded")],
        ),
        (Status.KILLED, "test", CONFIRM_SOLO),
    ),
    (
        "a pytest-timeout failure needs a solo retry before it counts",
        ProcessOutcome(1),
        _record(
            exitstatus=1,
            loads=_loaded(),
            phases=[
                _phase(
                    "t::a",
                    "failed",
                    2.0,
                    exception="Failed",
                    message="Failed: Timeout >1.0s",
                )
            ],
        ),
        (Status.KILLED, "test", CONFIRM_SOLO),
    ),
    (
        "a failure only in a test outside the baseline is an error",
        ProcessOutcome(1),
        _record(
            exitstatus=1, loads=_loaded(), phases=[_phase("t::flaky", "failed", 2.0)]
        ),
        (Status.ERROR, "failure-outside-baseline", CONFIRM_NONE),
    ),
    (
        "an internal pytest error is an error",
        ProcessOutcome(3),
        _record(exitstatus=3, loads=_loaded(), internal_errors=["INTERNALERROR"]),
        (Status.ERROR, "pytest-exit-3", CONFIRM_RETRY),
    ),
    (
        "no tests collected is an error",
        ProcessOutcome(5),
        _record(exitstatus=5, loads=_loaded()),
        (Status.ERROR, "no-tests-collected", CONFIRM_RETRY),
    ),
    (
        "pytest and the process disagreeing on the exit status is an error",
        ProcessOutcome(1),
        _record(exitstatus=0, loads=_loaded(), phases=[PASS_A]),
        (Status.ERROR, "exit-status-mismatch", CONFIRM_RETRY),
    ),
    (
        "a boot problem such as a changed source file is an error",
        ProcessOutcome(0),
        _record(exitstatus=0, boot_errors=["m.py: source changed"], phases=[PASS_A]),
        (Status.ERROR, "boot", CONFIRM_NONE),
    ),
    (
        "unsafe options are an error",
        ProcessOutcome(3),
        _record(unsafe_options=["--lf"]),
        (Status.ERROR, "unsafe-options", CONFIRM_NONE),
    ),
    (
        "a process that never started is an error",
        ProcessOutcome(None, output="cannot start"),
        RunRecord(),
        (Status.ERROR, "not-started", CONFIRM_NONE),
    ),
    (
        "a cancelled run is not run",
        ProcessOutcome(-9, cancelled=True),
        RunRecord(),
        (Status.NOT_RUN, "cancelled", CONFIRM_NONE),
    ),
]


@pytest.mark.parametrize(
    "name, process, record, expected", CASES, ids=[c[0] for c in CASES]
)
def test_classify(
    name: str,
    process: ProcessOutcome,
    record: RunRecord,
    expected: tuple[Status, str, str],
) -> None:
    verdict = classify(process, record, PASSING)
    assert (verdict.status, verdict.reason, verdict.confirm) == expected


def test_kill_names_the_first_failing_test() -> None:
    record = _record(
        exitstatus=1,
        loads=_loaded(),
        phases=[
            PASS_A,
            _phase("t::b", "failed", 3.0, "teardown", exception="E", message="m"),
        ],
    )
    verdict = classify(ProcessOutcome(1), record, PASSING)
    assert (verdict.killing_test, verdict.kill_phase, verdict.tests_run) == (
        "t::b",
        "teardown",
        2,
    )


def test_control_runs_need_no_activation() -> None:
    clean = _record(exitstatus=0, phases=[PASS_A])
    assert (
        classify(ProcessOutcome(0), clean, PASSING, mutated=False).status
        is Status.SURVIVED
    )
    broken = _record(exitstatus=1, phases=[FAIL_B])
    assert (
        classify(ProcessOutcome(1), broken, PASSING, mutated=False).status
        is Status.KILLED
    )


def _mutant(
    identifier: str, policy: str = "p", operator: str = "cmp.negate", **extra: Any
) -> Mutant:
    return Mutant(
        id=identifier,
        path="m.py",
        scope="f",
        operator=operator,
        span=SourceSpan(1, 0, 1, 1),
        splice=Splice(0, 1, b"x"),
        original="a",
        replacement="b",
        static=False,
        policy=policy,
        cover_lines=(1, 1),
        **extra,
    )


def _evaluate(
    statuses: dict[str, Status], policy: PolicyConfig, **mutants: Mutant
) -> Any:
    items = [mutants.get(name) or _mutant(name) for name in statuses]
    results = {name: MutantResult(name, status) for name, status in statuses.items()}
    return evaluate_policy(policy, items, results)


def test_scores_and_denominators() -> None:
    statuses = {
        "k1": Status.KILLED,
        "k2": Status.KILLED,
        "t1": Status.TIMEOUT,
        "s1": Status.SURVIVED,
        "n1": Status.NO_COVERAGE,
        "e1": Status.ERROR,
        "u1": Status.UNSUPPORTED,
        "r1": Status.NOT_RUN,
    }
    result = _evaluate(statuses, PolicyConfig(name="p"))
    # detected 3 (2 killed + 1 timeout) of 5 scored; errors and the rest stay out.
    assert result.detected == 3 and result.total == 8
    assert result.score == pytest.approx(60.0)
    assert result.covered_score == pytest.approx(75.0)
    strict = PolicyConfig(
        name="p", thresholds=Thresholds(count_timeouts_as_killed=False)
    )
    result = _evaluate(statuses, strict)
    assert result.detected == 2
    assert result.score == pytest.approx(40.0)
    assert result.covered_score == pytest.approx(50.0)
    score, covered = overall_scores([result])
    assert score == pytest.approx(40.0) and covered == pytest.approx(50.0)


def test_pre_run_statuses_stay_out_of_the_score() -> None:
    items = [
        _mutant("a"),
        _mutant("b", pre_status=Status.SUPPRESSED, reason="logging"),
        _mutant("c", pre_status=Status.SUPPRESSED, reason="inline"),
        _mutant("d", pre_status=Status.EQUIVALENT),
        _mutant("e", pre_status=Status.INVALID),
        _mutant("other", policy="q"),
    ]
    result = evaluate_policy(
        PolicyConfig(name="p"), items, {"a": MutantResult("a", Status.KILLED)}
    )
    assert result.total == 5 and result.score == 100.0
    assert result.suppressed == {"logging": 1, "inline": 1}
    assert result.user_suppressions == 1


def test_nothing_to_score_passes_score_gates() -> None:
    policy = PolicyConfig(name="p", thresholds=Thresholds(min_score=100))
    result = evaluate_policy(policy, [], {})
    assert result.score is None and result.passed


def test_each_threshold_can_fail_the_policy() -> None:
    statuses = {
        "k": Status.KILLED,
        "s": Status.SURVIVED,
        "n": Status.NO_COVERAGE,
        "t": Status.TIMEOUT,
    }
    cases = {
        "below the minimum 80%": Thresholds(min_score=80),
        "covered score": Thresholds(min_covered_score=90),
        "1 survivor(s), at most 0": Thresholds(max_survivors=0),
        "without coverage, at most 0": Thresholds(max_no_coverage=0),
        "timeout(s), at most 0": Thresholds(max_timeouts=0),
    }
    for fragment, thresholds in cases.items():
        result = _evaluate(statuses, PolicyConfig(name="p", thresholds=thresholds))
        assert len(result.failures) == 1 and fragment in result.failures[0]
    assert _evaluate(statuses, PolicyConfig(name="p")).passed


def test_tolerated_survivors_do_not_count() -> None:
    policy = PolicyConfig(
        name="p",
        tolerate_survivors=frozenset({"num.shift"}),
        thresholds=Thresholds(max_survivors=0, min_score=100),
    )
    result = _evaluate(
        {"k": Status.KILLED, "s": Status.SURVIVED},
        policy,
        s=_mutant("s", operator="num.shift"),
    )
    assert result.tolerated == 1 and result.survived == 0
    assert result.score == 100.0 and result.passed


def test_suppression_budget_and_refused_skips() -> None:
    items = [
        _mutant("a", pre_status=Status.SUPPRESSED, reason="inline"),
        _mutant("b", pre_status=Status.SUPPRESSED, reason="config-line"),
        _mutant("c", pre_status=Status.SUPPRESSED, reason="docstring"),
    ]
    policy = PolicyConfig(name="p", thresholds=Thresholds(max_suppressions=1))
    result = evaluate_policy(policy, items, {}, refused=["m.py:3: not allowed"])
    assert result.user_suppressions == 2
    assert result.failures[0] == "m.py:3: not allowed"
    assert "2 suppression(s)" in result.failures[1]


def _at(activation: float, failure: float) -> RunRecord:
    return _record(
        exitstatus=1,
        loads=_loaded(time=activation),
        phases=[_phase("t::a", "failed", failure, exception="AssertionError")],
    )


def test_activation_must_precede_the_failure_within_clock_tolerance() -> None:
    # Clocks of two processes may disagree slightly; half a second is not that.
    assert classify(ProcessOutcome(1), _at(1.0, 2.0), PASSING).status is Status.KILLED
    assert classify(ProcessOutcome(1), _at(0.05, 0.0), PASSING).status is Status.KILLED
    assert classify(ProcessOutcome(1), _at(0.5, 0.0), PASSING).status is Status.ERROR
    assert classify(ProcessOutcome(1), _at(0.0, 0.5), PASSING).status is Status.KILLED


def test_startup_evidence_is_read_carefully() -> None:
    activated = RunRecord(loads=_loaded())
    # Dying with any non-zero status after loading the mutant is a startup kill.
    for code in (1, 2, 4, -11):
        verdict = classify(ProcessOutcome(code, output="boom"), activated, PASSING)
        assert (verdict.status, verdict.reason) == (Status.KILLED, "startup")
    # Exiting cleanly without a record is not evidence of anything.
    clean = classify(ProcessOutcome(0), activated, PASSING)
    assert (clean.status, clean.reason) == (Status.ERROR, "no-record")
    # A test that never finished, without the mutant loaded, is not a crash
    # the mutant can be blamed for.
    hung = _record(started=["t::a"])
    verdict = classify(ProcessOutcome(-9), hung, PASSING)
    assert (verdict.status, verdict.reason) == (Status.ERROR, "incomplete-record")
    blamed = _record(started=["t::a"], loads=_loaded())
    verdict = classify(ProcessOutcome(-9), blamed, PASSING)
    assert (verdict.reason, verdict.killing_test) == ("crash", "t::a")
    idle = _record(loads=_loaded())
    assert classify(ProcessOutcome(-9), idle, PASSING).reason == "incomplete-record"


def test_survivor_needs_a_passed_call_phase() -> None:
    def survived(*phases: TestPhase) -> str:
        record = _record(exitstatus=0, loads=_loaded(), phases=list(phases))
        verdict = classify(ProcessOutcome(0), record, PASSING)
        assert verdict.status is Status.SURVIVED
        return verdict.reason

    whole = (
        _phase("t::a", "passed", 2.0, "setup"),
        _phase("t::a", "passed", 2.1, "call"),
        _phase("t::a", "passed", 2.2, "teardown"),
    )
    assert survived(*whole) == ""
    # Setup passing while the call is skipped is not a test that passed.
    skipped = (
        _phase("t::a", "passed", 2.0, "setup"),
        _phase("t::a", "skipped", 2.1, "call"),
        _phase("t::a", "passed", 2.2, "teardown"),
    )
    assert survived(*skipped) == "skipped-under-mutant"


def test_only_timing_failures_wait_for_a_solo_retry() -> None:
    def confirm(exception: str, message: str) -> str:
        phase = _phase("t::a", "failed", 2.0, exception=exception, message=message)
        record = _record(exitstatus=1, loads=_loaded(), phases=[phase])
        return classify(ProcessOutcome(1), record, PASSING).confirm

    assert confirm("Failed", "Failed: Timeout >2.0s") == CONFIRM_SOLO
    assert confirm("Failed", "Failed: custom reason") == CONFIRM_NONE
    assert confirm("AssertionError", "assert 'Timeout' == 'x'") == CONFIRM_NONE
    assert confirm("TimeoutError", "") == CONFIRM_SOLO


def test_details_say_what_happened() -> None:
    collection = _record(
        exitstatus=2, loads=_loaded(), collect_errors=[("tests/t.py", "ImportError: x")]
    )
    verdict = classify(ProcessOutcome(2), collection, PASSING)
    assert verdict.killing_test == "tests/t.py" and "ImportError: x" in verdict.detail
    anonymous = _record(exitstatus=2, loads=_loaded(), collect_errors=[("", "boom")])
    assert classify(ProcessOutcome(2), anonymous, PASSING).killing_test is None
    outside = _record(
        exitstatus=1,
        loads=_loaded(),
        phases=[PASS_A, _phase("t::flaky", "failed", 2.5)],
    )
    detail = classify(ProcessOutcome(1), outside, PASSING).detail
    assert "t::flaky" in detail and "t::a" not in detail
    internal = _record(exitstatus=3, loads=_loaded(), internal_errors=["INTERNAL boom"])
    verdict = classify(ProcessOutcome(3, output="unrelated noise"), internal, PASSING)
    assert verdict.detail == "INTERNAL boom"
    silent = _record(exitstatus=3, loads=_loaded())
    verdict = classify(ProcessOutcome(3, output="  the output  "), silent, PASSING)
    assert verdict.detail == "the output"


def test_long_output_keeps_its_tail() -> None:
    exact = "x" * 1500
    assert classify(
        ProcessOutcome(None, output=exact), RunRecord(), PASSING
    ).detail == (exact)
    long = "a" * 600 + "b" * 1500
    detail = classify(ProcessOutcome(None, output=long), RunRecord(), PASSING).detail
    assert detail == "..." + "b" * 1500


def test_policy_result_defaults_are_neutral() -> None:
    from pytestmut.policy.evaluate import PolicyResult

    empty = PolicyResult(name="p")
    assert (empty.total, empty.detected, empty.survived, empty.tolerated) == (0,) * 4
    assert (empty.no_coverage, empty.timeouts, empty.user_suppressions) == (0,) * 3
    assert empty.score is None and empty.covered_score is None and empty.passed
    from pytestmut.verdict.classify import Verdict

    assert Verdict(Status.KILLED).tests_run == 0


def test_timeouts_and_memory_kills_are_counted_together() -> None:
    statuses = {"t": Status.TIMEOUT, "o": Status.OOM, "s": Status.SURVIVED}
    result = _evaluate(statuses, PolicyConfig(name="p"))
    assert result.timeouts == 2 and result.detected == 2
    assert result.score == pytest.approx(200 / 3)
    capped = PolicyConfig(name="p", thresholds=Thresholds(max_timeouts=1))
    assert not _evaluate(statuses, capped).passed


def test_meeting_a_threshold_exactly_passes() -> None:
    statuses = {"k": Status.KILLED, "s": Status.SURVIVED}
    exact = PolicyConfig(
        name="p",
        thresholds=Thresholds(min_score=50, min_covered_score=50, max_survivors=1),
    )
    assert _evaluate(statuses, exact).passed
    above = PolicyConfig(name="p", thresholds=Thresholds(min_covered_score=50.1))
    assert not _evaluate(statuses, above).passed
