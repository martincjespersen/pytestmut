"""Guidance attached to mutants the tests did not catch."""

from __future__ import annotations

import pytest

from pytestmut.mutation.model import Mutant, MutantResult, SourceSpan, Splice, Status
from pytestmut.mutation.registry import OPERATORS
from pytestmut.report import guidance as module
from pytestmut.report.guidance import guidance


def _mutant(operator: str, original: str = "<", replacement: str = "<=") -> Mutant:
    return Mutant(
        id="abc123",
        path="shop/pricing.py",
        scope="total",
        operator=operator,
        span=SourceSpan(8, 4, 8, 5),
        splice=Splice(0, 1, b"x"),
        original=original,
        replacement=replacement,
        static=False,
        policy="default",
        cover_lines=(8, 8),
    )


def test_every_operator_has_advice() -> None:
    assert set(module._ADVICE) == set(OPERATORS)


@pytest.mark.parametrize("operator", sorted(OPERATORS))
def test_survivor_guidance_names_change_place_tests_and_a_way_out(
    operator: str,
) -> None:
    result = MutantResult(
        "abc123",
        Status.SURVIVED,
        tests_selected=7,
        tests=tuple(f"tests/t.py::test_{n}" for n in range(7)),
    )
    text = guidance(_mutant(operator), result)
    assert text.startswith("Changing `<` -> `<=` at shop/pricing.py:8 in `total`")
    assert "tests/t.py::test_0" in text and "and 2 more" in text
    assert "tests/t.py::test_6" not in text
    assert f"# pytestmut: skip[{operator}] -- <why>" in text
    assert text.count("# pytestmut: skip") == 1


def test_counts_tests_when_their_ids_are_unknown() -> None:
    cached = MutantResult("abc123", Status.SURVIVED, tests_selected=3, from_cache=True)
    text = guidance(_mutant("cmp.boundary"), cached)
    assert "3 test(s) ran this code and none failed." in text


def test_uncovered_guidance_points_at_the_function() -> None:
    unreached = MutantResult("abc123", Status.NO_COVERAGE, reason="no test reaches it")
    text = guidance(_mutant("arith.add_sub", "+", "-"), unreached)
    assert text.startswith("No test executes this line.")
    assert "calls `total`" in text and "line 8 runs" in text
    unimported = MutantResult(
        "abc123", Status.NO_COVERAGE, reason="file never imported by the tests"
    )
    assert guidance(_mutant("arith.add_sub"), unimported).startswith(
        "The tests never import this file."
    )


@pytest.mark.parametrize(
    "status", [Status.KILLED, Status.TIMEOUT, Status.OOM, Status.ERROR, Status.NOT_RUN]
)
def test_nothing_to_do_for_other_statuses(status: Status) -> None:
    assert guidance(_mutant("cmp.boundary"), MutantResult("abc123", status)) == ""


def test_forced_conditions_name_the_missing_branch() -> None:
    result = MutantResult("abc123", Status.SURVIVED)
    forced_true = guidance(_mutant("cond.const", "ready", "True"), result)
    assert "case where it is false is never checked" in forced_true
    forced_false = guidance(_mutant("cond.const", "ready", "False"), result)
    assert "case where it is true is never checked" in forced_false
