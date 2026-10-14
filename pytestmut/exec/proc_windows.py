"""Windows process control: one Job Object per test process.

``Popen.kill`` ends a single process on Windows, and the ``python.exe`` of a
virtual environment is a launcher whose child is the real interpreter, so
killing the pid that was started can miss everything that matters. A Job
Object with kill-on-close takes the whole tree down, including when pytestmut
itself dies.

The child is created suspended, placed in the job, then resumed, so it cannot
start a grandchild outside the job. If any step fails the code falls back to
``taskkill /T /F``.

This module is exercised only by the Windows CI job.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_CREATE_SUSPENDED = 0x00000004
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_MEMORY = 0x00000200
_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_EXTENDED_LIMIT_INFORMATION = 9
# PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_SUSPEND_RESUME
# | PROCESS_QUERY_LIMITED_INFORMATION
_PROCESS_ACCESS = 0x0001 | 0x0100 | 0x0800 | 0x1000


@dataclass
class WindowsHandle:
    """A running test process and the Job Object that contains it.

    Attributes:
        process: The started process.
        job: Handle of the Job Object, or 0 when job creation failed.
    """

    process: subprocess.Popen[bytes]
    job: int = 0

    def kill_tree(self) -> None:
        """Terminate every process in the job, or fall back to taskkill."""
        if sys.platform != "win32":
            return
        if self.job:
            _kernel32().TerminateJobObject(self.job, 1)
        elif self.process.poll() is None:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
                capture_output=True,
                check=False,
            )

    def tree_rss(self) -> int | None:
        """Peak committed memory of the job in bytes."""
        if sys.platform != "win32" or not self.job:
            return None
        return _peak_job_memory(self.job)

    def memory_killed(self) -> bool:
        """The job limit makes allocations fail; it does not kill."""
        return False

    def close(self) -> None:
        """Close the job, which kills anything still inside it."""
        if sys.platform == "win32" and self.job:
            _kernel32().CloseHandle(self.job)
            self.job = 0
        if self.process.stdout is not None:
            self.process.stdout.close()


def spawn(
    argv: tuple[str, ...], env: dict[str, str], cwd: Path, memory_limit: int | None
) -> WindowsHandle:
    """Start a process inside a new Job Object.

    Arguments:
        argv: Command line.
        env: Complete environment.
        cwd: Working directory.
        memory_limit: Bytes the job may commit, or None for no limit.

    Returns:
        A handle that can kill the whole tree.

    Raises:
        OSError: If the process cannot be started.
    """
    if sys.platform != "win32":
        raise OSError("Windows process control is not available on this platform")
    job = _create_job(memory_limit)
    flags = _CREATE_NEW_PROCESS_GROUP | (_CREATE_SUSPENDED if job else 0)
    try:
        process = subprocess.Popen(
            argv,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except OSError:
        if job:
            _kernel32().CloseHandle(job)
        raise
    if not job:
        return WindowsHandle(process=process)
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_ACCESS, False, process.pid)
    assigned = bool(handle) and bool(kernel32.AssignProcessToJobObject(job, handle))
    resumed = bool(handle) and _resume(handle)
    if handle:
        kernel32.CloseHandle(handle)
    if not resumed:
        # A process that stays suspended would hang the run; end it now.
        process.kill()
    if not assigned:
        kernel32.CloseHandle(job)
        job = 0
    return WindowsHandle(process=process, job=job)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in "abcdef"]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _KERNEL32: ctypes.WinDLL | None = None

    def _kernel32() -> ctypes.WinDLL:
        global _KERNEL32
        if _KERNEL32 is None:
            library = ctypes.WinDLL("kernel32", use_last_error=True)
            # Explicit prototypes keep 64-bit handles from being truncated.
            library.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            library.CreateJobObjectW.restype = wintypes.HANDLE
            library.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.LPVOID,
                wintypes.DWORD,
            ]
            library.SetInformationJobObject.restype = wintypes.BOOL
            library.QueryInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.LPVOID,
            ]
            library.QueryInformationJobObject.restype = wintypes.BOOL
            library.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            library.OpenProcess.restype = wintypes.HANDLE
            library.AssignProcessToJobObject.argtypes = [
                wintypes.HANDLE,
                wintypes.HANDLE,
            ]
            library.AssignProcessToJobObject.restype = wintypes.BOOL
            library.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            library.TerminateJobObject.restype = wintypes.BOOL
            library.CloseHandle.argtypes = [wintypes.HANDLE]
            library.CloseHandle.restype = wintypes.BOOL
            _KERNEL32 = library
        return _KERNEL32

    def _create_job(memory_limit: int | None) -> int:
        kernel32 = _kernel32()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return 0
        limits = _ExtendedLimits()
        flags = _KILL_ON_JOB_CLOSE | _DIE_ON_UNHANDLED_EXCEPTION
        if memory_limit:
            flags |= _JOB_MEMORY
            # A hard ceiling above the watchdog's limit, as a backstop.
            limits.JobMemoryLimit = int(memory_limit * 1.25)
        limits.BasicLimitInformation.LimitFlags = flags
        configured = kernel32.SetInformationJobObject(
            job,
            _EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not configured:
            kernel32.CloseHandle(job)
            return 0
        return int(job)

    def _peak_job_memory(job: int) -> int | None:
        limits = _ExtendedLimits()
        found = _kernel32().QueryInformationJobObject(
            job,
            _EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
            None,
        )
        return int(limits.PeakJobMemoryUsed) if found else None

    def _resume(handle: int) -> bool:
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        return int(ntdll.NtResumeProcess(handle)) >= 0

else:

    def _kernel32() -> object:
        raise OSError("kernel32 is only available on Windows")

    def _create_job(memory_limit: int | None) -> int:
        return 0

    def _peak_job_memory(job: int) -> int | None:
        return None

    def _resume(handle: int) -> bool:
        return False
