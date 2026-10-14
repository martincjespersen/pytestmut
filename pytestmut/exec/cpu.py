"""How many workers and how much memory the machine can give.

``os.cpu_count()`` reports the host's cores, which inside a container can be
several times what the process may use. Oversubscribed workers are slow
workers, and slow workers turn into false timeouts.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
MIB = 1024**2
# Smallest per-process limit: an interpreter with pytest loaded needs room.
MIN_TREE_LIMIT = 512 * MIB
# A mutant may legitimately need more than the unmutated suite did.
HEADROOM = 2.5
BUDGET_FRACTION = 0.5
FALLBACK_BUDGET = 4 * GIB


def _cgroup_quota() -> float | None:
    """CPU quota of the current cgroup, in cores."""
    try:
        text = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        if text and text[0] != "max":
            return int(text[0]) / int(text[1])
    except (OSError, ValueError, IndexError, ZeroDivisionError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text("ascii"))
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text("ascii"))
        if quota > 0 and period > 0:
            return quota / period
    except (OSError, ValueError):
        pass
    return None


def effective_cpus() -> int:
    """Number of CPUs this process may actually use.

    Returns:
        The smallest of the affinity mask, the cgroup quota and the core
        count, and at least 1.
    """
    counts: list[float] = [float(os.cpu_count() or 1)]
    process_count = getattr(os, "process_cpu_count", None)
    if process_count is not None:
        counts.append(float(process_count() or 1))
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            counts.append(float(len(affinity(0))))
        except OSError:
            pass
    quota = _cgroup_quota()
    if quota is not None:
        counts.append(quota)
    return max(1, int(min(counts)))


def total_memory() -> int | None:
    """Physical memory in bytes, or None when it cannot be determined."""
    if sys.platform == "win32":
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return None
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, AttributeError):
        return None


def children_peak_rss() -> int | None:
    """Largest resident size any finished child process reached.

    The kernel keeps this high-water mark for the processes this one has
    waited for, which makes it exact even for a run too short to be sampled.
    It is one number for all children so far, so it describes a single job
    only while jobs run one at a time.

    Returns:
        Bytes, or None where the platform does not report it.
    """
    if sys.platform == "win32":
        return None
    import resource

    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if peak <= 0:
        return None
    # macOS reports bytes, Linux and the BSDs kilobytes.
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def cpu_workers(configured: int | None) -> int:
    """Worker count the CPUs allow, before memory is considered.

    Arguments:
        configured: Count from the configuration, or None for automatic.

    Returns:
        The configured count, or one worker per usable CPU.
    """
    return max(1, configured) if configured is not None else effective_cpus()


def memory_budget(configured: int | None) -> int | None:
    """Total memory all test processes together may use.

    Arguments:
        configured: Bytes from the configuration; 0 for no budget, None for
            automatic.

    Returns:
        The budget in bytes, or None for no budget. Automatic is half of the
        physical memory, so a run leaves the machine usable.
    """
    if configured == 0:
        return None
    if configured is not None:
        return configured
    memory = total_memory()
    if memory is None:
        return FALLBACK_BUDGET
    return int(BUDGET_FRACTION * memory)


@dataclass(frozen=True)
class MemoryPlan:
    """How many workers run and how much memory each may use.

    Attributes:
        workers: Number of test processes in flight at once.
        limit: Bytes one test process tree may use, or None for no limit.
        budget: Bytes all of them together may use, or None for no budget.
        reduced: True when memory, not CPUs, decided the worker count.
        over_budget: True when one run of the suite alone needs more than
            the budget.
    """

    workers: int
    limit: int | None
    budget: int | None
    reduced: bool = False
    over_budget: bool = False


def plan_memory(
    budget: int | None,
    wanted_workers: int,
    peak_rss: int | None,
    configured_limit: int | None,
) -> MemoryPlan:
    """Size the run so that ``workers * limit`` stays within the budget.

    The per-process limit is what one run of the suite was measured to need,
    with headroom for a mutant that legitimately uses more. The worker count
    is then however many such limits fit in the budget. Every process tree is
    killed at its limit, so the budget holds even if every mutant misbehaves
    at once.

    Arguments:
        budget: Bytes all test processes together may use, or None.
        wanted_workers: Workers the CPUs and the amount of work allow.
        peak_rss: Memory one unmutated run of the suite needed, when known.
        configured_limit: Per-process limit from the configuration; 0 for
            off, None for automatic.

    Returns:
        The plan.
    """
    measured = peak_rss or 0
    automatic = max(MIN_TREE_LIMIT, int(HEADROOM * measured))
    if configured_limit == 0:
        limit: int | None = None
        needed = max(MIN_TREE_LIMIT, int(1.5 * measured))
    else:
        limit = configured_limit if configured_limit is not None else automatic
        needed = limit
    if budget is None:
        return MemoryPlan(workers=max(1, wanted_workers), limit=limit, budget=None)
    over_budget = needed > budget
    if over_budget and configured_limit is None:
        # One run does not fit with full headroom; give it what there is.
        limit = max(budget, int(1.25 * measured))
        over_budget = limit > budget
    workers = max(1, min(wanted_workers, budget // max(needed, 1)))
    return MemoryPlan(
        workers=workers,
        limit=limit,
        budget=budget,
        reduced=workers < wanted_workers,
        over_budget=over_budget,
    )


def format_size(size: int | None) -> str:
    """Render a byte count for messages.

    Arguments:
        size: Bytes, or None.

    Returns:
        Text such as ``1.5 GiB``, or ``unlimited``.
    """
    if size is None:
        return "unlimited"
    if size >= GIB:
        return f"{size / GIB:.1f} GiB"
    return f"{size / MIB:.0f} MiB"
