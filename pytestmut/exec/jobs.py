"""The wire format between the orchestrator and its test processes.

A job is described to the test process by ``job.json``; the process answers
with JSON-lines record files, one per interpreter it spawns. Both shapes are
shared with ``_shim/``, which cannot import this package, and are pinned by a
contract test.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytestmut._meta import WIRE_VERSION
from pytestmut.mutation.model import Mutant

OBSERVE = "observe"
TRACE = "trace"
MUTATE = "mutate"
CONTROL = "control"


@dataclass(frozen=True)
class TargetIdentity:
    """A target file as the test process must recognise it.

    Attributes:
        index: Position in the run's target list.
        relpath: POSIX path relative to the project root.
        path: Absolute path.
        device: ``st_dev`` of the file.
        inode: ``st_ino`` of the file, or 0 when the filesystem has none.
        sha256: Digest of the bytes the mutants were generated from.
        module: Dotted module name implied by the package layout.
    """

    index: int
    relpath: str
    path: str
    device: int
    inode: int
    sha256: str
    module: str

    def as_wire(self) -> dict[str, Any]:
        """The entry written to ``job.json``."""
        return {
            "index": self.index,
            "path": self.path,
            "dev": self.device,
            "ino": self.inode,
            "sha256": self.sha256,
            "module": self.module,
        }


def identify(index: int, relpath: str, path: Path, sha256: str) -> TargetIdentity:
    """Capture the identity of a target file.

    Arguments:
        index: Position in the run's target list.
        relpath: POSIX path relative to the project root.
        path: Absolute path of the file.
        sha256: Digest of its bytes.

    Returns:
        The identity, including the module name implied by ``__init__.py``
        files above it.
    """
    stat = path.stat()
    parts = [path.stem] if path.stem != "__init__" else []
    directory = path.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return TargetIdentity(
        index=index,
        relpath=relpath,
        path=str(path),
        device=stat.st_dev,
        inode=stat.st_ino,
        sha256=sha256,
        module=".".join(reversed(parts)),
    )


@dataclass(frozen=True)
class Job:
    """One test process to run.

    Attributes:
        id: Short id, unique within the run.
        mode: ``observe`` and ``trace`` are baselines serving every target
            unmodified, ``mutate`` activates one mutant, ``control`` runs a
            selection with nothing mutated.
        mutant: The mutant to activate, for ``mutate`` jobs.
        nodeids: Tests to run, or None for all of them.
        order: Explicit order for the selected tests, or None for collection
            order.
        prune_files: Test files that may be collected, or None for all.
        stop_on_fail: Stop the session at the first failing test.
        timeout_s: Wall-clock budget.
        worker: Worker slot the job runs in.
    """

    id: str
    mode: str
    mutant: Mutant | None = None
    nodeids: tuple[str, ...] | None = None
    order: tuple[str, ...] | None = None
    prune_files: tuple[str, ...] | None = None
    stop_on_fail: bool = True
    timeout_s: float = 60.0
    worker: int = 0


def write_job(
    job_dir: Path,
    job: Job,
    targets: tuple[TargetIdentity, ...],
    run_id: str,
    own_group: bool,
) -> Path:
    """Write ``job.json`` for a test process.

    Arguments:
        job_dir: Directory of the job.
        job: The job.
        targets: Every target file of the run.
        run_id: Id of the run.
        own_group: True when the process gets its own process group, which
            lets its watchdog kill the group safely.

    Returns:
        Path of the written file.
    """
    wire_targets: list[dict[str, Any]]
    if job.mode in {OBSERVE, TRACE}:
        wire_targets = [target.as_wire() for target in targets]
    elif job.mutant is not None:
        by_relpath = {target.relpath: target for target in targets}
        entry = by_relpath[job.mutant.path].as_wire()
        entry["splice"] = {
            "start": job.mutant.splice.start,
            "end": job.mutant.splice.end,
            "replacement": job.mutant.splice.replacement.hex(),
        }
        wire_targets = [entry]
    else:
        wire_targets = []
    data = {
        "wire": WIRE_VERSION,
        "run_id": run_id,
        "job_id": job.id,
        "mode": job.mode,
        "cache_tag": sys.implementation.cache_tag,
        "supervisor_pid": os.getpid(),
        "own_group": own_group,
        "record_dir": str(job_dir / "rec"),
        "targets": wire_targets,
        "mutant_id": job.mutant.id if job.mutant else None,
        "nodeids": list(job.nodeids) if job.nodeids is not None else None,
        "order": list(job.order) if job.order is not None else None,
        "prune_files": list(job.prune_files) if job.prune_files else None,
        "stop_on_fail": job.stop_on_fail,
    }
    path = job_dir / "job.json"
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    return path


@dataclass(frozen=True)
class TestPhase:
    """The outcome of one phase of one test.

    Attributes:
        nodeid: pytest node id.
        when: ``setup``, ``call`` or ``teardown``.
        outcome: ``passed``, ``failed`` or ``skipped``.
        duration: Seconds spent in the phase.
        time: Wall-clock time the phase was reported.
        exception: Exception type name for failures.
        message: Short failure message.
        xfail: True for an expected failure.
    """

    __test__ = False

    nodeid: str
    when: str
    outcome: str
    duration: float
    time: float
    exception: str = ""
    message: str = ""
    xfail: bool = False


@dataclass
class RunRecord:
    """Everything the test processes of one job reported.

    Attributes:
        configured: True once pytest reached ``pytest_configure``.
        unsafe_options: Options the recorder refused to run under.
        collected: Node ids in collection order, for baseline jobs.
        collected_count: Number of tests left after selection.
        missing: Selected tests that were not collected.
        missing_count: How many selected tests were not collected.
        collect_errors: ``(node id, message)`` of collection failures.
        started: Node ids of tests that began, in order.
        phases: Reported test phases, in order.
        exitstatus: Exit status pytest computed, or None without a finish.
        loads: Target files served through the hook, in every process.
        modules_seen: Target modules present in ``sys.modules`` at the end.
        shadows: Modules named like a target but loaded from elsewhere.
        boot_errors: Problems activating the job.
        internal_errors: pytest internal errors.
        root_seen: True when the pytest process itself wrote a record.
        truncated: True when a record line could not be parsed.
    """

    configured: bool = False
    unsafe_options: list[str] = field(default_factory=list)
    collected: list[str] | None = None
    collected_count: int = 0
    missing: list[str] = field(default_factory=list)
    missing_count: int = 0
    collect_errors: list[tuple[str, str]] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    phases: list[TestPhase] = field(default_factory=list)
    exitstatus: int | None = None
    loads: list[dict[str, Any]] = field(default_factory=list)
    modules_seen: list[dict[str, str]] = field(default_factory=list)
    shadows: list[dict[str, str]] = field(default_factory=list)
    boot_errors: list[str] = field(default_factory=list)
    internal_errors: list[str] = field(default_factory=list)
    root_seen: bool = False
    truncated: bool = False

    @property
    def activation_time(self) -> float | None:
        """When the mutated file was first loaded, in any process."""
        times = [float(load["t"]) for load in self.loads if load.get("mutated")]
        return min(times) if times else None

    @property
    def finished(self) -> bool:
        """True when pytest ran to its own end."""
        return self.exitstatus is not None

    def unfinished_test(self) -> str | None:
        """The test that had started when the process died, if any."""
        if self.finished or not self.started:
            return None
        last = self.started[-1]
        ended = {phase.nodeid for phase in self.phases if phase.when == "teardown"}
        return None if last in ended else last


def read_record(record_dir: Path) -> RunRecord:
    """Merge the record files of one job.

    Arguments:
        record_dir: The job's ``rec`` directory.

    Returns:
        The merged record. A line that does not parse, as left by a process
        killed mid-write, marks the record as truncated.
    """
    record = RunRecord()
    for path in sorted(record_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            record.truncated = True
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                record.truncated = True
                continue
            if isinstance(event, dict):
                _absorb(record, event)
    record.loads.sort(key=lambda load: load.get("t", 0.0))
    return record


def _absorb(record: RunRecord, event: dict[str, Any]) -> None:
    kind = event.get("ev")
    if kind == "proc":
        record.root_seen = record.root_seen or bool(event.get("root"))
    elif kind == "load":
        record.loads.append(event)
    elif kind == "boot_error":
        record.boot_errors.append(f"{event.get('path', '')}: {event.get('msg', '')}")
    elif kind == "configure":
        record.configured = True
        record.unsafe_options = list(event.get("unsafe", ()))
    elif kind == "collected":
        if "ids" in event:
            record.collected = list(event["ids"])
            record.collected_count = len(record.collected)
        else:
            record.collected_count = int(event.get("count", 0))
            record.missing = list(event.get("missing", ()))
            record.missing_count = int(event.get("missing_count", 0))
    elif kind == "collect_error":
        record.collect_errors.append((event.get("id", ""), event.get("msg", "")))
    elif kind == "start":
        record.started.append(event.get("id", ""))
    elif kind == "test":
        record.phases.append(
            TestPhase(
                nodeid=event.get("id", ""),
                when=event.get("when", ""),
                outcome=event.get("outcome", ""),
                duration=float(event.get("dur", 0.0)),
                time=float(event.get("t", 0.0)),
                exception=event.get("exc", ""),
                message=event.get("msg", ""),
                xfail=bool(event.get("xfail", False)),
            )
        )
    elif kind == "modules":
        record.modules_seen = list(event.get("seen", ()))
        record.shadows = list(event.get("shadows", ()))
    elif kind == "internal_error":
        record.internal_errors.append(event.get("msg", ""))
    elif kind == "finish":
        record.exitstatus = int(event.get("exitstatus", -1))
