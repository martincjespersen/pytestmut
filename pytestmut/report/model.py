"""The canonical result of a run and its JSON form.

Every report is rendered from a ``RunResult``, and ``results.json`` is that
object serialised, so ``pytestmut report`` can re-render any format later
without running a test.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytestmut._meta import RESULT_SCHEMA_VERSION
from pytestmut.config.model import Thresholds
from pytestmut.exceptions import ConfigError
from pytestmut.mutation.model import (
    Mutant,
    MutantResult,
    SourceSpan,
    Splice,
    Status,
)
from pytestmut.policy.evaluate import PolicyResult, final_status


@dataclass(frozen=True)
class FileReport:
    """One target file in the result.

    Attributes:
        path: POSIX path relative to the project root.
        sha256: Digest of the bytes the mutants were built from.
        error: Why the file could not be processed, if it could not.
        note: Something the user should know, e.g. never imported.
        empty_scopes: Functions that produced no runnable mutant.
    """

    path: str
    sha256: str = ""
    error: str = ""
    note: str = ""
    empty_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BaselineSummary:
    """Facts about the baseline shown in reports.

    Attributes:
        tests: Number of tests in the baseline.
        wall_s: Wall-clock time of the baseline run.
        workers: Workers used for mutants.
        selection: Test selection mode that was used.
        quarantined: Tests excluded from kill credit, with the reason.
    """

    tests: int = 0
    wall_s: float = 0.0
    workers: int = 1
    selection: str = "all"
    quarantined: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """Everything a report needs.

    Attributes:
        schema: Version of this structure.
        tool_version: Version of pytestmut that produced it.
        started_at: ISO timestamp of the start of the run.
        duration_s: Wall-clock time of the whole run.
        project_root: Absolute path of the project.
        python: Interpreter version.
        pytest: pytest version.
        platform: Platform name.
        scope: ``full`` or a description of the diff scope.
        partial: True when some mutants were left unrun on purpose.
        files: Target files.
        mutants: Every candidate mutant.
        results: Verdicts by mutant id.
        policies: Per-policy evaluation.
        baseline: Baseline facts.
        warnings: Things the user should know.
        failures: Reasons the run failed apart from policy thresholds.
        exit_code: Process exit code of the run.
    """

    schema: int = RESULT_SCHEMA_VERSION
    tool_version: str = ""
    started_at: str = ""
    duration_s: float = 0.0
    project_root: str = ""
    python: str = ""
    pytest: str = ""
    platform: str = ""
    scope: str = "full"
    partial: bool = False
    files: tuple[FileReport, ...] = ()
    mutants: tuple[Mutant, ...] = ()
    results: dict[str, MutantResult] = field(default_factory=dict)
    policies: tuple[PolicyResult, ...] = ()
    baseline: BaselineSummary = field(default_factory=BaselineSummary)
    warnings: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    exit_code: int = 0

    def status_of(self, mutant: Mutant) -> Status:
        """Final status of a mutant."""
        return final_status(mutant, self.results)

    def counts(self) -> Counter[str]:
        """Number of mutants per status value, over the whole run."""
        return Counter(self.status_of(mutant).value for mutant in self.mutants)

    def with_status(self, *statuses: Status) -> list[Mutant]:
        """Mutants whose final status is one of the given ones."""
        wanted = set(statuses)
        return [mutant for mutant in self.mutants if self.status_of(mutant) in wanted]


def _plain(value: Any) -> Any:
    if isinstance(value, Status):
        return value.value
    if isinstance(value, bytes):
        return value.hex()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _plain(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return [_plain(item) for item in items]
    return value


def to_json(result: RunResult) -> str:
    """Serialise a result.

    Arguments:
        result: The run result.

    Returns:
        Indented JSON text.
    """
    return json.dumps(_plain(result), indent=2, sort_keys=False) + "\n"


def _mutant(data: dict[str, Any]) -> Mutant:
    splice = data["splice"]
    status = data.get("pre_status")
    return Mutant(
        id=data["id"],
        path=data["path"],
        scope=data["scope"],
        operator=data["operator"],
        span=SourceSpan(**data["span"]),
        splice=Splice(
            start=splice["start"],
            end=splice["end"],
            replacement=bytes.fromhex(splice["replacement"]),
        ),
        original=data["original"],
        replacement=data["replacement"],
        static=data["static"],
        policy=data["policy"],
        cover_lines=(data["cover_lines"][0], data["cover_lines"][1]),
        pre_status=Status(status) if status else None,
        reason=data.get("reason", ""),
        duplicate_of=data.get("duplicate_of"),
        memoised=bool(data.get("memoised", False)),
    )


def _result(data: dict[str, Any]) -> MutantResult:
    fields = {item.name for item in dataclasses.fields(MutantResult)}
    known = {key: value for key, value in data.items() if key in fields}
    known["status"] = Status(known["status"])
    known["tests"] = tuple(known.get("tests", ()))
    return MutantResult(**known)


def _policy(data: dict[str, Any]) -> PolicyResult:
    fields = {item.name for item in dataclasses.fields(PolicyResult)}
    known = {key: value for key, value in data.items() if key in fields}
    known["failures"] = tuple(known.get("failures", ()))
    known["thresholds"] = Thresholds(**known.get("thresholds", {}))
    return PolicyResult(**known)


def from_json(text: str) -> RunResult:
    """Parse a serialised result.

    Arguments:
        text: Content of ``results.json``.

    Returns:
        The run result.

    Raises:
        ConfigError: If the text is not a result this version can read.
    """
    try:
        data = json.loads(text)
        if data.get("schema") != RESULT_SCHEMA_VERSION:
            raise ConfigError(
                f"results were written with schema {data.get('schema')}, this "
                f"version reads schema {RESULT_SCHEMA_VERSION}; run again"
            )
        baseline = data.get("baseline", {})
        return RunResult(
            schema=data["schema"],
            tool_version=data.get("tool_version", ""),
            started_at=data.get("started_at", ""),
            duration_s=data.get("duration_s", 0.0),
            project_root=data.get("project_root", ""),
            python=data.get("python", ""),
            pytest=data.get("pytest", ""),
            platform=data.get("platform", ""),
            scope=data.get("scope", "full"),
            partial=data.get("partial", False),
            files=tuple(
                FileReport(
                    path=item["path"],
                    sha256=item.get("sha256", ""),
                    error=item.get("error", ""),
                    note=item.get("note", ""),
                    empty_scopes=tuple(item.get("empty_scopes", ())),
                )
                for item in data.get("files", ())
            ),
            mutants=tuple(_mutant(item) for item in data.get("mutants", ())),
            results={
                key: _result(item) for key, item in data.get("results", {}).items()
            },
            policies=tuple(_policy(item) for item in data.get("policies", ())),
            baseline=BaselineSummary(
                tests=baseline.get("tests", 0),
                wall_s=baseline.get("wall_s", 0.0),
                workers=baseline.get("workers", 1),
                selection=baseline.get("selection", "all"),
                quarantined=dict(baseline.get("quarantined", {})),
            ),
            warnings=tuple(data.get("warnings", ())),
            failures=tuple(data.get("failures", ())),
            exit_code=data.get("exit_code", 0),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"cannot read results: {exc}") from exc


def load_result(path: Path) -> RunResult:
    """Read ``results.json``.

    Arguments:
        path: File to read.

    Returns:
        The run result.

    Raises:
        ConfigError: If the file is missing or unreadable.
    """
    try:
        return from_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(
            f"no results at {path}; run 'pytestmut run' first ({exc})"
        ) from exc
