"""Run the orchestrator and deliver its results, for any front end.

Shared by the ``pytestmut run`` command and the ``pytest --mutate`` plugin so
both write the same files, print the same report and exit the same way.
"""

from __future__ import annotations

import contextlib
import signal
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator

from pytestmut.config.model import Config
from pytestmut.exceptions import PytestmutError
from pytestmut.mutation.model import Mutant, MutantResult
from pytestmut.orchestrator import Orchestrator, RunOptions
from pytestmut.report.model import to_json
from pytestmut.report.render import render, write_reports

Echo = Callable[[str], None]
INTERRUPTED = 130


def _stderr(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def _stdout(message: str) -> None:
    sys.stdout.write(message)
    sys.stdout.flush()


class Console:
    """Progress output on stderr, ASCII only.

    Arguments:
        quiet: Print nothing.
    """

    def __init__(self, quiet: bool = False) -> None:
        self._quiet = quiet
        self._tty = sys.stderr.isatty()
        self._counts: Counter[str] = Counter()
        self._last = 0.0
        self._open_line = False

    def notify(self, message: str) -> None:
        """Print a one-line status message."""
        if self._quiet:
            return
        self.close()
        _stderr(f"pytestmut: {message}")

    def progress(
        self, done: int, total: int, mutant: Mutant, result: MutantResult
    ) -> None:
        """Show running totals; rewritten in place on a terminal."""
        self._counts[result.status.value] += 1
        if self._quiet:
            return
        now = time.monotonic()
        interval = 0.1 if self._tty else 15.0
        if done != total and now - self._last < interval:
            return
        self._last = now
        summary = "  ".join(
            f"{name} {count}" for name, count in sorted(self._counts.items())
        )
        line = f"[{done}/{total}] {summary}"
        if self._tty:
            sys.stderr.write("\r" + line.ljust(78)[:118])
            sys.stderr.flush()
            self._open_line = True
        else:
            _stderr(line)

    def close(self) -> None:
        """Finish a progress line left open on a terminal."""
        if self._open_line:
            sys.stderr.write("\n")
            sys.stderr.flush()
            self._open_line = False


@contextlib.contextmanager
def _terminate_as_interrupt() -> Iterator[None]:
    """Treat SIGTERM like Ctrl-C while a run is in progress.

    CI systems cancel jobs, and ``timeout`` ends commands, with SIGTERM.
    Python's default for it is to die on the spot, leaving test processes to
    their own watchdogs; as an interrupt, the run instead stops its workers,
    removes its scratch space and exits with code 130.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_and_report(
    config: Config,
    options: RunOptions = RunOptions(),
    quiet: bool = False,
    show_suppressed: bool = False,
    out: Echo = _stdout,
    err: Echo = _stderr,
) -> int:
    """Run mutation testing and write every configured report.

    Arguments:
        config: The validated configuration.
        options: Per-invocation choices.
        quiet: Suppress progress output.
        show_suppressed: List suppressed candidates in the text report.
        out: Receives the text report.
        err: Receives error and file-location messages.

    Returns:
        The process exit code.
    """
    console = Console(quiet)
    orchestrator = Orchestrator(
        config, notify=console.notify, progress=console.progress
    )
    try:
        with _terminate_as_interrupt():
            result = orchestrator.run(options)
    except PytestmutError as exc:
        console.close()
        err(f"pytestmut: error: {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        orchestrator.cancel()
        console.close()
        err("pytestmut: interrupted")
        return INTERRUPTED
    console.close()
    orchestrator.state.results_file.write_text(
        to_json(result), encoding="utf-8", newline="\n"
    )
    formats = config.report.formats
    if "text" in formats:
        out(render(result, "text", show_suppressed=show_suppressed))
    written = write_reports(
        result,
        tuple(fmt for fmt in formats if fmt != "text"),
        config.root / config.report.output_dir,
        config.report.markdown_max_survivors,
        config.report.html_embed_source,
    )
    for fmt, path in written.items():
        err(f"pytestmut: {fmt} report: {path}")
    return result.exit_code
