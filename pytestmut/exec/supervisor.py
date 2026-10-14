"""Run one test process and guarantee that nothing of it survives.

The supervisor does three things a plain ``subprocess.run(timeout=...)`` does
not: it kills the whole process tree, it sweeps that tree after every run
(a normal exit can leave workers behind just as a timeout can), and it never
lets the child's output grow without bound.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from pytestmut.exec import proc_posix, proc_windows

# How often the memory of a running process tree is sampled.
MEMORY_POLL_S = 0.2


@dataclass(frozen=True)
class ProcessSpec:
    """What to run.

    Attributes:
        argv: Command line.
        env: Complete environment.
        cwd: Working directory.
        timeout_s: Wall-clock budget.
        memory_limit: Bytes the process tree may use; 0 or None for no limit.
        output_cap: Bytes of output kept, split between head and tail.
    """

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    timeout_s: float
    memory_limit: int | None = None
    output_cap: int = 131072


@dataclass(frozen=True)
class ProcessOutcome:
    """How a process ended.

    Attributes:
        returncode: Exit code, negative for a signal, None if never started.
        timed_out: True when the budget expired and the tree was killed.
        oom: True when the memory limit was exceeded and the tree was killed.
        cancelled: True when the run was cancelled from outside.
        duration_s: Wall-clock time from start to exit.
        peak_rss: Largest resident size seen for the tree, when measured.
        output: Captured output, head and tail if it was truncated.
    """

    returncode: int | None
    timed_out: bool = False
    oom: bool = False
    cancelled: bool = False
    duration_s: float = 0.0
    peak_rss: int | None = None
    output: str = ""


class ProcessSupervisor(Protocol):
    """Runs a process to completion under a budget."""

    def run(self, spec: ProcessSpec, cancel: threading.Event) -> ProcessOutcome:
        """Run the process and wait for it.

        Arguments:
            spec: What to run.
            cancel: Set from another thread to abort the run.

        Returns:
            How the process ended.
        """
        ...


class _Drain(threading.Thread):
    """Reads a pipe to its end, keeping only the head and tail."""

    def __init__(self, stream: IO[bytes], cap: int) -> None:
        super().__init__(name="pytestmut-drain", daemon=True)
        self._stream = stream
        self._half = max(cap // 2, 1024)
        self._head = bytearray()
        self._tail = bytearray()
        self._dropped = 0

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(65536)
                if not chunk:
                    return
                room = self._half - len(self._head)
                if room > 0:
                    self._head += chunk[:room]
                    chunk = chunk[room:]
                self._tail += chunk
                if len(self._tail) > self._half:
                    self._dropped += len(self._tail) - self._half
                    del self._tail[: len(self._tail) - self._half]
        except (OSError, ValueError):
            return

    def text(self) -> str:
        middle = b""
        if self._dropped:
            middle = b"\n... %d bytes of output dropped ...\n" % self._dropped
        data = bytes(self._head) + middle + bytes(self._tail)
        return data.decode("utf-8", errors="replace")


class LocalSupervisor:
    """Runs processes on this machine with tree-kill on every platform.

    Arguments:
        poll_interval: Seconds between checks of the budget, the memory
            limit and the cancel flag.
    """

    def __init__(self, poll_interval: float = 0.05) -> None:
        self._poll = poll_interval

    @property
    def own_group(self) -> bool:
        """True when each process gets a group that can be killed as one."""
        return sys.platform != "win32"

    def run(self, spec: ProcessSpec, cancel: threading.Event) -> ProcessOutcome:
        """Run the process and wait for it.

        Arguments:
            spec: What to run.
            cancel: Set from another thread to abort the run.

        Returns:
            How the process ended.
        """
        started = time.monotonic()
        try:
            handle = _spawn(spec)
        except OSError as exc:
            return ProcessOutcome(returncode=None, output=f"cannot start: {exc}")
        assert handle.process.stdout is not None
        drain = _Drain(handle.process.stdout, spec.output_cap)
        drain.start()
        deadline = started + spec.timeout_s
        timed_out = oom = cancelled = False
        peak = 0
        next_memory_check = started
        try:
            while handle.process.poll() is None:
                now = time.monotonic()
                if cancel.is_set():
                    cancelled = True
                    break
                if now >= deadline:
                    timed_out = True
                    break
                if now >= next_memory_check:
                    next_memory_check = now + MEMORY_POLL_S
                    used = handle.tree_rss()
                    if used is not None:
                        peak = max(peak, used)
                        if spec.memory_limit and used > spec.memory_limit:
                            oom = True
                            break
                time.sleep(self._poll)
        finally:
            # Always sweep: a clean exit can leave descendants behind too.
            handle.kill_tree()
        try:
            returncode: int | None = handle.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            returncode = None
        drain.join(timeout=5)
        # A kernel-enforced limit fails allocations faster than the poll above
        # sees them; the recorded peak still shows the tree went over.
        final = handle.peak_after_exit()
        handle.close()
        if final is not None:
            peak = max(peak, final)
            if spec.memory_limit and final > spec.memory_limit:
                oom = True
        return ProcessOutcome(
            returncode=returncode,
            timed_out=timed_out,
            oom=oom,
            cancelled=cancelled,
            duration_s=time.monotonic() - started,
            peak_rss=peak or None,
            output=drain.text(),
        )


class _Handle(Protocol):
    process: subprocess.Popen[bytes]

    def kill_tree(self) -> None:
        """Kill every process the run started."""

    def tree_rss(self) -> int | None:
        """Memory in use by the process tree, when measurable."""

    def peak_after_exit(self) -> int | None:
        """Peak memory of the finished tree, when the platform records one."""

    def close(self) -> None:
        """Release handles held for the run."""


def _spawn(spec: ProcessSpec) -> _Handle:
    if sys.platform == "win32":
        return proc_windows.spawn(
            spec.argv, dict(spec.env), spec.cwd, spec.memory_limit
        )
    return proc_posix.spawn(spec.argv, dict(spec.env), spec.cwd)
