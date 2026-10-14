"""POSIX process control: one session per test process, killed as a group."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class GroupMemory:
    """Resident memory per process group, sampled once for all callers.

    Every running job asks about its own group several times a second; one
    scan of the process table answers all of them.

    Arguments:
        max_age: Seconds a sample stays fresh.
    """

    def __init__(self, max_age: float = 0.2) -> None:
        self._max_age = max_age
        self._lock = threading.Lock()
        self._taken = 0.0
        self._sample: dict[int, int] | None = {}

    def rss(self, pgid: int) -> int | None:
        """Resident memory of one group in bytes.

        Arguments:
            pgid: Process group id.

        Returns:
            Bytes in use, or None when the platform cannot be asked.
        """
        with self._lock:
            now = time.monotonic()
            if now - self._taken >= self._max_age:
                self._sample = _sample()
                self._taken = now
            if self._sample is None:
                return None
            return self._sample.get(pgid, 0)


_MEMORY = GroupMemory()


@dataclass
class PosixHandle:
    """A running test process and the process group it leads.

    Attributes:
        process: The started process, leader of its own session.
    """

    process: subprocess.Popen[bytes]

    def kill_tree(self) -> None:
        """Kill every process still in the group.

        A descendant that started its own session is out of reach; that is a
        documented limit of group-based cleanup.
        """
        if sys.platform != "win32":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def tree_rss(self) -> int | None:
        """Resident memory of the whole group in bytes, when measurable."""
        return _MEMORY.rss(self.process.pid)

    def memory_killed(self) -> bool:
        """POSIX has no kernel-enforced limit here; the watchdog decides."""
        return False

    def close(self) -> None:
        """Release the pipe."""
        if self.process.stdout is not None:
            self.process.stdout.close()


def spawn(argv: tuple[str, ...], env: dict[str, str], cwd: Path) -> PosixHandle:
    """Start a process as the leader of a new session.

    Arguments:
        argv: Command line.
        env: Complete environment.
        cwd: Working directory.

    Returns:
        A handle that can kill the whole group.

    Raises:
        OSError: If the process cannot be started.
    """
    process = subprocess.Popen(
        argv,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    return PosixHandle(process=process)


def _sample() -> dict[int, int] | None:
    if sys.platform.startswith("linux"):
        return _linux_sample()
    return _ps_sample()


def _linux_sample() -> dict[int, int] | None:
    if sys.platform == "win32":
        return None
    page = os.sysconf("SC_PAGE_SIZE")
    totals: dict[int, int] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
            # Counted after the command name: index 2 is the process group
            # and index 21 the resident set size in pages.
            group = int(fields[2])
            totals[group] = totals.get(group, 0) + int(fields[21]) * page
        except (OSError, ValueError, IndexError):
            continue
    return totals


def _ps_sample() -> dict[int, int] | None:
    try:
        output = subprocess.run(
            ["ps", "-axo", "pgid=,rss="],
            capture_output=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    totals: dict[int, int] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            group = int(parts[0])
            totals[group] = totals.get(group, 0) + int(parts[1]) * 1024
    return totals
