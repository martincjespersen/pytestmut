"""Report rendering and the result file."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from pytestmut.config.model import Thresholds
from pytestmut.exceptions import ConfigError
from pytestmut.mutation.model import Mutant, MutantResult, SourceSpan, Splice, Status
from pytestmut.policy.evaluate import PolicyResult
from pytestmut.report.model import (
    BaselineSummary,
    FileReport,
    RunResult,
    from_json,
    load_result,
    to_json,
)
from pytestmut.report.render import FILE_NAMES, render, write_reports
from pytestmut.report.source import SourceCache

SOURCE = "def total(price, quantity):\n    return price * quantity  # café\n"


def _mutant(identifier: str, operator: str, original: str, replacement: str) -> Mutant:
    col = SOURCE.splitlines()[1].index(original)
    return Mutant(
        id=identifier,
        path="shop/pricing.py",
        scope="total",
        operator=operator,
        span=SourceSpan(2, col, 2, col + len(original)),
        splice=Splice(0, 1, replacement.encode()),
        original=original,
        replacement=replacement,
        static=False,
        policy="default",
        cover_lines=(2, 2),
    )


@pytest.fixture
def result(tmp_path: Path) -> RunResult:
    path = tmp_path / "shop" / "pricing.py"
    path.parent.mkdir()
    path.write_bytes(SOURCE.encode("utf-8"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    killed = _mutant("aaaaaaaaaaaa", "arith.mul_div", "*", "/")
    survived = _mutant("bbbbbbbbbbbb", "return.none", "price * quantity", "None")
    hidden = Mutant(
        **{
            **_mutant("cccccccccccc", "num.shift", "price", "x").__dict__,
            "pre_status": Status.SUPPRESSED,
            "reason": "inline",
        }
    )
    return RunResult(
        tool_version="0.1.0",
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=1.5,
        project_root=str(tmp_path),
        python="3.13.0",
        pytest="9.0.0",
        platform="test",
        files=(
            FileReport(path="shop/pricing.py", sha256=digest, empty_scopes=("helper",)),
            FileReport(path="shop/broken.py", error="cannot parse <script>"),
        ),
        mutants=(killed, survived, hidden),
        results={
            killed.id: MutantResult(
                killed.id, Status.KILLED, "test", "tests/t.py::test_total", "call", 3, 1
            ),
            survived.id: MutantResult(survived.id, Status.SURVIVED, tests_selected=3),
        },
        policies=(
            PolicyResult(
                name="default",
                total=3,
                counts={"killed": 1, "survived": 1, "suppressed": 1},
                suppressed={"inline": 1},
                detected=1,
                survived=1,
                score=50.0,
                covered_score=50.0,
                failures=("score 50.0% is below the minimum 80%",),
                thresholds=Thresholds(min_score=80, max_survivors=0),
            ),
        ),
        baseline=BaselineSummary(tests=3, wall_s=0.2, workers=2, selection="coverage"),
        warnings=("a </script> warning",),
        failures=("shop/broken.py: cannot parse <script>",),
        exit_code=4,
    )


def test_result_round_trips_through_json(result: RunResult) -> None:
    text = to_json(result)
    assert from_json(text) == result
    assert json.loads(text)["mutants"][0]["splice"]["replacement"] == b"/".hex()


def test_result_file_errors(tmp_path: Path, result: RunResult) -> None:
    with pytest.raises(ConfigError, match="run 'pytestmut run' first"):
        load_result(tmp_path / "missing.json")
    old = json.loads(to_json(result))
    old["schema"] = 0
    with pytest.raises(ConfigError, match="schema 0"):
        from_json(json.dumps(old))
    with pytest.raises(ConfigError, match="cannot read results"):
        from_json("{not json")


def test_text_report(result: RunResult) -> None:
    text = render(result, "text")
    assert text.isascii() is False  # the source line keeps its own characters
    assert "killed" in text and "survived" in text and "suppressed" in text
    assert "suppressed by reason: inline 1" in text
    assert "default" in text and "FAIL" in text and "50.0%" in text
    assert "1/0" in text  # survivors against the limit
    assert "shop/pricing.py:2  return.none  [bbbbbbbbbbbb]  in total" in text
    assert "- return price * quantity  # café" in text
    assert "+ return None  # café" in text
    assert "shop/pricing.py::helper" in text
    assert "shop/broken.py: cannot parse" in text
    assert text.rstrip().endswith("FAILED (exit code 4)")
    assert "num.shift  inline" not in text
    assert "num.shift  inline" in render(result, "text", show_suppressed=True)
    chrome = "\n".join(line for line in text.splitlines() if "café" not in line)
    assert chrome.isascii()


def test_markdown_report(result: RunResult) -> None:
    text = render(result, "markdown", markdown_max_survivors=0)
    assert text.startswith("## Mutation testing: failed")
    assert "| default | 3 | 50.0% | 50.0% | 1 / 0 | 0 | **FAIL** |" in text
    assert "### Survivors (1)" in text and "...and 1 more." in text
    full = render(result, "markdown")
    assert "```diff" in full and "+ return None  # café" in full
    assert "default: score 50.0% is below the minimum 80%" in full


def test_html_report_is_self_contained(result: RunResult) -> None:
    html = render(result, "html")
    assert html.startswith("<!doctype html>")
    assert "__DATA__" not in html
    assert not re.search(r"https?://", html)
    embedded = re.search(
        r'<script id="data" type="application/json">(.*?)</script>', html, re.S
    )
    assert embedded is not None
    # Text from the project must not be able to close the script element.
    assert "</script>" not in embedded.group(1)
    data = json.loads(embedded.group(1))
    assert data["exit_code"] == 4 and data["score"] == 50.0
    assert data["warnings"] == ["a </script> warning"]
    files = {item["path"]: item for item in data["files"]}
    assert files["shop/pricing.py"]["lines"][1].endswith("# café")
    assert [m["status"] for m in files["shop/pricing.py"]["mutants"]] == [
        "killed",
        "survived",
        "suppressed",
    ]
    assert files["shop/pricing.py"]["mutants"][0]["killing_test"] == (
        "tests/t.py::test_total"
    )
    assert files["shop/broken.py"]["note"] == "cannot parse <script>"
    bare = render(result, "html", embed_source=False)
    assert "price * quantity  # caf" not in bare


def test_reports_degrade_when_the_source_changed(
    result: RunResult, tmp_path: Path
) -> None:
    (tmp_path / "shop" / "pricing.py").write_text("changed = True\n", encoding="utf-8")
    sources = SourceCache(result)
    assert sources.get("shop/pricing.py") is None
    survivor = result.with_status(Status.SURVIVED)[0]
    assert sources.diff(survivor) == (["price * quantity"], ["None"])
    assert "- price * quantity" in render(result, "text")


def test_write_reports(result: RunResult, tmp_path: Path) -> None:
    out = tmp_path / "out" / "nested"
    written = write_reports(result, ("markdown", "html", "json"), out)
    assert {path.name for path in written.values()} == {
        FILE_NAMES["markdown"],
        FILE_NAMES["html"],
        FILE_NAMES["json"],
    }
    assert from_json(written["json"].read_text(encoding="utf-8")) == result
    assert write_reports(result, (), tmp_path / "unused") == {}
    assert not (tmp_path / "unused").exists()
    with pytest.raises(ConfigError):
        render(result, "pdf")
