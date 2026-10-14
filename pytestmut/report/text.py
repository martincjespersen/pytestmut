"""Plain-text report for terminals and logs.

Output is ASCII only so it prints on any console, including Windows code
pages that cannot encode box-drawing characters.
"""

from __future__ import annotations

import textwrap

from pytestmut.mutation.model import Mutant, Status
from pytestmut.policy.evaluate import PolicyResult, overall_scores
from pytestmut.report.model import RunResult
from pytestmut.report.source import SourceCache

_STATUS_ORDER = (
    Status.KILLED,
    Status.SURVIVED,
    Status.NO_COVERAGE,
    Status.TIMEOUT,
    Status.OOM,
    Status.ERROR,
    Status.NOT_RUN,
    Status.UNSUPPORTED,
    Status.SUPPRESSED,
    Status.EQUIVALENT,
    Status.DUPLICATE,
    Status.INVALID,
)
_LISTED = (
    (Status.SURVIVED, "Survivors"),
    (Status.NO_COVERAGE, "No coverage"),
    (Status.TIMEOUT, "Timeouts"),
    (Status.OOM, "Out of memory"),
    (Status.ERROR, "Errors"),
)
# Stands in for spaces inside code spans while text is wrapped.
_KEEP = "\x00"
_LABELS = {Status.NO_COVERAGE: "no coverage", Status.NOT_RUN: "not run"}


def label(status: Status) -> str:
    """Human name of a status."""
    return _LABELS.get(status, status.value)


def percent(value: float | None) -> str:
    """Format a score, or a dash when there is nothing to score."""
    return "-" if value is None else f"{value:.1f}%"


def _limit(value: int) -> str:
    return "-" if value < 0 else str(value)


def score_lines(policies: tuple[PolicyResult, ...]) -> list[str]:
    """Render both scores with the counts they were computed from.

    Arguments:
        policies: Policy results.

    Returns:
        Two lines, or one saying there was nothing to score.
    """
    score, covered = overall_scores(policies)
    if score is None:
        return ["  score          -  (no mutant was scored)"]
    detected = sum(policy.detected for policy in policies)
    uncovered = sum(policy.no_coverage for policy in policies)
    missed = sum(
        policy.survived
        + (0 if policy.thresholds.count_timeouts_as_killed else policy.timeouts)
        for policy in policies
    )
    scored = detected + missed + uncovered
    return [
        f"  score          {percent(score):>6}  ({detected} detected of {scored} "
        f"scored: {missed} survived, {uncovered} without coverage)",
        f"  covered score  {percent(covered):>6}  ({detected} detected of "
        f"{detected + missed} that a test reaches)",
    ]


def policy_table(policies: tuple[PolicyResult, ...]) -> list[str]:
    """Render the per-policy table.

    Arguments:
        policies: Policy results.

    Returns:
        Table lines.
    """
    header = ("Policy", "Mutants", "Score", "Covered", "Survived", "No cov", "Result")
    rows = [header]
    for policy in policies:
        rows.append(
            (
                policy.name,
                str(policy.total),
                percent(policy.score),
                percent(policy.covered_score),
                f"{policy.survived}/{_limit(policy.thresholds.max_survivors)}",
                f"{policy.no_coverage}/{_limit(policy.thresholds.max_no_coverage)}",
                "pass" if policy.passed else "FAIL",
            )
        )
    widths = [max(len(row[col]) for row in rows) for col in range(len(header))]
    lines = []
    for row in rows:
        cells = [row[0].ljust(widths[0])]
        cells += [cell.rjust(widths[col]) for col, cell in enumerate(row) if col]
        lines.append("  " + "  ".join(cells).rstrip())
    return lines


def _wrap(text: str) -> list[str]:
    """Wrap prose without splitting a `code span`, so it can be copied whole."""
    spans = text.split("`")
    for index in range(1, len(spans), 2):
        spans[index] = spans[index].replace(" ", _KEEP)
    wrapped = textwrap.wrap(
        "`".join(spans),
        width=88,
        initial_indent="      ",
        subsequent_indent="      ",
        break_on_hyphens=False,
        break_long_words=False,
    )
    return [line.replace(_KEEP, " ") for line in wrapped]


def _mutant_lines(result: RunResult, mutant: Mutant, sources: SourceCache) -> list[str]:
    where = f"{mutant.path}:{mutant.span.line}"
    scope = f"  in {mutant.scope}" if mutant.scope else ""
    lines = [f"  {where}  {mutant.operator}  [{mutant.id}]{scope}"]
    before, after = sources.diff(mutant)
    lines += [f"      - {line.strip()}" for line in before]
    lines += [f"      + {line.strip()}" for line in after]
    verdict = result.results.get(mutant.id)
    if verdict is not None and verdict.hint:
        lines += _wrap(verdict.hint)
    elif verdict is not None and (verdict.reason or verdict.detail):
        parts = [verdict.reason, verdict.detail.strip()[:300]]
        lines.append("      " + ": ".join(part for part in parts if part))
    return lines


def render_text(result: RunResult, show_suppressed: bool = False) -> str:
    """Render the text report.

    Arguments:
        result: The run result.
        show_suppressed: Also list suppressed candidates.

    Returns:
        The report.
    """
    counts = result.counts()
    sources = SourceCache(result)
    lines = [
        f"pytestmut {result.tool_version}: {len(result.mutants)} mutants in "
        f"{len(result.files)} file(s), {result.baseline.tests} tests, "
        f"{result.baseline.workers} worker(s), {result.duration_s:.1f}s",
    ]
    if result.scope != "full":
        lines.append(f"scope: {result.scope}")
    if result.partial:
        lines.append("PARTIAL RUN: some mutants were not run; scores cover the rest")
    lines.append("")
    for status in _STATUS_ORDER:
        if counts[status.value]:
            lines.append(f"  {label(status):<12} {counts[status.value]:>6}")
    reasons: dict[str, int] = {}
    for policy in result.policies:
        for reason, count in policy.suppressed.items():
            reasons[reason] = reasons.get(reason, 0) + count
    if reasons:
        listing = ", ".join(
            f"{name} {count}" for name, count in sorted(reasons.items())
        )
        lines.append(f"  suppressed by reason: {listing}")
    lines += ["", *score_lines(result.policies)]
    lines += ["", *policy_table(result.policies)]
    failed = [policy for policy in result.policies if not policy.passed]
    if failed or result.failures:
        lines += ["", "Failures"]
        lines += [f"  {message}" for message in result.failures]
        for policy in failed:
            lines += [f"  {policy.name}: {message}" for message in policy.failures]
    for status, title in _LISTED:
        mutants = result.with_status(status)
        if mutants:
            lines += ["", f"{title} ({len(mutants)})"]
            for mutant in mutants:
                lines += _mutant_lines(result, mutant, sources)
    if show_suppressed:
        suppressed = result.with_status(Status.SUPPRESSED)
        if suppressed:
            lines += ["", f"Suppressed ({len(suppressed)})"]
            lines += [
                f"  {m.path}:{m.span.line}  {m.operator}  {m.reason}"
                for m in suppressed
            ]
    empty = [
        f"{item.path}::{scope}" for item in result.files for scope in item.empty_scopes
    ]
    if empty:
        lines += ["", f"Functions with no mutants ({len(empty)})"]
        lines += [f"  {name}" for name in empty[:20]]
        if len(empty) > 20:
            lines.append(f"  ... and {len(empty) - 20} more")
    notes = [(item.path, item.error or item.note) for item in result.files]
    notes = [(path, text) for path, text in notes if text]
    if notes:
        lines += ["", "Files"]
        lines += [f"  {path}: {text}" for path, text in notes]
    if result.baseline.quarantined:
        lines += ["", f"Quarantined tests ({len(result.baseline.quarantined)})"]
        lines += [
            f"  {nodeid}: {why}"
            for nodeid, why in sorted(result.baseline.quarantined.items())
        ]
    if result.warnings:
        lines += ["", "Warnings"]
        lines += [f"  {message}" for message in result.warnings]
    lines += ["", _verdict_line(result)]
    return "\n".join(lines) + "\n"


def _verdict_line(result: RunResult) -> str:
    if result.exit_code == 0:
        return "PASSED: every policy met its thresholds"
    if result.exit_code == 1 and all(policy.passed for policy in result.policies):
        return (
            "FAILED: the run hit its time limit; mutants that did not run cannot pass"
        )
    if result.exit_code == 1:
        return "FAILED: at least one policy missed its thresholds"
    return f"FAILED (exit code {result.exit_code})"
