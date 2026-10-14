"""Runs inside every test process: serves the mutated module and records it.

This module is imported by ``sitecustomize`` before anything else, so it uses
the standard library only and never imports the ``pytestmut`` package (doing
so would make pytest warn that a plugin module was imported too early).

Activation patches ``SourceFileLoader.get_code``. When the file being loaded
is a target, matched by device and inode, its source is taken from memory with
the mutation applied and compiled directly. No mutated file and no bytecode
for a target ever touch the disk, and ``__file__``, ``__spec__`` and line
numbers stay those of the real file.
"""

from __future__ import annotations

import hashlib
import json
import linecache
import os
import sys
import threading
import time
from typing import Any

WIRE_VERSION = 1
ENV_JOB = "PYTESTMUT_JOB"
ENV_ROOT = "PYTESTMUT_ROOT_PID"

_MISSING: Any = object()
_state: State | None = None


class State:
    """Everything one process knows about its job.

    Arguments:
        job: The decoded ``job.json``.
    """

    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        self.mode: str = job.get("mode", "observe")
        self.is_root = ENV_ROOT not in os.environ
        if self.is_root:
            os.environ[ENV_ROOT] = str(os.getpid())
        self._by_name: dict[str, list[dict[str, Any]]] = {}
        for target in job.get("targets", ()):
            name = os.path.normcase(os.path.basename(target["path"]))
            self._by_name.setdefault(name, []).append(target)
        self._by_path: dict[str, dict[str, Any] | None] = {}
        self._served: dict[str, bytes | None] = {}
        self._seeded: dict[str, tuple[int, None, list[str], str]] = {}
        self._lock = threading.Lock()
        self._handle: Any = None
        self._handle_pid = 0
        self._record_dir: str = job.get("record_dir", "")
        self.tracer: Any = None

    def record(self, event: dict[str, Any]) -> None:
        """Append one event to this process's record file.

        Arguments:
            event: JSON-serialisable event; ``t`` and ``pid`` are added.
        """
        if not self._record_dir:
            return
        event.setdefault("t", time.time())
        event["pid"] = os.getpid()
        try:
            with self._lock:
                if self._handle is None or self._handle_pid != os.getpid():
                    name = "%d-%s.jsonl" % (os.getpid(), os.urandom(4).hex())
                    self._handle = open(
                        os.path.join(self._record_dir, name),
                        "a",
                        encoding="utf-8",
                        newline="\n",
                    )
                    self._handle_pid = os.getpid()
                    header = {
                        "ev": "proc",
                        "pid": os.getpid(),
                        "ppid": os.getppid(),
                        "root": os.environ.get(ENV_ROOT) == str(os.getpid()),
                        "t": time.time(),
                    }
                    self._handle.write(json.dumps(header) + "\n")
                self._handle.write(json.dumps(event) + "\n")
                self._handle.flush()
        except (OSError, ValueError, TypeError):
            pass

    def match(self, path: str) -> dict[str, Any] | None:
        """Find the target entry for a file path, by file identity.

        Arguments:
            path: Path a loader is about to read.

        Returns:
            The target entry, or None when the file is not a target.
        """
        cached = self._by_path.get(path, _MISSING)
        if cached is not _MISSING:
            return cached  # type: ignore[no-any-return]
        entry: dict[str, Any] | None = None
        candidates = self._by_name.get(os.path.normcase(os.path.basename(path)))
        if candidates:
            try:
                stat = os.stat(path)
            except OSError:
                stat = None
            if stat is not None:
                for candidate in candidates:
                    if candidate.get("ino"):
                        same = (stat.st_dev, stat.st_ino) == (
                            candidate.get("dev"),
                            candidate.get("ino"),
                        )
                    else:
                        same = os.path.normcase(
                            os.path.realpath(path)
                        ) == os.path.normcase(candidate["path"])
                    if same:
                        entry = candidate
                        break
        self._by_path[path] = entry
        return entry

    def served(self, path: str, entry: dict[str, Any]) -> bytes | None:
        """Source bytes to compile for a target file.

        Arguments:
            path: Path of the file.
            entry: Its target entry.

        Returns:
            The bytes with the job's mutation applied when this file carries
            it, or None when the file no longer matches what the mutant was
            built from.
        """
        if path in self._served:
            return self._served[path]
        data: bytes | None
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self.record({"ev": "boot_error", "path": path, "msg": str(exc)})
            data = None
        if data is not None and entry.get("sha256"):
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                self.record(
                    {
                        "ev": "boot_error",
                        "path": path,
                        "msg": "source changed since mutants were generated",
                    }
                )
                data = None
        splice = entry.get("splice")
        if data is not None and splice:
            replacement = bytes.fromhex(splice["replacement"])
            data = data[: splice["start"]] + replacement + data[splice["end"] :]
        self._served[path] = data
        return data

    def seed_linecache(self, path: str, data: bytes) -> None:
        """Make tracebacks and ``inspect.getsource`` show the served source."""
        try:
            import importlib.util

            text = importlib.util.decode_source(data)
        except (SyntaxError, UnicodeDecodeError, LookupError):
            return
        entry = (len(text), None, text.splitlines(True), path)
        self._seeded[path] = entry
        linecache.cache[path] = entry

    def reseed_linecache(self) -> None:
        """Restore seeded entries that something cleared."""
        for path, entry in self._seeded.items():
            if linecache.cache.get(path) is not entry:
                linecache.cache[path] = entry


def state() -> State | None:
    """The state of this process, or None when no job is active."""
    return _state


def install() -> State | None:
    """Activate the job named by the environment, once per process.

    Returns:
        The process state, or None when there is no job for this interpreter.
    """
    global _state
    if _state is not None:
        return _state
    path = os.environ.get(ENV_JOB)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            job = json.load(handle)
    except (OSError, ValueError):
        return None
    if job.get("wire") != WIRE_VERSION:
        return None
    if job.get("cache_tag") != sys.implementation.cache_tag:
        return None
    _state = State(job)
    _patch_loader(_state)
    if _state.is_root and job.get("own_group") and os.name == "posix":
        _watch_supervisor(int(job.get("supervisor_pid", 0)))
    if _state.is_root and _state.mode == "trace":
        _start_tracer(_state)
    return _state


def _start_tracer(current: State) -> None:
    """Start the line tracer before any project code is imported."""
    try:
        from _pytestmut_tracer import Tracer
    except ImportError as exc:
        current.record({"ev": "boot_error", "path": "", "msg": f"no tracer: {exc}"})
        return

    def file_index(filename: str) -> int | None:
        entry = current.match(filename)
        return None if entry is None else int(entry["index"])

    current.tracer = Tracer(file_index)
    current.tracer.start()


def _patch_loader(current: State) -> None:
    from importlib.machinery import SourceFileLoader

    original_get_code = SourceFileLoader.get_code
    original_get_data = SourceFileLoader.get_data

    def get_code(self: Any, fullname: str) -> Any:
        try:
            path = self.get_filename(fullname)
            entry = current.match(path)
            data = current.served(path, entry) if entry is not None else None
        except Exception:  # noqa: BLE001 - never break an import
            entry, data = None, None
        if entry is None or data is None:
            return original_get_code(self, fullname)
        current.record(
            {
                "ev": "load",
                "path": entry["path"],
                "module": fullname,
                "mutated": bool(entry.get("splice")),
                "loader": type(self).__name__,
                "root": current.is_root,
                "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
            }
        )
        current.seed_linecache(path, data)
        return self.source_to_code(data, path)

    def get_data(self: Any, path: str) -> bytes:
        try:
            entry = current.match(path) if path.endswith(".py") else None
            data = current.served(path, entry) if entry is not None else None
        except Exception:  # noqa: BLE001 - never break an import
            data = None
        if data is not None:
            return data
        return original_get_data(self, path)

    SourceFileLoader.get_code = get_code  # type: ignore[method-assign]
    SourceFileLoader.get_data = get_data  # type: ignore[method-assign]


def _watch_supervisor(supervisor_pid: int) -> None:
    """Kill this process group if the supervising process disappears."""
    if supervisor_pid <= 0 or os.getppid() != supervisor_pid:
        return

    def watch() -> None:
        import signal

        while True:
            time.sleep(1.0)
            if os.getppid() != supervisor_pid:
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                finally:
                    os._exit(1)

    threading.Thread(target=watch, name="pytestmut-watchdog", daemon=True).start()
