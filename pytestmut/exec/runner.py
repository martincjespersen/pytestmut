"""Run one job: write its files, supervise the process, read what it reported."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pytestmut.config.model import PytestConfig
from pytestmut.exec.environment import WorkerEnvironment
from pytestmut.exec.jobs import Job, RunRecord, TargetIdentity, read_record, write_job
from pytestmut.exec.supervisor import ProcessOutcome, ProcessSpec, ProcessSupervisor
from pytestmut.pytest_integration.cmdline import build_argv
from pytestmut.pytest_integration.probe import PytestEnvironment
from pytestmut.state.statedir import RunDir, StateDir


@dataclass(frozen=True)
class JobOutcome:
    """Everything known about a finished job.

    Attributes:
        job: The job that ran.
        process: How its process ended.
        record: What its processes reported.
        trace: The line trace, for trace jobs.
    """

    job: Job
    process: ProcessOutcome
    record: RunRecord
    trace: dict[str, Any] | None = None


class JobRunner(Protocol):
    """Runs jobs to completion."""

    def run(self, job: Job, cancel: threading.Event) -> JobOutcome:
        """Run one job and collect its evidence.

        Arguments:
            job: The job to run.
            cancel: Set from another thread to abort the run.

        Returns:
            The job's outcome.
        """
        ...


class PytestJobRunner:
    """Runs jobs as pytest subprocesses.

    Arguments:
        supervisor: Starts and contains the processes.
        state: The state directory, used to delete job scratch space.
        run_dir: Directories of the current run.
        environment: Environment shared by all jobs.
        probe: The project's pytest setup.
        pytest_config: pytest settings from the configuration.
        targets: Every target file of the run.
        project_root: Directory the processes start in.
        memory_limit: Bytes a process tree may use, or None for no limit.
        output_cap: Bytes of output kept per process.
        own_group: True when each process leads its own process group.
        keep_jobs: Keep job directories for debugging.
    """

    def __init__(
        self,
        supervisor: ProcessSupervisor,
        state: StateDir,
        run_dir: RunDir,
        environment: WorkerEnvironment,
        probe: PytestEnvironment,
        pytest_config: PytestConfig,
        targets: tuple[TargetIdentity, ...],
        project_root: Path,
        memory_limit: int | None,
        output_cap: int,
        own_group: bool,
        keep_jobs: bool = False,
    ) -> None:
        self._supervisor = supervisor
        self._state = state
        self._run_dir = run_dir
        self._environment = environment
        self._probe = probe
        self._pytest_config = pytest_config
        self._targets = targets
        self._root = project_root
        self._memory_limit = memory_limit
        self._output_cap = output_cap
        self._own_group = own_group
        self._keep_jobs = keep_jobs

    def set_memory_limit(self, limit: int | None) -> None:
        """Change the limit applied to later jobs.

        Arguments:
            limit: Bytes a process tree may use, or None for no limit.
        """
        self._memory_limit = limit

    def run(self, job: Job, cancel: threading.Event) -> JobOutcome:
        """Run one job and collect its evidence.

        Arguments:
            job: The job to run.
            cancel: Set from another thread to abort the run.

        Returns:
            The job's outcome.
        """
        job_dir = self._run_dir.job_dir(job.id)
        write_job(job_dir, job, self._targets, self._run_dir.run_id, self._own_group)
        spec = ProcessSpec(
            argv=build_argv(self._probe, self._pytest_config, job_dir),
            env=self._environment.for_job(job_dir, job.worker),
            cwd=self._root,
            timeout_s=job.timeout_s,
            memory_limit=self._memory_limit,
            output_cap=self._output_cap,
        )
        process = self._supervisor.run(spec, cancel)
        record = read_record(job_dir / "rec")
        trace: dict[str, Any] | None = None
        trace_file = job_dir / "rec" / "trace.json"
        if trace_file.is_file():
            try:
                trace = json.loads(trace_file.read_text(encoding="utf-8"))
            except ValueError:
                trace = None
        if not self._keep_jobs:
            self._state.remove_job(self._run_dir, job.id)
        return JobOutcome(job=job, process=process, record=record, trace=trace)
