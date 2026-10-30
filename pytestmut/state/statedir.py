"""The sentinel-guarded state directory.

Everything pytestmut writes lives under one directory, and that directory is
the only thing it ever deletes. A sentinel file marks it as ours: a directory
without the sentinel is never emptied, whatever the configuration says.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from pytestmut._meta import SENTINEL_NAME, TOOL_NAME, __version__
from pytestmut.exceptions import StateDirError


@dataclass(frozen=True)
class RunDir:
    """Scratch space of one run.

    It lives outside the project by default. Tests that walk up from their
    temporary directory looking for a ``pyproject.toml`` or a ``.git`` must
    not find the project being tested, just as they would not in a normal
    run.

    Attributes:
        run_id: Short unique id of the run.
        path: Root of the run's scratch space, marked with the sentinel.
    """

    run_id: str
    path: Path

    @property
    def tmp_root(self) -> Path:
        """Parent of the per-job directories."""
        return self.path / "j"

    def job_dir(self, job_id: str) -> Path:
        """Create and return the directory of one job.

        Arguments:
            job_id: Short job id, unique within the run.

        Returns:
            The job directory, with its ``rec`` and ``tmp`` subdirectories.
        """
        path = self.tmp_root / job_id
        (path / "rec").mkdir(parents=True, exist_ok=True)
        (path / "tmp").mkdir(exist_ok=True)
        return path


# Results of this many recent runs are kept, for comparing verdicts.
RUNS_KEPT = 10


class StateDir:
    """Creates, locks and cleans the state directory.

    Arguments:
        project_root: Root of the project being tested.
        path: Configured location, relative to the root or absolute.
    """

    def __init__(self, project_root: Path, path: str) -> None:
        self._root = project_root.resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        self._path = Path(os.path.abspath(candidate))
        self._locked = False

    @property
    def path(self) -> Path:
        """Absolute path of the state directory."""
        return self._path

    @property
    def results_file(self) -> Path:
        """Where the canonical result of the last run is kept."""
        return self._path / "results.json"

    @property
    def history_dir(self) -> Path:
        """Where the results of recent runs are kept, one file per run."""
        return self._path / "history"

    def archive(self, text: str, run_id: str, keep: int = RUNS_KEPT) -> Path:
        """Keep a run's result, dropping all but the most recent ``keep``.

        Arguments:
            text: The serialised result.
            run_id: Id of the run, used as the file name.
            keep: How many results to keep in total.

        Returns:
            The file written.
        """
        self.history_dir.mkdir(parents=True, exist_ok=True)
        target = self.history_dir / f"{run_id}.json"
        target.write_text(text, encoding="utf-8", newline="\n")
        older = sorted(
            (path for path in self.history_dir.glob("*.json") if path != target),
            key=lambda path: path.stat().st_mtime,
        )
        for path in older[: max(0, len(older) - (keep - 1))]:
            path.unlink(missing_ok=True)
        return target

    @property
    def cache_file(self) -> Path:
        """Where reusable verdicts are journaled."""
        return self._path / "cache" / "verdicts.jsonl"

    def pycache_prefix(self, cache_tag: str) -> Path:
        """Bytecode cache directory for one interpreter version."""
        return self._path / "pycache" / cache_tag

    def _refuse(self, why: str) -> StateDirError:
        return StateDirError(f"state directory {self._path}: {why}")

    def ensure(self) -> None:
        """Create the directory, or check that an existing one is ours.

        Raises:
            StateDirError: If the path is the project itself, contains it, is
                a symlink, or exists without the sentinel.
        """
        if self._path == self._root or self._path in self._root.parents:
            raise self._refuse("must not be the project root or contain it")
        if self._path.is_symlink():
            raise self._refuse("must not be a symlink")
        if self._path.exists():
            if not self._path.is_dir():
                raise self._refuse("exists and is not a directory")
            if not (self._path / SENTINEL_NAME).is_file() and any(self._path.iterdir()):
                raise self._refuse(
                    f"exists, is not empty and has no {SENTINEL_NAME} marker; "
                    "refusing to use it"
                )
        self._path.mkdir(parents=True, exist_ok=True)
        marker = {"tool": TOOL_NAME, "version": __version__}
        (self._path / SENTINEL_NAME).write_text(
            json.dumps(marker) + "\n", encoding="utf-8"
        )
        (self._path / ".gitignore").write_text("*\n", encoding="utf-8")

    def lock(self) -> None:
        """Take the run lock.

        Raises:
            StateDirError: If another live process holds it.
        """
        lock = self._path / "lock"
        try:
            holder = int(lock.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            holder = 0
        if holder and holder != os.getpid() and process_alive(holder):
            raise self._refuse(f"in use by another run (pid {holder})")
        lock.write_text(str(os.getpid()), encoding="utf-8")
        self._locked = True

    def unlock(self) -> None:
        """Release the run lock if this process holds it."""
        if self._locked:
            (self._path / "lock").unlink(missing_ok=True)
            self._locked = False

    def new_run(self, tmp_root: str) -> RunDir:
        """Start a run: sweep leftovers of dead runs, make fresh scratch space.

        Arguments:
            tmp_root: Configured parent of the scratch space; empty uses the
                system temporary directory.

        Returns:
            The scratch space of the new run.
        """
        # Scratch space of versions before 0.1.0; history lives elsewhere.
        self.remove(self._path / "runs")
        parent = (
            Path(tmp_root).expanduser() if tmp_root else Path(tempfile.gettempdir())
        )
        parent = parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        _sweep_stale(parent)
        run_id = uuid.uuid4().hex[:8]
        path = parent / f"{TOOL_NAME}-{run_id}"
        # Private, like tempfile.mkdtemp: the system temp directory is shared,
        # and the tests' own temporary files end up in here.
        path.mkdir(mode=0o700)
        marker = {"tool": TOOL_NAME, "run": run_id, "pid": os.getpid()}
        (path / SENTINEL_NAME).write_text(json.dumps(marker), encoding="utf-8")
        (path / "j").mkdir()
        return RunDir(run_id=run_id, path=path)

    def remove(self, path: Path) -> None:
        """Delete a path inside the state directory.

        Arguments:
            path: File or directory to delete.

        Raises:
            StateDirError: If the path is outside the state directory or the
                sentinel is missing.
        """
        if not path.exists() and not path.is_symlink():
            return
        if not (self._path / SENTINEL_NAME).is_file():
            raise self._refuse(f"no {SENTINEL_NAME} marker; refusing to delete")
        absolute = Path(os.path.abspath(path))
        if self._path != absolute and self._path not in absolute.parents:
            raise self._refuse(f"refusing to delete {absolute}, which is outside it")
        _remove_tree(absolute)

    def remove_job(self, run: RunDir, job_id: str) -> None:
        """Delete the scratch directory of one finished job.

        Arguments:
            run: The run the job belongs to.
            job_id: Id of the job.
        """
        if _is_scratch(run.path):
            _remove_tree(run.tmp_root / job_id)

    def remove_scratch(self, run: RunDir) -> None:
        """Delete the scratch space of a run.

        Arguments:
            run: The run whose scratch space is no longer needed. A
                directory without the sentinel is left alone.
        """
        if _is_scratch(run.path):
            _remove_tree(run.path)

    def clean(self) -> bool:
        """Delete the whole state directory.

        Returns:
            True when something was deleted.

        Raises:
            StateDirError: If the directory exists without the sentinel.
        """
        if not self._path.exists():
            return False
        if self._path.is_symlink():
            raise self._refuse("is a symlink; refusing to delete")
        if not (self._path / SENTINEL_NAME).is_file():
            raise self._refuse(f"no {SENTINEL_NAME} marker; refusing to delete")
        _remove_tree(self._path)
        return True


def _is_scratch(path: Path) -> bool:
    """True for a directory this tool created as scratch space."""
    return path.is_dir() and not path.is_symlink() and (path / SENTINEL_NAME).is_file()


def _sweep_stale(parent: Path) -> None:
    """Delete scratch space left behind by runs whose process is gone."""
    for item in parent.glob(f"{TOOL_NAME}-*"):
        try:
            if not _is_scratch(item):
                continue
            marker = json.loads((item / SENTINEL_NAME).read_text(encoding="utf-8"))
            owner = int(marker.get("pid", 0))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if marker.get("tool") == TOOL_NAME and owner and not process_alive(owner):
            _remove_tree(item)


def process_alive(pid: int) -> bool:
    """Tell whether a process is running, without disturbing it.

    A zombie, a process that has exited but was not yet reaped by its parent,
    counts as dead. Containers without an init process never reap orphans, so
    a killed orphan stays a zombie there for good.

    Arguments:
        pid: Process id.

    Returns:
        True while the process runs.
    """
    if sys.platform == "win32":
        import ctypes

        # os.kill(pid, 0) would terminate the process on Windows.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        running = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and (
            code.value == 259
        )
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle(handle)
        return running
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _zombie(pid)


def _zombie(pid: int) -> bool:
    """True when the process has exited and only awaits reaping."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            # The state follows the command name, which may contain spaces.
            return handle.read().rsplit(b")", 1)[1].split()[0] == b"Z"
    except (OSError, IndexError):
        pass
    try:
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return state.strip().startswith("Z")


def _remove_tree(path: Path) -> None:
    """Delete a tree without following symlinks, retrying on locked files."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    for attempt in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        # Windows keeps files locked briefly after a process exits.
        time.sleep(0.2 * (attempt + 1))
