"""Run the unmutated suite and decide whether it can serve as a baseline.

The baseline runs in exactly the configuration mutants will run in: same
command line, same scrubbed environment, same import hook (serving every
target file unmodified) and, when workers are parallel, the same concurrency.
A suite that cannot pass this way would turn its own failures into "kills".
"""

from __future__ import annotations

import fnmatch
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pytestmut.config.model import RunConfig
from pytestmut.exceptions import BaselineError
from pytestmut.exec.jobs import OBSERVE, Job, TargetIdentity
from pytestmut.exec.runner import JobOutcome, JobRunner

BASELINE_TIMEOUT_S = 3600.0


@dataclass(frozen=True)
class Baseline:
    """What the baseline established.

    Attributes:
        nodeids: Stable test ids in collection order.
        durations: Seconds per test, the slowest sample across runs.
        passing: Tests with no failed phase in any run.
        quarantined: Tests excluded from kill credit, with the reason.
        overhead_s: Process time outside the tests, the slowest sample.
        wall_s: Wall-clock time of the slowest baseline run.
        loaded: Relative paths of targets the tests imported.
        unsupported: Targets loaded by a mechanism the hook cannot reach,
            mapped to the loader that was used.
        peak_rss: Largest memory use seen for a baseline process tree.
        workers: Worker count the suite proved safe for.
        warnings: Things the user should know.
    """

    nodeids: tuple[str, ...]
    durations: dict[str, float]
    passing: frozenset[str]
    quarantined: dict[str, str] = field(default_factory=dict)
    overhead_s: float = 0.0
    wall_s: float = 0.0
    loaded: frozenset[str] = frozenset()
    unsupported: dict[str, str] = field(default_factory=dict)
    peak_rss: int | None = None
    workers: int = 1
    warnings: tuple[str, ...] = ()


def _failed_tests(outcome: JobOutcome) -> set[str]:
    return {
        phase.nodeid for phase in outcome.record.phases if phase.outcome == "failed"
    }


def _tail(text: str, limit: int = 3000) -> str:
    stripped = text.strip()
    return stripped if len(stripped) <= limit else "..." + stripped[-limit:]


class BaselineRunner:
    """Runs and validates the baseline.

    Arguments:
        runner: Runs the baseline jobs.
        targets: Every target file of the run.
        config: Scheduling settings.
        scrubbed: Environment variable names removed for the test processes,
            named in failure messages because a missing one is a likely cause.
    """

    def __init__(
        self,
        runner: JobRunner,
        targets: tuple[TargetIdentity, ...],
        config: RunConfig,
        scrubbed: tuple[str, ...],
    ) -> None:
        self._runner = runner
        self._targets = targets
        self._config = config
        self._scrubbed = scrubbed

    def solo(self, cancel: threading.Event) -> JobOutcome:
        """Run the suite once, alone, and require it to be healthy.

        This run comes first for two reasons: a broken suite is reported
        after one run instead of after one per worker, and its peak memory
        decides how many copies may run at once.

        Arguments:
            cancel: Set from another thread to abort.

        Returns:
            The outcome of the run, including its peak memory.

        Raises:
            BaselineError: If the suite cannot serve as a baseline.
        """
        outcome = self._concurrent(1, cancel, "s")[0]
        self._require_healthy(outcome)
        return outcome

    def run(self, workers: int, cancel: threading.Event, solo: JobOutcome) -> Baseline:
        """Run the baseline at the planned concurrency.

        Arguments:
            workers: Number of concurrent baseline runs, equal to the number
                of workers planned for mutants.
            cancel: Set from another thread to abort.
            solo: The healthy solo run made beforehand.

        Returns:
            The validated baseline.

        Raises:
            BaselineError: If the suite is flaky, or fails under concurrency
                and the configuration says to abort, or imports an installed
                copy of the code instead of the files being mutated.
        """
        warnings: list[str] = []
        if workers <= 1:
            return self._summarise([solo], 1, warnings)
        outcomes = self._concurrent(workers, cancel, "b")
        if any(self._problem(outcome) for outcome in outcomes):
            if self._config.on_parallel_failure == "abort":
                raise BaselineError(
                    f"the test suite passes alone but fails when {workers} copies "
                    "run at once; it shares a port, file or database. Fix that, "
                    "set run.workers = 1, or set run.on_parallel_failure = "
                    '"serial".\n' + self._describe(outcomes)
                )
            warnings.append(
                f"the test suite fails when {workers} copies run at once, so "
                "mutants run one at a time; " + self._describe(outcomes)
            )
            return self._summarise([solo], 1, warnings)
        return self._summarise([solo, *outcomes], workers, warnings)

    def _concurrent(
        self, count: int, cancel: threading.Event, prefix: str
    ) -> list[JobOutcome]:
        jobs = [
            Job(
                id=f"{prefix}{index}",
                mode=OBSERVE,
                stop_on_fail=False,
                timeout_s=BASELINE_TIMEOUT_S,
                worker=index,
            )
            for index in range(count)
        ]
        if count == 1:
            return [self._runner.run(jobs[0], cancel)]
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(lambda job: self._runner.run(job, cancel), jobs))

    def _problem(self, outcome: JobOutcome) -> str:
        process, record = outcome.process, outcome.record
        if process.cancelled:
            return "cancelled"
        if process.timed_out:
            return f"did not finish within {BASELINE_TIMEOUT_S:.0f}s"
        if process.oom:
            return (
                "one run of the suite needs more memory than allowed; raise "
                "safety.memory_budget or safety.memory_limit"
            )
        if record.unsafe_options:
            return "unsafe options"
        if not record.configured or not record.finished:
            return "pytest did not run to completion"
        if record.collect_errors:
            return "collection errors"
        if record.exitstatus not in (0, 1, 5):
            return f"pytest exited with status {record.exitstatus}"
        if record.exitstatus == 5 or not record.collected:
            return "no tests were collected"
        unexpected = _failed_tests(outcome) - self._listed(_failed_tests(outcome))
        if unexpected and self._config.on_baseline_failure == "abort":
            return "tests failed"
        return ""

    def _listed(self, nodeids: set[str]) -> set[str]:
        """Tests the configuration quarantines by name."""
        globs = self._config.quarantine
        return {
            nodeid
            for nodeid in nodeids
            if any(fnmatch.fnmatchcase(nodeid, glob) for glob in globs)
        }

    def _require_healthy(self, outcome: JobOutcome) -> None:
        problem = self._problem(outcome)
        if not problem:
            return
        record = outcome.record
        lines = [f"the unmutated test suite cannot serve as a baseline: {problem}"]
        if record.unsafe_options:
            flags = ", ".join(record.unsafe_options)
            lines.append(
                f"pytest is running with {flags}; remove it from addopts or list "
                "it in pytest.strip_flags"
            )
        failed = sorted(_failed_tests(outcome))
        if failed:
            lines.append("failing tests: " + ", ".join(failed[:10]))
        for nodeid, message in record.collect_errors[:3]:
            lines.append(f"collection error in {nodeid}: {message.strip()[-400:]}")
        if record.boot_errors:
            lines.extend(record.boot_errors[:3])
        lines.extend(message.strip()[-600:] for message in record.internal_errors[:1])
        if self._scrubbed:
            lines.append(
                "these environment variables are withheld from the tests: "
                + ", ".join(self._scrubbed)
                + " (allow one with safety.env_keep)"
            )
        lines.append(_tail(outcome.process.output))
        raise BaselineError("\n".join(line for line in lines if line))

    def _describe(self, outcomes: list[JobOutcome]) -> str:
        failed: set[str] = set()
        for outcome in outcomes:
            failed |= _failed_tests(outcome)
        return "failing under concurrency: " + ", ".join(sorted(failed)[:10])

    def _summarise(
        self, outcomes: list[JobOutcome], workers: int, warnings: list[str]
    ) -> Baseline:
        first = outcomes[0].record
        collected = [set(outcome.record.collected or ()) for outcome in outcomes]
        stable = set.intersection(*collected)
        quarantined: dict[str, str] = {}
        for nodeid in sorted(set.union(*collected) - stable):
            quarantined[nodeid] = "test id differs between runs"
        failing: set[str] = set()
        for outcome in outcomes:
            failing |= _failed_tests(outcome)
        listed = self._listed(set.union(*collected))
        flaky = {
            nodeid
            for nodeid in failing - listed
            if not all(nodeid in _failed_tests(outcome) for outcome in outcomes)
        }
        if flaky and self._config.on_baseline_failure == "abort":
            raise BaselineError(
                "flaky tests: these failed in some baseline runs and passed in "
                "others: " + ", ".join(sorted(flaky)[:10]) + ". Fix them, list "
                'them in run.quarantine, or set run.on_baseline_failure = "quarantine".'
            )
        for nodeid in sorted(failing):
            quarantined[nodeid] = (
                "flaky in the baseline" if nodeid in flaky else "fails in the baseline"
            )
        for nodeid in sorted(listed):
            quarantined[nodeid] = "listed in run.quarantine"
        nodeids = tuple(n for n in (first.collected or ()) if n in stable)
        durations: dict[str, float] = {}
        overhead = wall = 0.0
        peak = 0
        for outcome in outcomes:
            spent: dict[str, float] = {}
            for phase in outcome.record.phases:
                spent[phase.nodeid] = spent.get(phase.nodeid, 0.0) + phase.duration
            for nodeid, seconds in spent.items():
                durations[nodeid] = max(durations.get(nodeid, 0.0), seconds)
            wall = max(wall, outcome.process.duration_s)
            overhead = max(overhead, outcome.process.duration_s - sum(spent.values()))
            peak = max(peak, outcome.process.peak_rss or 0)
        loaded, unsupported = self._audit(outcomes)
        if quarantined:
            warnings.append(
                f"{len(quarantined)} test(s) are quarantined and cannot kill mutants"
            )
        return Baseline(
            nodeids=nodeids,
            durations=durations,
            passing=frozenset(set(nodeids) - set(quarantined)),
            quarantined=quarantined,
            overhead_s=max(overhead, 0.0),
            wall_s=wall,
            loaded=loaded,
            unsupported=unsupported,
            peak_rss=peak or None,
            workers=workers,
            warnings=tuple(warnings),
        )

    def _audit(
        self, outcomes: list[JobOutcome]
    ) -> tuple[frozenset[str], dict[str, str]]:
        """Check that every imported target went through the hook."""
        by_path = {target.path: target.relpath for target in self._targets}
        through_hook: set[str] = set()
        seen: dict[str, str] = {}
        shadows: dict[str, str] = {}
        for outcome in outcomes:
            for load in outcome.record.loads:
                if load.get("path") in by_path:
                    through_hook.add(by_path[load["path"]])
            for module in outcome.record.modules_seen:
                if module.get("path") in by_path:
                    seen[by_path[module["path"]]] = module.get("loader", "")
            for shadow in outcome.record.shadows:
                shadows[shadow.get("name", "")] = shadow.get("file", "")
        unsupported = {
            relpath: loader
            for relpath, loader in seen.items()
            if relpath not in through_hook
        }
        shadowed = {
            name: origin
            for name, origin in shadows.items()
            if not any(
                target.module == name and target.relpath in through_hook
                for target in self._targets
            )
        }
        if shadowed:
            listing = "\n".join(
                f"  {name} was imported from {origin}"
                for name, origin in sorted(shadowed.items())
            )
            raise BaselineError(
                "the tests import an installed copy of the code instead of the "
                "files being mutated, so no mutant would ever be active:\n"
                f"{listing}\nInstall the project in editable mode "
                "(uv sync, or pip install -e .) and run again."
            )
        return frozenset(through_hook), unsupported
