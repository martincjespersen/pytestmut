"""Self-contained HTML report.

One file, no network access: the page is a fixed template with the run data
embedded as JSON and rendered by inline script.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytestmut.policy.evaluate import overall_scores
from pytestmut.report.model import RunResult
from pytestmut.report.source import SourceCache

_TEMPLATE = Path(__file__).resolve().parent / "assets" / "template.html"
_PLACEHOLDER = "__DATA__"
_DETAIL_LIMIT = 400


def _payload(result: RunResult, embed_source: bool) -> dict[str, Any]:
    sources = SourceCache(result)
    by_file: dict[str, list[dict[str, Any]]] = {item.path: [] for item in result.files}
    for mutant in result.mutants:
        verdict = result.results.get(mutant.id)
        by_file.setdefault(mutant.path, []).append(
            {
                "id": mutant.id,
                "line": mutant.span.line,
                "operator": mutant.operator,
                "scope": mutant.scope,
                "policy": mutant.policy,
                "status": result.status_of(mutant).value,
                "original": mutant.original,
                "replacement": mutant.replacement,
                "reason": verdict.reason if verdict else mutant.reason,
                "detail": verdict.detail.strip()[:_DETAIL_LIMIT] if verdict else "",
                "killing_test": (verdict.killing_test or "") if verdict else "",
                "tests": verdict.tests_selected if verdict else 0,
                "hint": verdict.hint if verdict else "",
            }
        )
    files = []
    for item in result.files:
        source = sources.get(item.path) if embed_source else None
        lines = (
            [source.line_text(number) for number in range(1, source.line_count + 1)]
            if source is not None
            else []
        )
        files.append(
            {
                "path": item.path,
                "note": item.error or item.note,
                "lines": lines,
                "mutants": by_file.get(item.path, []),
            }
        )
    score, covered = overall_scores(result.policies)
    return {
        "version": result.tool_version,
        "started": result.started_at,
        "duration": result.duration_s,
        "python": result.python,
        "pytest": result.pytest,
        "scope": result.scope,
        "partial": result.partial,
        "exit_code": result.exit_code,
        "mutants": len(result.mutants),
        "tests": result.baseline.tests,
        "workers": result.baseline.workers,
        "score": score,
        "covered_score": covered,
        "failures": list(result.failures)
        + [
            f"{policy.name}: {message}"
            for policy in result.policies
            for message in policy.failures
        ],
        "warnings": list(result.warnings),
        "policies": [
            {
                "name": policy.name,
                "total": policy.total,
                "score": policy.score,
                "covered_score": policy.covered_score,
                "survived": policy.survived,
                "no_coverage": policy.no_coverage,
                "timeouts": policy.timeouts,
                "suppressed": sum(policy.suppressed.values()),
                "max_survivors": policy.thresholds.max_survivors,
                "max_no_coverage": policy.thresholds.max_no_coverage,
                "max_timeouts": policy.thresholds.max_timeouts,
                "passed": policy.passed,
            }
            for policy in result.policies
        ],
        "files": files,
    }


def render_html(result: RunResult, embed_source: bool = True) -> str:
    """Render the HTML report.

    Arguments:
        result: The run result.
        embed_source: Include the source of each file so mutants are shown in
            place; without it they are listed by line number.

    Returns:
        A complete HTML document.
    """
    data = json.dumps(_payload(result, embed_source), ensure_ascii=True)
    # Keep the embedded JSON from closing its own script element.
    data = data.replace("</", "<\\/")
    template = _TEMPLATE.read_text(encoding="utf-8")
    return template.replace(_PLACEHOLDER, data)
