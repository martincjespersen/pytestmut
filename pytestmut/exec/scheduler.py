"""Run mutants in parallel and settle every provisional verdict.

Most mutants need one process. The rest are where wrong numbers come from, so
each ambiguous outcome gets a follow-up before it is final:

- a timeout is retried alone with a larger budget, then compared with an
  unmutated control run of the same tests;
- a kill with no failing test (import or collection error) needs the same
  tests to start cleanly without the mutant;
- the first kill credited to a test in a narrowed selection needs that test to
  pass on its own, otherwise it depends on tests that were left out;
- an error is retried once.
"""

from __future__ import annotations

import fnmatch
import queue
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace

from pytestmut.baseline.runner import Baseline
from pytestmut.baseline.testmap import Selection, Selector
from pytestmut.config.model import RunConfig, TimeoutConfig
from pytestmut.exec.jobs import CONTROL, MUTATE, Job
from pytestmut.exec.runner import JobOutcome, JobRunner
from pytestmut.mutation.model import Mutant, MutantResult, Status
from pytestmut.verdict.classify import (
    CONFIRM_CONTROL,
    CONFIRM_RETRY,
    CONFIRM_SOLO,
    Verdict,
    classify,
)

ResultCallback = Callable[[Mutant, MutantResult], None]
BUDGET_EXHAUSTED = "time budget exhausted"
# Test ids kept per verdict, enough to point at the tests to improve.
LISTED_TESTS = 10
_MAX_ROUNDS = 4
_THROTTLE_SAMPLE = 50
_THROTTLE_RATIO = 0.10


@dataclass(frozen=True)
class _Deferred:
    """A mutant that must be settled while nothing else runs."""

    mutant: Mutant
    attempts: int


class MutantExecutor:
    """Executes mutants and returns final verdicts.

    Arguments:
        runner: Runs the test processes.
        baseline: The validated baseline.
        selector: Chooses the tests of each mutant.
        timeout: Time budget settings.
        config: Scheduling settings.
        cancel: Set to abort; pending mutants become ``not_run``.
        on_result: Called with each final verdict as it arrives.
        deadline: ``time.monotonic()`` value after which unstarted mutants
            are left unrun, or None.
        protected: Ids of mutants the deadline does not apply to.
        hints: Test that killed each mutant in an earlier run, by mutant id;
            that test is tried first, alone.
    """

    def __init__(
        self,
        runner: JobRunner,
        baseline: Baseline,
        selector: Selector,
        timeout: TimeoutConfig,
        config: RunConfig,
        cancel: threading.Event,
        on_result: ResultCallback | None = None,
        deadline: float | None = None,
        protected: frozenset[str] = frozenset(),
        hints: Mapping[str, str] | None = None,
    ) -> None:
        self._runner = runner
        self._baseline = baseline
        self._selector = selector
        self._timeout = timeout
        self._config = config
        self._cancel = cancel
        self._on_result = on_result
        self._deadline = deadline
        self._protected = protected
        self._hints = dict(hints or {})
        # Running tests out of baseline order can expose order dependence, so
        # every kill is then checked the way narrowed selections are.
        self._reordered = config.test_order == "fastest"
        self._lock = threading.Lock()
        self._sequence = 0
        self._alone: dict[str, bool] = {}
        self._expired = 0
        self._finished = 0
        self.order_dependent: set[str] = set()
        self.warnings: list[str] = []

    def execute(
        self, mutants: Sequence[Mutant], workers: int
    ) -> dict[str, MutantResult]:
        """Run every mutant to a final verdict.

        Arguments:
            mutants: Runnable mutants.
            workers: Number of mutants in flight at once.

        Returns:
            Verdicts by mutant id.
        """
        results: dict[str, MutantResult] = {}
        deferred: list[_Deferred] = []
        slots: queue.Queue[int] = queue.Queue()
        for slot in range(workers):
            slots.put(slot)
        permits = threading.Semaphore(workers)
        throttled = False

        def task(mutant: Mutant) -> MutantResult | _Deferred:
            with permits:
                slot = slots.get()
                try:
                    return self._guarded(mutant, slot, solo=False)
                finally:
                    slots.put(slot)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(task, mutant): mutant for mutant in mutants}
            try:
                for future in as_completed(futures):
                    item = future.result()
                    if isinstance(item, _Deferred):
                        deferred.append(item)
                    else:
                        self._publish(results, futures[future], item)
                    if not throttled and workers > 1 and self._too_many_expired():
                        throttled = True
                        self._halve(permits, workers)
            except KeyboardInterrupt:
                self._cancel.set()
                raise
        for item in sorted(deferred, key=lambda entry: entry.mutant.id):
            result = self._guarded(item.mutant, 0, solo=True, attempts=item.attempts)
            assert isinstance(result, MutantResult)
            self._publish(results, item.mutant, result)
        return results

    def verify_full(self, mutant: Mutant) -> MutantResult:
        """Re-run a survivor against every test, nothing left out.

        Arguments:
            mutant: A mutant that survived its selected tests.

        Returns:
            The verdict against the whole suite.
        """
        nodeids = self._selector.all_tests
        outcome = self._attempt(mutant, nodeids, None, 0, self._budget(nodeids))
        verdict = classify(outcome.process, outcome.record, self._baseline.passing)
        return self._result(mutant, verdict, nodeids, 1, outcome.process.duration_s)

    def _publish(
        self, results: dict[str, MutantResult], mutant: Mutant, result: MutantResult
    ) -> None:
        results[mutant.id] = result
        if self._on_result is not None:
            self._on_result(mutant, result)

    def _too_many_expired(self) -> bool:
        with self._lock:
            if self._finished < _THROTTLE_SAMPLE:
                return False
            return self._expired > self._finished * _THROTTLE_RATIO

    def _halve(self, permits: threading.Semaphore, workers: int) -> None:
        kept = max(1, workers // 2)
        self.warnings.append(
            f"more than {_THROTTLE_RATIO:.0%} of the mutants timed out on their "
            f"first attempt; continuing with {kept} worker(s) instead of {workers}"
        )

        def hold() -> None:
            for _ in range(workers - kept):
                permits.acquire()

        threading.Thread(target=hold, name="pytestmut-throttle", daemon=True).start()

    def _next_id(self, prefix: str) -> str:
        with self._lock:
            self._sequence += 1
            return f"{prefix}{self._sequence}"

    def _budget(self, nodeids: Sequence[str]) -> float:
        durations = self._baseline.durations
        estimate = self._baseline.overhead_s + sum(
            durations.get(nodeid, 0.0) for nodeid in nodeids
        )
        budget = self._timeout.factor * estimate + self._timeout.grace_seconds
        return min(max(budget, self._timeout.floor_seconds), self._timeout.max_seconds)

    def _prune(self, nodeids: Sequence[str], full: bool) -> tuple[str, ...] | None:
        if full:
            return None
        return tuple(sorted({nodeid.split("::", 1)[0] for nodeid in nodeids}))

    def _attempt(
        self,
        mutant: Mutant | None,
        nodeids: Sequence[str],
        prune: tuple[str, ...] | None,
        worker: int,
        budget: float,
    ) -> JobOutcome:
        order: tuple[str, ...] | None = None
        if self._reordered:
            durations = self._baseline.durations
            order = tuple(sorted(nodeids, key=lambda n: durations.get(n, 0.0)))
        job = Job(
            id=self._next_id("m" if mutant is not None else "c"),
            mode=MUTATE if mutant is not None else CONTROL,
            mutant=mutant,
            nodeids=tuple(nodeids),
            order=order,
            prune_files=prune,
            stop_on_fail=True,
            timeout_s=budget,
            worker=worker,
        )
        return self._runner.run(job, self._cancel)

    def _control_passes(
        self,
        nodeids: Sequence[str],
        prune: tuple[str, ...] | None,
        worker: int,
        budget: float,
        within: float | None = None,
    ) -> bool:
        outcome = self._attempt(None, nodeids, prune, worker, budget)
        verdict = classify(
            outcome.process, outcome.record, self._baseline.passing, mutated=False
        )
        if verdict.status is not Status.SURVIVED:
            return False
        return within is None or outcome.process.duration_s <= within

    def _passes_alone(self, nodeid: str, worker: int) -> bool:
        with self._lock:
            known = self._alone.get(nodeid)
        if known is not None:
            return known
        passes = self._control_passes(
            (nodeid,), self._prune((nodeid,), False), worker, self._budget((nodeid,))
        )
        with self._lock:
            self._alone[nodeid] = passes
            if not passes:
                self.order_dependent.add(nodeid)
        return passes

    def _is_serial(self, nodeids: Sequence[str]) -> bool:
        globs = self._config.serial_tests
        return bool(globs) and any(
            fnmatch.fnmatchcase(nodeid, glob) for nodeid in nodeids for glob in globs
        )

    def _guarded(
        self, mutant: Mutant, worker: int, solo: bool, attempts: int = 0
    ) -> MutantResult | _Deferred:
        started = time.monotonic()
        try:
            item = self._process(mutant, worker, solo, attempts)
        except Exception as exc:  # noqa: BLE001 - one mutant must not end the run
            item = MutantResult(
                mutant_id=mutant.id,
                status=Status.ERROR,
                reason="internal",
                detail=f"{type(exc).__name__}: {exc}",
                duration_s=time.monotonic() - started,
            )
        if isinstance(item, MutantResult):
            with self._lock:
                self._finished += 1
        return item

    def _process(
        self, mutant: Mutant, worker: int, solo: bool, attempts: int
    ) -> MutantResult | _Deferred:
        started = time.monotonic()
        past_deadline = self._deadline is not None and started > self._deadline
        if self._cancel.is_set() or (
            past_deadline and mutant.id not in self._protected
        ):
            reason = "cancelled" if self._cancel.is_set() else BUDGET_EXHAUSTED
            return MutantResult(mutant.id, Status.NOT_RUN, reason)
        exclude: frozenset[str] = frozenset()
        retried = False
        verdict = Verdict(Status.ERROR, "unsettled")
        selection = Selection()
        hint = self._hints.get(mutant.id)
        for _ in range(_MAX_ROUNDS + 1):
            selection = self._selector.select(mutant, exclude)
            if selection.status is not None:
                return MutantResult(mutant.id, selection.status, selection.reason)
            hinted = hint is not None and hint in selection.nodeids
            if hinted and hint is not None:
                # Cheap re-validation of a cached kill: its killing test alone.
                selection = Selection(nodeids=(hint,))
            hint = None
            nodeids = selection.nodeids
            if not solo and self._is_serial(nodeids):
                return _Deferred(mutant, attempts)
            prune = self._prune(nodeids, selection.full)
            budget = self._budget(nodeids)
            if solo:
                budget = min(
                    budget * self._timeout.retry_factor,
                    self._timeout.max_seconds * self._timeout.retry_factor,
                )
            outcome = self._attempt(mutant, nodeids, prune, worker, budget)
            attempts += 1
            verdict = classify(outcome.process, outcome.record, self._baseline.passing)
            if hinted and verdict.status is not Status.KILLED:
                continue
            if verdict.confirm == CONFIRM_RETRY and not retried:
                retried = True
                continue
            if verdict.confirm == CONFIRM_SOLO and not solo:
                if verdict.status is Status.TIMEOUT:
                    with self._lock:
                        self._expired += 1
                return _Deferred(mutant, attempts)
            if verdict.status is Status.TIMEOUT:
                attempts += 1
                if not self._control_passes(
                    nodeids, prune, worker, budget, within=budget / 2
                ):
                    verdict = Verdict(
                        Status.ERROR,
                        "budget-unsound",
                        tests_run=verdict.tests_run,
                        detail="the same tests are too slow without the mutant to "
                        "tell a timeout from a slow machine",
                    )
            elif verdict.confirm == CONFIRM_CONTROL:
                attempts += 1
                if not self._control_passes(nodeids, prune, worker, budget):
                    verdict = Verdict(
                        Status.ERROR,
                        "control-failed",
                        detail="the same tests also fail to start without the "
                        "mutant: " + verdict.detail,
                    )
            elif (
                verdict.status is Status.KILLED
                and verdict.killing_test is not None
                and (not selection.full or self._reordered)
                and not self._passes_alone(verdict.killing_test, worker)
            ):
                attempts += 2
                if not self._control_passes(nodeids, prune, worker, budget):
                    exclude = exclude | {verdict.killing_test}
                    continue
            break
        result = self._result(
            mutant,
            verdict,
            selection.nodeids,
            attempts,
            time.monotonic() - started,
        )
        return replace(result, from_cache=hinted and verdict.status is Status.KILLED)

    @staticmethod
    def _result(
        mutant: Mutant,
        verdict: Verdict,
        selected: Sequence[str],
        attempts: int,
        seconds: float,
    ) -> MutantResult:
        return MutantResult(
            mutant_id=mutant.id,
            status=verdict.status,
            reason=verdict.reason,
            killing_test=verdict.killing_test,
            kill_phase=verdict.kill_phase,
            tests_selected=len(selected),
            tests_run=verdict.tests_run,
            duration_s=seconds,
            attempts=attempts,
            detail=verdict.detail,
            tests=tuple(selected[:LISTED_TESTS]),
        )
