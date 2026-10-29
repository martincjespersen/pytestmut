"""The run pipeline, from configuration to exit code.

discover -> probe -> generate -> scope -> baseline -> trace -> self-check
-> execute -> verify -> post-checks -> evaluate.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import hashlib
import json
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pytestmut._meta import __version__
from pytestmut.baseline.runner import Baseline, BaselineRunner
from pytestmut.baseline.selfcheck import run_self_check
from pytestmut.baseline.testmap import Selector, TestMap
from pytestmut.cache.fingerprint import FileHashes, environment_fingerprint
from pytestmut.cache.store import VerdictCache
from pytestmut.config.loader import parse_size
from pytestmut.config.model import CacheConfig, Config, ReportConfig
from pytestmut.exceptions import (
    BaselineError,
    ConfigError,
    EmptyScopeError,
    PytestmutError,
    ToolFailure,
)
from pytestmut.exec.cpu import (
    children_peak_rss,
    cpu_workers,
    format_size,
    memory_budget,
    plan_memory,
)
from pytestmut.exec.environment import WorkerEnvironment, build_environment
from pytestmut.exec.jobs import TRACE, Job, TargetIdentity, identify
from pytestmut.exec.runner import JobOutcome, PytestJobRunner
from pytestmut.exec.scheduler import BUDGET_EXHAUSTED, MutantExecutor
from pytestmut.exec.supervisor import LocalSupervisor, ProcessSupervisor
from pytestmut.mutation.generator import FileMutants, Generator, Issue
from pytestmut.mutation.model import Mutant, MutantResult, Status
from pytestmut.policy.evaluate import evaluate_policy
from pytestmut.policy.matcher import PolicyMatcher
from pytestmut.pytest_integration.probe import (
    PytestEnvironment,
    probe_environment,
    run_probe,
)
from pytestmut.report.guidance import guidance
from pytestmut.report.model import (
    BaselineSummary,
    FileReport,
    Provenance,
    RunResult,
)
from pytestmut.source.discovery import Discovery, discover_targets
from pytestmut.state.statedir import RunDir, StateDir
from pytestmut.vcs.diffscope import DiffScope, scope_to_diff
from pytestmut.vcs.git import Git, TreeSnapshot

Notify = Callable[[str], None]
Progress = Callable[[int, int, Mutant, MutantResult], None]


@dataclass(frozen=True)
class RunOptions:
    """Per-invocation choices that are not configuration.

    Attributes:
        staged: Scope the run to lines staged in the git index.
        hook: Running as a pre-commit hook; implies diff limits.
        only: Mutant id prefixes or path globs to restrict the run to.
        keep_jobs: Keep per-job directories for debugging.
        reached_only: Keep only mutants in code the selected tests execute,
            so a narrowed set of tests is judged on what it exercises.
        time_limit: Seconds from the start after which no new mutant starts.
    """

    staged: bool = False
    hook: bool = False
    only: tuple[str, ...] = ()
    keep_jobs: bool = False
    reached_only: bool = False
    time_limit: float | None = None


def _digest(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def _test_files(rootdir: Path, nodeids: Iterable[str]) -> list[Path]:
    """The files holding these tests, and the conftest files above them."""
    files: set[Path] = set()
    for nodeid in nodeids:
        path = rootdir / nodeid.split("::", 1)[0]
        files.add(path)
        for folder in path.parents:
            files.add(folder / "conftest.py")
            if folder == rootdir:
                break
    return sorted(path for path in files if path.is_file())


def _canonical(value: Any) -> Any:
    """A JSON-ready form that is the same in every process.

    ``repr`` would not do: the order of a set of strings changes with each
    interpreter's hash seed.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _relocated(config: Config) -> Config:
    """The configuration as far as it can change a verdict.

    Paths are made relative to the project, so two clones agree, and the
    cache and report settings are reset: they decide what is reused and
    written, not what a mutant's tests do.
    """
    source = config.config_file
    if source is not None and source.is_absolute():
        try:
            source = source.relative_to(config.root)
        except ValueError:
            source = Path(source.name)
    return replace(
        config,
        root=Path("."),
        config_file=source,
        cache=CacheConfig(),
        report=ReportConfig(),
    )


def _provenance(
    config: Config,
    run_id: str,
    git: Git,
    probe: PytestEnvironment,
    files: list[FileMutants],
    baseline: Baseline | None,
) -> Provenance:
    """Record what produced this run, to compare it with others later."""
    hashes = FileHashes()
    commit, dirty = git.head()
    tests = ""
    if baseline is not None:
        rootdir = probe.rootdir
        tests = _digest(
            path.relative_to(rootdir).as_posix() + hashes.of(path)
            for path in _test_files(rootdir, baseline.nodeids)
        )
    return Provenance(
        run_id=run_id,
        commit=commit,
        dirty=dirty,
        sources=_digest(
            f"{item.relpath}{item.sha256}"
            for item in sorted(files, key=lambda item: item.relpath)
        ),
        tests=tests,
        # The project's location is left out, so two clones agree.
        config=_digest(
            [json.dumps(_canonical(_relocated(config)), sort_keys=True, default=str)]
        ),
        environment=environment_fingerprint(config, probe, hashes),
    )


def _silent(message: str) -> None:
    """Default notifier: say nothing."""


class Orchestrator:
    """Runs mutation testing for one project.

    Arguments:
        config: The validated configuration.
        supervisor: Starts and contains test processes.
        notify: Receives one-line progress messages.
        progress: Receives each mutant's verdict as it arrives.
    """

    def __init__(
        self,
        config: Config,
        supervisor: ProcessSupervisor | None = None,
        notify: Notify = _silent,
        progress: Progress | None = None,
    ) -> None:
        self._config = config
        self._supervisor = supervisor or LocalSupervisor()
        self._notify = notify
        self._progress = progress
        self._cancel = threading.Event()
        self._state = StateDir(config.root, config.state_dir)

    @property
    def state(self) -> StateDir:
        """The state directory of the project."""
        return self._state

    def cancel(self) -> None:
        """Ask a running ``run`` to stop as soon as it can."""
        self._cancel.set()

    def run(self, options: RunOptions = RunOptions()) -> RunResult:
        """Run the whole pipeline.

        Arguments:
            options: Per-invocation choices.

        Returns:
            The result, including the exit code the process should use.

        Raises:
            ConfigError: For invalid configuration, including a policy that
                matches no code.
            BaselineError: When the unmutated suite cannot be a baseline.
            ToolFailure: When the tool cannot trust its own results.
            EmptyScopeError: When nothing is left to mutate.
        """
        if not self._config.paths:
            raise ConfigError(
                "nothing to mutate: set paths in [tool.pytestmut] or pass them, "
                "e.g. 'pytestmut run src/mypackage'"
            )
        self._state.ensure()
        self._state.lock()
        run_dir: RunDir | None = None
        try:
            run_dir = self._state.new_run(self._config.safety.tmp_root)
            return self._run(options, run_dir)
        finally:
            if run_dir is not None and not options.keep_jobs:
                self._state.remove_scratch(run_dir)
            self._state.unlock()

    def _run(self, options: RunOptions, run_dir: RunDir) -> RunResult:
        config = self._config
        started = time.monotonic()
        started_at = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        )
        git = Git(config.root)
        before = git.snapshot() if config.safety.check_worktree else None
        environment = build_environment(
            os.environ,
            config.safety,
            run_dir.run_id,
            self._state.pycache_prefix(sys.implementation.cache_tag),
            run_dir.path / "void",
        )
        self._notify("checking the pytest setup")
        probe = run_probe(
            config.root,
            config.pytest.args,
            probe_environment(environment.base),
            run_dir.path / "probe",
        )
        discovery = discover_targets(
            config.root,
            config.paths,
            config.exclude,
            probe.python_files,
            probe.testpaths,
            self._state.path,
        )
        self._notify(f"generating mutants for {len(discovery.targets)} file(s)")
        files = self._generate(discovery)
        self._check_generation(files, options)
        mutants = [mutant for item in files for mutant in item.mutants]
        scope = scope_to_diff(config, git, mutants, options.staged, options.hook)
        mutants = self._restrict(scope.mutants, options.only)
        runnable = [mutant for mutant in mutants if mutant.runnable]
        if not runnable and (scope.is_diff or not config.fail_on_empty):
            return self._finish(
                files,
                mutants,
                {},
                None,
                scope,
                started,
                started_at,
                probe,
                (),
                (),
                provenance=_provenance(config, run_dir.run_id, git, probe, files, None),
            )
        if not runnable:
            raise EmptyScopeError(
                "no runnable mutants: every candidate was suppressed, invalid or "
                "equivalent, or the paths contain no mutable code"
            )
        targets = self._identify(discovery, files)
        # More workers than mutants would only make the baseline heavier.
        wanted = min(cpu_workers(config.run.workers), len(runnable))
        budget = memory_budget(
            parse_size(config.safety.memory_budget, "safety.memory_budget")
        )
        runner = self._runner(run_dir, environment, probe, targets, options)
        baselines = BaselineRunner(runner, targets, config.run, environment.scrubbed)
        # The first run may use the whole budget and no more; what it needs
        # decides how many copies may run side by side afterwards.
        runner.set_memory_limit(budget)
        self._notify("running the test suite once to measure it")
        solo = baselines.solo(self._cancel)
        self._raise_if_cancelled()
        # Sampling can miss the peak of a short run; the kernel's high-water
        # mark for finished children cannot.
        measured = max(solo.process.peak_rss or 0, children_peak_rss() or 0) or None
        plan = plan_memory(
            budget,
            wanted,
            measured,
            parse_size(config.safety.memory_limit, "safety.memory_limit"),
        )
        runner.set_memory_limit(plan.limit)
        self._notify(
            f"memory: {format_size(measured)} per run measured, "
            f"budget {format_size(plan.budget)}, limit {format_size(plan.limit)} "
            f"per worker, {plan.workers} worker(s)"
        )
        if plan.workers > 1:
            self._notify(f"running the baseline {plan.workers} times at once")
        baseline = baselines.run(plan.workers, self._cancel, solo)
        self._raise_if_cancelled()
        workers = min(baseline.workers, plan.workers)
        memory_notes: list[str] = []
        if plan.reduced:
            memory_notes.append(
                f"the memory budget of {format_size(plan.budget)} limits the run "
                f"to {plan.workers} worker(s) instead of {wanted}; one run of the "
                f"suite uses {format_size(measured)}"
            )
        if plan.over_budget:
            memory_notes.append(
                "one run of the suite needs more than the memory budget of "
                f"{format_size(plan.budget)}; raise safety.memory_budget"
            )
        traced, baseline = self._trace(
            runner, baseline, targets, force=options.reached_only
        )
        test_map = traced if config.run.selection == "coverage" else None
        if options.reached_only and traced is not None:
            mutants = self._reached(mutants, traced, targets)
            runnable = [mutant for mutant in mutants if mutant.runnable]
            reached = "code reached by the selected tests"
            scope = replace(
                scope,
                mutants=mutants,
                description=(
                    reached if not scope.is_diff else f"{scope.description}, {reached}"
                ),
            )
            self._notify(f"{len(runnable)} mutant(s) are in code the tests reach")
            if not runnable:
                raise EmptyScopeError(
                    "the selected tests reach no mutable code in the configured "
                    "paths; check --tests and the paths being mutated"
                )
        selector = Selector(baseline, targets, test_map)
        full_budget = self._full_budget(baseline)
        if config.run.self_check:
            self._notify("self-check: a mutant that must be killed")
            run_self_check(runner, baseline, targets, full_budget, self._cancel)
        self._raise_if_cancelled()
        cache = VerdictCache.open(
            self._state, config, probe, discovery, targets, baseline
        )
        results, pending = cache.split(runnable, selector)
        done = len(results)
        total = len(runnable)
        lock = threading.Lock()

        tainted: list[str] = []
        last_check = time.monotonic()

        def on_result(mutant: Mutant, result: MutantResult) -> None:
            nonlocal done, last_check
            with lock:
                done += 1
                cache.record(mutant, result, selector)
                if self._progress is not None:
                    self._progress(done, total, mutant, result)
                # A test that writes into the repository under a mutant can be
                # overwritten by the next one, so look while the run is going.
                now = time.monotonic()
                if before is not None and not tainted and now - last_check >= 1.0:
                    last_check = now
                    current = git.snapshot()
                    if current is not None:
                        tainted.extend(current.changed_since(before))

        deadline = scope.deadline(started)
        if options.time_limit is not None:
            limit = started + options.time_limit
            deadline = limit if deadline is None else min(deadline, limit)
        executor = MutantExecutor(
            runner,
            baseline,
            selector,
            config.timeout,
            config.run,
            self._cancel,
            on_result=on_result,
            deadline=deadline,
            protected=scope.protected,
            hints=cache.hints,
        )
        self._notify(
            f"running {len(pending)} mutant(s) on {workers} worker(s)"
            + (f", {len(results)} reused from cache" if results else "")
        )
        results.update(executor.execute(pending, workers))
        cache.close()
        self._raise_if_cancelled()
        selection_notes = self._verify_survivors(executor, runnable, results, selector)
        failures = self._post_checks(git, before, files, tainted)
        warnings = [
            *baseline.warnings,
            *memory_notes,
            *executor.warnings,
            *selection_notes,
        ]
        if executor.order_dependent:
            warnings.append(
                "these tests fail when run without the rest of the suite and "
                "could not be trusted to kill mutants on their own: "
                + ", ".join(sorted(executor.order_dependent))
            )
        if environment.scrubbed:
            warnings.append(
                "withheld from the tests: " + ", ".join(environment.scrubbed)
            )
        return self._finish(
            files,
            mutants,
            results,
            baseline,
            scope,
            started,
            started_at,
            probe,
            tuple(warnings),
            tuple(failures),
            workers=workers,
            test_map=test_map,
            provenance=_provenance(config, run_dir.run_id, git, probe, files, baseline),
        )

    def _raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise KeyboardInterrupt

    def _generate(self, discovery: Discovery) -> list[FileMutants]:
        generator = Generator(self._config, PolicyMatcher(self._config))
        return [generator.generate(target) for target in discovery.targets]

    def _check_generation(self, files: list[FileMutants], options: RunOptions) -> None:
        fatal = [
            f"{issue.path}:{issue.line}: {issue.message}"
            for item in files
            for issue in item.issues
            if issue.fatal
        ]
        if fatal:
            raise ConfigError("invalid directives in source:\n" + "\n".join(fatal))
        used = {mutant.policy for item in files for mutant in item.mutants}
        for policy in self._config.policies:
            if policy.name not in used and not policy.allow_empty:
                raise ConfigError(
                    f"policy {policy.name!r} matches no code; fix its paths or "
                    "symbols, or set allow_empty = true"
                )

    @staticmethod
    def _restrict(mutants: list[Mutant], only: tuple[str, ...]) -> list[Mutant]:
        if not only:
            return mutants
        from pytestmut.source.discovery import path_matches

        return [
            mutant
            for mutant in mutants
            if any(mutant.id.startswith(item) for item in only)
            or path_matches(mutant.path, only)
        ]

    @staticmethod
    def _identify(
        discovery: Discovery, files: list[FileMutants]
    ) -> tuple[TargetIdentity, ...]:
        digests = {item.relpath: item.sha256 for item in files if not item.error}
        targets = [target for target in discovery.targets if target.relpath in digests]
        return tuple(
            identify(index, target.relpath, target.path, digests[target.relpath])
            for index, target in enumerate(targets)
        )

    def _runner(
        self,
        run_dir: RunDir,
        environment: WorkerEnvironment,
        probe: PytestEnvironment,
        targets: tuple[TargetIdentity, ...],
        options: RunOptions,
    ) -> PytestJobRunner:
        own_group = bool(getattr(self._supervisor, "own_group", False))
        return PytestJobRunner(
            supervisor=self._supervisor,
            state=self._state,
            run_dir=run_dir,
            environment=environment,
            probe=probe,
            pytest_config=self._config.pytest,
            targets=targets,
            project_root=self._config.root,
            memory_limit=None,
            output_cap=self._config.safety.max_output_bytes,
            own_group=own_group,
            keep_jobs=options.keep_jobs,
        )

    def _full_budget(self, baseline: Baseline) -> float:
        timeout = self._config.timeout
        budget = timeout.factor * baseline.wall_s + timeout.grace_seconds
        return min(max(budget, timeout.floor_seconds), timeout.max_seconds)

    @staticmethod
    def _reached(
        mutants: list[Mutant], traced: TestMap, targets: tuple[TargetIdentity, ...]
    ) -> list[Mutant]:
        """Mutants in code the traced tests execute.

        Import-time and memoised code is kept for every file the tests
        execute a line of, since tests depend on it without the trace showing
        it per line. Files loaded by a test's child process are kept whole.
        """
        index = {target.relpath: target.index for target in targets}
        touched: set[int] = set()
        for files in traced.files_by_test.values():
            touched |= files
        kept: list[Mutant] = []
        for mutant in mutants:
            position = index.get(mutant.path)
            if position is None or position not in touched:
                continue
            if (
                mutant.static
                or mutant.memoised
                or position in traced.by_file
                or traced.tests_for(position, *mutant.cover_lines)
            ):
                kept.append(mutant)
        return kept

    def _trace(
        self,
        runner: PytestJobRunner,
        baseline: Baseline,
        targets: tuple[TargetIdentity, ...],
        force: bool = False,
    ) -> tuple[TestMap | None, Baseline]:
        """Run the traced baseline that maps tests to the lines they reach.

        Arguments:
            runner: Runs the trace job.
            baseline: The validated baseline.
            targets: Every target file of the run.
            force: Trace even when test selection does not use the map.

        Returns:
            The line map, or None when no trace was needed, and the baseline
            with tests whose ids changed between runs quarantined.
        """
        if self._config.run.selection != "coverage" and not force:
            return None, baseline
        self._notify("mapping tests to the lines they execute")
        job = Job(
            id="trace",
            mode=TRACE,
            stop_on_fail=False,
            timeout_s=max(3600.0, 20 * baseline.wall_s),
        )
        outcome = runner.run(job, self._cancel)
        record = outcome.record
        failed = sorted(
            {phase.nodeid for phase in record.phases if phase.outcome == "failed"}
        )
        new_failures = [n for n in failed if n not in baseline.quarantined]
        clean = record.exitstatus == 0 or (record.exitstatus == 1 and not new_failures)
        if outcome.trace is None or not clean or outcome.process.timed_out:
            failed = new_failures
            raise BaselineError(
                "the test suite passes normally but fails while being traced"
                + (f": {', '.join(failed[:5])}" if failed else "")
                + '. Set run.selection = "all" to run without tracing.\n'
                + outcome.process.output.strip()[-2000:]
            )
        stable = set(record.collected or ())
        unstable = sorted(set(baseline.nodeids) - stable)
        quarantined = dict(baseline.quarantined)
        for nodeid in unstable:
            quarantined[nodeid] = "test id differs between runs"
        baseline = replace(
            baseline,
            nodeids=tuple(n for n in baseline.nodeids if n in stable),
            passing=baseline.passing & stable,
            quarantined=quarantined,
        )
        test_map = TestMap.from_trace(
            outcome.trace, self._child_loads(outcome, targets)
        )
        traced = {
            context[2:]
            for context in outcome.trace.get("contexts", {})
            if context[:2] == "t:"
        }
        ran = {phase.nodeid for phase in record.phases if phase.when == "call"}
        missing = ran - traced
        if missing:
            raise ToolFailure(
                f"{len(missing)} test(s) ran without being traced, so tests could "
                'not be matched to mutants. Set run.selection = "all".'
            )
        return test_map, baseline

    @staticmethod
    def _child_loads(
        outcome: JobOutcome, targets: tuple[TargetIdentity, ...]
    ) -> dict[int, set[str]]:
        by_path = {target.path: target.index for target in targets}
        loads: dict[int, set[str]] = {}
        for load in outcome.record.loads:
            if load.get("root") or load.get("path") not in by_path:
                continue
            test = str(load.get("test", "")).rsplit(" (", 1)[0]
            if test:
                loads.setdefault(by_path[load["path"]], set()).add(test)
        return loads

    def _verify_survivors(
        self,
        executor: MutantExecutor,
        runnable: list[Mutant],
        results: dict[str, MutantResult],
        selector: Selector,
    ) -> list[str]:
        """Re-run survivors against every test, as a check on test selection.

        A sample is checked first. If any of it turns out to be killable, the
        line map missed tests somewhere, so every other survivor is checked
        too and corrected. The numbers that are reported are then complete,
        and the miss is reported as a warning.

        Returns:
            Warnings about kills that selection missed.
        """
        config = self._config.run
        if config.verify_survivors <= 0 or config.selection != "coverage":
            return []
        total = len(selector.all_tests)
        survivors = [
            mutant
            for mutant in runnable
            if results.get(mutant.id) is not None
            and results[mutant.id].status is Status.SURVIVED
            and not results[mutant.id].from_cache
            and results[mutant.id].tests_selected < total
        ]
        survivors.sort(key=lambda m: hashlib.sha256(m.id.encode()).hexdigest())
        count = min(
            config.verify_survivors_max,
            max(1, round(len(survivors) * config.verify_survivors)),
        )
        missed = self._recheck(executor, survivors[:count], results)
        if missed:
            rest = survivors[count:]
            if rest:
                self._notify(
                    f"test selection missed a kill; re-checking the other {len(rest)} "
                    "survivor(s) against the full suite"
                )
            missed += self._recheck(executor, rest, results)
        if not missed:
            return []
        listing = ", ".join(
            f"{mutant.path}:{mutant.span.line} [{mutant.id}]" for mutant in missed[:5]
        )
        return [
            f"test selection missed {len(missed)} kill(s) ({listing}); every "
            "survivor was re-run against the full suite, so the results are "
            "complete. Selection is unreliable for this code, typically because "
            'of results cached between tests; run.selection = "all" avoids '
            "the extra re-runs."
        ]

    def _recheck(
        self,
        executor: MutantExecutor,
        survivors: list[Mutant],
        results: dict[str, MutantResult],
    ) -> list[Mutant]:
        """Re-run survivors against the full suite and correct their verdicts."""
        if survivors:
            self._notify(
                f"verifying {len(survivors)} survivor(s) against the full suite"
            )
        missed: list[Mutant] = []
        for mutant in survivors:
            if self._cancel.is_set():
                break
            verdict = executor.verify_full(mutant)
            if verdict.status is Status.KILLED:
                results[mutant.id] = verdict
                missed.append(mutant)
        return missed

    def _post_checks(
        self,
        git: Git,
        before: TreeSnapshot | None,
        files: list[FileMutants],
        tainted: list[str],
    ) -> list[str]:
        failures: list[str] = []
        if before is not None:
            after = git.snapshot()
            changed = sorted(
                set(tainted) | set(after.changed_since(before) if after else ())
            )
            if changed:
                failures.append(
                    "tracked files changed during the run (a test wrote into "
                    "the repository under a mutant?): " + ", ".join(changed[:10])
                )
        for item in files:
            if item.error:
                failures.append(f"{item.relpath}: {item.error}")
                continue
            path = self._config.root / item.relpath
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest = ""
            if digest != item.sha256:
                failures.append(f"{item.relpath} changed while the run was in progress")
        return failures

    def _finish(
        self,
        files: list[FileMutants],
        mutants: list[Mutant],
        results: dict[str, MutantResult],
        baseline: Baseline | None,
        scope: DiffScope,
        started: float,
        started_at: str,
        probe: PytestEnvironment,
        warnings: tuple[str, ...],
        failures: tuple[str, ...],
        workers: int = 1,
        test_map: TestMap | None = None,
        provenance: Provenance | None = None,
    ) -> RunResult:
        config = self._config
        issues: list[Issue] = [issue for item in files for issue in item.issues]
        all_failures = list(failures)
        for item in files:
            if item.error and not any(item.relpath in text for text in all_failures):
                all_failures.append(f"{item.relpath}: {item.error}")
        errors = sum(1 for result in results.values() if result.status is Status.ERROR)
        if errors > config.run.max_errors:
            all_failures.append(
                f"{errors} mutant(s) ended in error, at most "
                f"{config.run.max_errors} allowed; see the Errors section"
            )
        by_id = {mutant.id: mutant for mutant in mutants}
        results = {
            identifier: (
                replace(result, hint=guidance(by_id[identifier], result))
                if identifier in by_id
                else result
            )
            for identifier, result in results.items()
        }
        policies = tuple(
            evaluate_policy(
                policy,
                mutants,
                results,
                refused=[
                    f"{issue.path}:{issue.line}: {issue.message}"
                    for issue in issues
                    if issue.policy == policy.name
                ],
            )
            for policy in config.all_policies
        )
        policies = tuple(
            policy
            for policy in policies
            if policy.total or policy.name == config.default_policy.name
        )
        not_run = any(
            results.get(mutant.id) is not None
            and results[mutant.id].status is Status.NOT_RUN
            for mutant in mutants
        )
        all_warnings = list(warnings) + [
            f"{issue.path}:{issue.line}: {issue.message}"
            for issue in issues
            if not issue.fatal and not issue.policy
        ]
        all_warnings += scope.warnings
        out_of_time = sum(
            1 for result in results.values() if result.reason == BUDGET_EXHAUSTED
        )
        if out_of_time:
            message = f"{out_of_time} mutant(s) were not run: the time "
            if scope.budget_seconds is None:
                # Only --time-limit set a deadline; the report says partial.
                all_warnings.append(message + "limit ran out")
            elif config.diff.on_budget_exceeded == "fail":
                all_failures.append(
                    message + f"budget of {scope.budget_seconds:g}s ran out"
                )
            else:
                all_warnings.append(
                    message + f"budget of {scope.budget_seconds:g}s ran out"
                )
        if all_failures:
            exit_code = ToolFailure.exit_code
        elif any(not policy.passed for policy in policies):
            exit_code = 1
        elif out_of_time and scope.budget_seconds is None:
            # Cut short by --time-limit: mutants that never ran cannot pass.
            exit_code = 1
        else:
            exit_code = 0
        notes = self._file_notes(files, baseline)
        return RunResult(
            tool_version=__version__,
            started_at=started_at,
            duration_s=time.monotonic() - started,
            project_root=str(config.root),
            python=probe.python_version,
            pytest=probe.pytest_version,
            platform=platform.platform(),
            scope=scope.description,
            partial=not_run or scope.partial,
            files=tuple(
                FileReport(
                    path=item.relpath,
                    sha256=item.sha256,
                    error=item.error,
                    note=notes.get(item.relpath, ""),
                    empty_scopes=item.empty_scopes,
                )
                for item in files
            ),
            mutants=tuple(mutants),
            results=results,
            policies=policies,
            baseline=BaselineSummary(
                tests=len(baseline.nodeids) if baseline else 0,
                wall_s=baseline.wall_s if baseline else 0.0,
                workers=workers,
                selection="coverage" if test_map is not None else "all",
                quarantined=dict(baseline.quarantined) if baseline else {},
            ),
            warnings=tuple(all_warnings),
            failures=tuple(all_failures),
            exit_code=exit_code,
            provenance=provenance or Provenance(),
        )

    @staticmethod
    def _file_notes(
        files: list[FileMutants], baseline: Baseline | None
    ) -> dict[str, str]:
        if baseline is None:
            return {}
        notes: dict[str, str] = {}
        for item in files:
            if item.error:
                continue
            if item.relpath in baseline.unsupported:
                loader = baseline.unsupported[item.relpath]
                notes[item.relpath] = f"not mutated: loaded by {loader}"
            elif item.relpath not in baseline.loaded and item.mutants:
                notes[item.relpath] = "never imported by the tests"
        return notes


def exit_code_for(error: PytestmutError) -> int:
    """Exit code for a run that ended in an exception.

    Arguments:
        error: The error that ended the run.

    Returns:
        The process exit code.
    """
    return error.exit_code
