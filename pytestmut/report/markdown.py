"""Markdown report, sized for a pull-request comment or a CI step summary."""

from __future__ import annotations

from pytestmut.mutation.model import Mutant, Status
from pytestmut.report.model import RunResult
from pytestmut.report.source import SourceCache
from pytestmut.report.text import label, percent, score_lines

_COUNTED = (
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
    (Status.ERROR, "Errors"),
)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _limit(value: int) -> str:
    return "" if value < 0 else f" / {value}"


def _mutant_block(mutant: Mutant, result: RunResult, sources: SourceCache) -> list[str]:
    before, after = sources.diff(mutant)
    scope = f" in `{mutant.scope}`" if mutant.scope else ""
    lines = [
        f"- `{mutant.path}:{mutant.span.line}` **{mutant.operator}**{scope} "
        f"(`{mutant.id}`)",
        "",
        "  ```diff",
    ]
    lines += [f"  - {line.strip()}" for line in before]
    lines += [f"  + {line.strip()}" for line in after]
    lines += ["  ```"]
    verdict = result.results.get(mutant.id)
    if verdict is not None and verdict.hint:
        lines += ["", f"  {verdict.hint}"]
    elif verdict is not None and (verdict.reason or verdict.detail):
        detail = verdict.detail.strip().replace("\n", " ")[:300]
        lines += ["", f"  {': '.join(p for p in (verdict.reason, detail) if p)}"]
    return lines


def render_markdown(result: RunResult, max_survivors: int = 50) -> str:
    """Render the markdown report.

    Arguments:
        result: The run result.
        max_survivors: Mutants listed per section before the rest is
            summarised in one line.

    Returns:
        The report.
    """
    sources = SourceCache(result)
    counts = result.counts()
    passed = result.exit_code == 0
    lines = [
        f"## Mutation testing: {'passed' if passed else 'failed'}",
        "",
        f"{len(result.mutants)} mutants in {len(result.files)} file(s), "
        f"{result.baseline.tests} tests, {result.baseline.workers} worker(s), "
        f"{result.duration_s:.1f}s. Scope: {result.scope}. "
        f"{result.run_label().capitalize()}.",
    ]
    if result.partial:
        lines += ["", "**Partial run:** some mutants were not run."]
    lines += ["", "```text", *[line.strip() for line in score_lines(result.policies)]]
    lines += ["```"]
    lines += [
        "",
        "| Policy | Mutants | Score | Covered | Survived | No coverage | Result |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for policy in result.policies:
        lines.append(
            f"| {_cell(policy.name)} | {policy.total} | {percent(policy.score)} | "
            f"{percent(policy.covered_score)} | "
            f"{policy.survived}{_limit(policy.thresholds.max_survivors)} | "
            f"{policy.no_coverage}{_limit(policy.thresholds.max_no_coverage)} | "
            f"{'pass' if policy.passed else '**FAIL**'} |"
        )
    failures = list(result.failures) + [
        f"{policy.name}: {message}"
        for policy in result.policies
        for message in policy.failures
    ]
    if failures:
        lines += ["", "### Failures", ""]
        lines += [f"- {message}" for message in failures]
    lines += ["", "### Mutants by status", "", "| Status | Count |", "|---|---:|"]
    lines += [
        f"| {label(status)} | {counts[status.value]} |"
        for status in _COUNTED
        if counts[status.value]
    ]
    reasons: dict[str, int] = {}
    for policy in result.policies:
        for reason, count in policy.suppressed.items():
            reasons[reason] = reasons.get(reason, 0) + count
    if reasons:
        listing = ", ".join(
            f"{name} {count}" for name, count in sorted(reasons.items())
        )
        lines += ["", f"Suppressed by reason: {listing}."]
    for status, title in _LISTED:
        mutants = result.with_status(status)
        if not mutants:
            continue
        lines += ["", f"### {title} ({len(mutants)})", ""]
        for mutant in mutants[:max_survivors]:
            lines += _mutant_block(mutant, result, sources)
        if len(mutants) > max_survivors:
            lines += ["", f"...and {len(mutants) - max_survivors} more."]
    if result.warnings:
        lines += ["", "### Warnings", ""]
        lines += [f"- {message}" for message in result.warnings]
    lines += [
        "",
        f"<sub>pytestmut {result.tool_version}, Python {result.python}, "
        f"pytest {result.pytest}</sub>",
    ]
    return "\n".join(lines) + "\n"
