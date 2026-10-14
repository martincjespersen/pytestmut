"""Data model for mutants and their results."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    """Final state of one mutant."""

    KILLED = "killed"
    SURVIVED = "survived"
    NO_COVERAGE = "no_coverage"
    TIMEOUT = "timeout"
    OOM = "oom"
    ERROR = "error"
    NOT_RUN = "not_run"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    EQUIVALENT = "equivalent"
    DUPLICATE = "duplicate"
    SUPPRESSED = "suppressed"


# Statuses a mutant can carry before any test has run.
PRE_RUN_STATUSES = frozenset(
    {Status.SUPPRESSED, Status.INVALID, Status.EQUIVALENT, Status.DUPLICATE}
)


@dataclass(frozen=True)
class Site:
    """A candidate mutation found by an operator.

    Attributes:
        operator: Operator id, e.g. ``cmp.boundary``.
        start: Character index of the first replaced character.
        end: Character index one past the last replaced character.
        replacement: Text to put there.
        node: Syntax node the mutation belongs to, used for scope and rules.
    """

    operator: str
    start: int
    end: int
    replacement: str
    node: ast.AST


@dataclass(frozen=True)
class Splice:
    """A replacement expressed on the raw bytes of the file.

    Attributes:
        start: Byte offset of the first replaced byte.
        end: Byte offset one past the last replaced byte.
        replacement: Bytes to put there, in the file's own encoding.
    """

    start: int
    end: int
    replacement: bytes


@dataclass(frozen=True)
class SourceSpan:
    """A region of source for display.

    Attributes:
        line: 1-based first line.
        col: 0-based first column in characters.
        end_line: 1-based last line.
        end_col: 0-based column one past the last character.
    """

    line: int
    col: int
    end_line: int
    end_col: int


@dataclass(frozen=True)
class Mutant:
    """One mutation of one file.

    Attributes:
        id: Stable identifier derived from path, scope, operator, original
            text, replacement and occurrence; unaffected by line shifts.
        path: POSIX path relative to the project root.
        scope: Qualified name of the enclosing definition, empty at module
            level.
        operator: Operator id.
        span: Where the original text sits.
        splice: The replacement on raw bytes.
        original: Text being replaced.
        replacement: Text replacing it.
        static: True when the code runs at import time.
        memoised: True when the code runs inside a function whose result is
            cached, so tests can depend on it without executing it.
        policy: Name of the policy the mutant belongs to.
        cover_lines: Inclusive line range whose execution reaches the mutant.
        pre_status: Status decided before running any test, if any.
        reason: Reason code or message for ``pre_status``.
        duplicate_of: Id of the mutant this one duplicates.
    """

    id: str
    path: str
    scope: str
    operator: str
    span: SourceSpan
    splice: Splice
    original: str
    replacement: str
    static: bool
    policy: str
    cover_lines: tuple[int, int]
    pre_status: Status | None = None
    reason: str = ""
    duplicate_of: str | None = None
    memoised: bool = False

    @property
    def runnable(self) -> bool:
        """True when the mutant still needs a verdict from the tests."""
        return self.pre_status is None


@dataclass(frozen=True)
class MutantResult:
    """The verdict for one mutant.

    Attributes:
        mutant_id: Id of the mutant.
        status: Final status.
        reason: Reason code qualifying the status.
        killing_test: Node id of the first failing test, for kills.
        kill_phase: ``setup``, ``call`` or ``teardown`` for kills.
        tests_selected: How many tests were chosen to run.
        tests_run: How many of them reported an outcome.
        duration_s: Wall-clock time spent on the mutant.
        attempts: Number of process runs made for it.
        from_cache: True when the verdict was reused from an earlier run.
        detail: Free text for humans, e.g. the tail of the output.
        tests: Node ids of the tests run against the mutant, at most ten.
        hint: What a surviving or unreached mutant asks of the tests.
    """

    mutant_id: str
    status: Status
    reason: str = ""
    killing_test: str | None = None
    kill_phase: str | None = None
    tests_selected: int = 0
    tests_run: int = 0
    duration_s: float = 0.0
    attempts: int = 0
    from_cache: bool = False
    detail: str = ""
    tests: tuple[str, ...] = ()
    hint: str = ""
