"""Per-test line tracer for the baseline run.

Records which lines of the target files each test executes, so a mutant only
runs against tests that reach it. coverage.py is deliberately not used: its
default core on Python 3.14 cannot attribute lines to tests, and a second
coverage instance fights the project's own.

On Python 3.12+ this uses ``sys.monitoring`` under its own tool id. Each line
event disables itself after the first hit and ``restart_events`` re-arms them
at every context switch, so the cost is one callback per line per test. Older
interpreters fall back to ``sys.settrace``.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

FileIndex = Callable[[str], "int | None"]


class Tracer:
    """Collects ``(file, line)`` pairs per named context.

    Arguments:
        file_index: Maps a code object's filename to the index of a target
            file, or None for files that are not targets.
    """

    def __init__(self, file_index: FileIndex) -> None:
        self._file_index = file_index
        self._data: dict[str, set[tuple[int, int]]] = {"": set()}
        self._current = self._data[""]
        self._context = ""
        self._tool: int | None = None
        self._known: dict[str, int | None] = {}
        # Bound once: each attribute access makes a new bound method object,
        # and the check for a replaced trace function compares identity.
        self._trace = self._global
        self.clobbered: list[str] = []
        self.mechanism = "none"

    @property
    def context(self) -> str:
        """Name of the context lines are currently attributed to."""
        return self._context

    def _index(self, filename: str) -> int | None:
        try:
            return self._known[filename]
        except KeyError:
            index = self._file_index(filename)
            self._known[filename] = index
            return index

    def start(self) -> None:
        """Begin tracing in this process."""
        if sys.version_info >= (3, 12):
            self._start_monitoring()
        else:
            self._start_settrace()

    def switch(self, context: str) -> None:
        """Attribute the lines that follow to another context.

        Arguments:
            context: Context name; the empty string means "outside any test".
        """
        previous = self._context
        self._context = context
        self._current = self._data.setdefault(context, set())
        if self._tool is not None:
            if sys.version_info >= (3, 12):
                sys.monitoring.restart_events()
        elif self.mechanism == "settrace" and sys.gettrace() is not self._trace:
            # A test replaced the trace function, so the context that just
            # ended has an incomplete line set.
            self.clobbered.append(previous)
            sys.settrace(self._trace)

    def stop(self) -> dict[str, dict[str, list[int]]]:
        """Stop tracing and return what was collected.

        Returns:
            ``{context: {file index as text: sorted lines}}``.
        """
        if self._tool is not None:
            if sys.version_info >= (3, 12):
                sys.monitoring.set_events(self._tool, 0)
                sys.monitoring.free_tool_id(self._tool)
            self._tool = None
        elif self.mechanism == "settrace":
            sys.settrace(None)
            threading.settrace(None)
        result: dict[str, dict[str, list[int]]] = {}
        for context, pairs in self._data.items():
            by_file: dict[str, list[int]] = {}
            for index, line in pairs:
                by_file.setdefault(str(index), []).append(line)
            result[context] = {key: sorted(lines) for key, lines in by_file.items()}
        return result

    def _start_monitoring(self) -> None:
        if sys.version_info < (3, 12):
            return
        monitoring = sys.monitoring
        events = monitoring.events
        for candidate in (4, 3, 2, 1):
            if monitoring.get_tool(candidate) is None:
                tool = candidate
                break
        else:
            self._start_settrace()
            return
        monitoring.use_tool_id(tool, "pytestmut")
        self._tool = tool
        self.mechanism = "monitoring"
        disable = monitoring.DISABLE

        def on_start(code: Any, offset: int) -> Any:
            if self._index(code.co_filename) is not None:
                monitoring.set_local_events(tool, code, events.LINE)
            return disable

        def on_line(code: Any, line: int) -> Any:
            index = self._index(code.co_filename)
            if index is not None:
                self._current.add((index, line))
            return disable

        monitoring.register_callback(tool, events.PY_START, on_start)
        monitoring.register_callback(tool, events.LINE, on_line)
        monitoring.set_events(tool, events.PY_START)

    def _start_settrace(self) -> None:
        self.mechanism = "settrace"
        sys.settrace(self._trace)
        threading.settrace(self._trace)

    def _global(self, frame: Any, event: str, arg: Any) -> Any:
        index = self._index(frame.f_code.co_filename)
        if index is None:
            return None
        self._current.add((index, frame.f_lineno))
        return self._local

    def _local(self, frame: Any, event: str, arg: Any) -> Any:
        if event == "line":
            index = self._index(frame.f_code.co_filename)
            if index is not None:
                self._current.add((index, frame.f_lineno))
        return self._local
