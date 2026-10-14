"""Which tests reach which lines, and which tests a mutant must face.

Selection is built to fail safe. Whenever attribution is uncertain (code run
at import time, lines seen outside any test, a test whose tracing was
disturbed) the wider set of tests is chosen. A mutant that no test reaches is
reported as ``no_coverage`` and never as a survivor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pytestmut.baseline.runner import Baseline
from pytestmut.exec.jobs import TargetIdentity
from pytestmut.mutation.model import Mutant, Status

_TEST_PREFIX = "t:"
_FIXTURE_PREFIX = "f:"


@dataclass(frozen=True)
class Selection:
    """The tests chosen for one mutant.

    Attributes:
        nodeids: Tests to run in baseline order; empty when none apply.
        status: Status to assign without running, if any.
        reason: Why that status, or why the full suite was chosen.
        full: True when the selection is the whole baseline.
    """

    nodeids: tuple[str, ...] = ()
    status: Status | None = None
    reason: str = ""
    full: bool = False


@dataclass
class TestMap:
    """Lines of target files executed per test.

    Attributes:
        by_line: Tests that executed each ``(file index, line)``.
        static_lines: Lines executed outside any test or fixture.
        always: Tests selected for every mutant because their trace is
            unreliable.
        by_file: Tests during which a child process loaded each file.
        files_by_test: Target files each test executed a line of.
        mechanism: How lines were traced.
    """

    __test__ = False

    by_line: dict[tuple[int, int], set[str]] = field(default_factory=dict)
    static_lines: set[tuple[int, int]] = field(default_factory=set)
    always: set[str] = field(default_factory=set)
    by_file: dict[int, set[str]] = field(default_factory=dict)
    files_by_test: dict[str, set[int]] = field(default_factory=dict)
    mechanism: str = "none"

    @classmethod
    def from_trace(
        cls,
        trace: Mapping[str, Any],
        child_loads: Mapping[int, set[str]],
    ) -> TestMap:
        """Build the map from a trace job's output.

        Arguments:
            trace: Content of ``trace.json``.
            child_loads: Tests during which a child process loaded each
                target file, by file index.

        Returns:
            The test map.
        """
        contexts: Mapping[str, Mapping[str, list[int]]] = trace.get("contexts", {})
        fixtures: Mapping[str, list[str]] = trace.get("fixtures", {})
        result = cls(mechanism=str(trace.get("mechanism", "none")))
        fixture_lines: dict[str, set[tuple[int, int]]] = {}
        for context, files in contexts.items():
            pairs = {
                (int(index), line) for index, lines in files.items() for line in lines
            }
            if context == "":
                result.static_lines |= pairs
            elif context.startswith(_FIXTURE_PREFIX):
                fixture_lines[context[len(_FIXTURE_PREFIX) :]] = pairs
            elif context.startswith(_TEST_PREFIX):
                nodeid = context[len(_TEST_PREFIX) :]
                for pair in pairs:
                    result.by_line.setdefault(pair, set()).add(nodeid)
        # A shared fixture is set up once, inside whichever test asks first;
        # every test that uses it depends on the lines its setup executed.
        for nodeid, names in fixtures.items():
            for name in names:
                for pair in fixture_lines.get(name, ()):
                    result.by_line.setdefault(pair, set()).add(nodeid)
        for context in trace.get("clobbered", ()):
            if context.startswith(_TEST_PREFIX):
                result.always.add(context[len(_TEST_PREFIX) :])
        result.by_file = {index: set(tests) for index, tests in child_loads.items()}
        for (index, _line), tests in result.by_line.items():
            for nodeid in tests:
                result.files_by_test.setdefault(nodeid, set()).add(index)
        for index, tests in result.by_file.items():
            for nodeid in tests:
                result.files_by_test.setdefault(nodeid, set()).add(index)
        return result

    def tests_for(self, index: int, first_line: int, last_line: int) -> set[str]:
        """Tests that executed any line of a range.

        Arguments:
            index: Index of the target file.
            first_line: First line of the range.
            last_line: Last line of the range.

        Returns:
            Node ids of the tests.
        """
        found: set[str] = set()
        for line in range(first_line, last_line + 1):
            found |= self.by_line.get((index, line), set())
        return found

    def is_static(self, index: int, first_line: int, last_line: int) -> bool:
        """True when a line of the range ran outside any test."""
        return any(
            (index, line) in self.static_lines
            for line in range(first_line, last_line + 1)
        )


class Selector:
    """Chooses the tests each mutant runs against.

    Arguments:
        baseline: The validated baseline.
        targets: Every target file of the run.
        test_map: Per-test line map, or None to run all tests always.
    """

    def __init__(
        self,
        baseline: Baseline,
        targets: tuple[TargetIdentity, ...],
        test_map: TestMap | None,
    ) -> None:
        self._baseline = baseline
        self._index = {target.relpath: target.index for target in targets}
        self._map = test_map
        self._all = tuple(n for n in baseline.nodeids if n in baseline.passing)

    @property
    def all_tests(self) -> tuple[str, ...]:
        """Every test that can kill, in baseline order."""
        return self._all

    def files_touched(self, nodeids: tuple[str, ...]) -> set[int] | None:
        """Target files the given tests execute.

        Arguments:
            nodeids: Tests to look up.

        Returns:
            Indices of the files, or None when no line map exists and every
            file must be assumed.
        """
        if self._map is None:
            return None
        touched: set[int] = set()
        for nodeid in nodeids:
            touched |= self._map.files_by_test.get(nodeid, set())
        return touched

    def select(
        self, mutant: Mutant, exclude: frozenset[str] = frozenset()
    ) -> Selection:
        """Choose the tests for one mutant.

        Arguments:
            mutant: The mutant.
            exclude: Tests to leave out, e.g. ones found to be order
                dependent.

        Returns:
            The selection, or a status when the mutant should not run.
        """
        if mutant.path in self._baseline.unsupported:
            loader = self._baseline.unsupported[mutant.path]
            return Selection(
                status=Status.UNSUPPORTED,
                reason=f"loaded by {loader}, which the import hook cannot reach",
            )
        if mutant.path not in self._baseline.loaded:
            return Selection(
                status=Status.NO_COVERAGE, reason="file never imported by the tests"
            )
        everything = tuple(n for n in self._all if n not in exclude)
        if self._map is None:
            return self._full(everything, "selection = all")
        index = self._index[mutant.path]
        first, last = mutant.cover_lines
        if mutant.static:
            return self._full(everything, "runs at import time")
        if mutant.memoised:
            # A test reading a cached result never ran these lines but still
            # depends on them, so the line map cannot be trusted here.
            return self._full(everything, "inside a memoised function")
        if self._map.is_static(index, first, last):
            return self._full(everything, "line also runs outside any test")
        wanted = self._map.tests_for(index, first, last)
        wanted |= self._map.always | self._map.by_file.get(index, set())
        chosen = tuple(n for n in everything if n in wanted)
        if not chosen:
            return Selection(status=Status.NO_COVERAGE, reason="no test reaches it")
        return Selection(nodeids=chosen, full=len(chosen) == len(self._all))

    def _full(self, everything: tuple[str, ...], reason: str) -> Selection:
        if not everything:
            return Selection(status=Status.NO_COVERAGE, reason="no usable tests")
        return Selection(nodeids=everything, reason=reason, full=True)
