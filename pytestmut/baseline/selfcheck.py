"""Prove the whole pipeline with a mutant that must die.

Before any real mutant runs, one target file gets a synthetic mutation that
raises at import time. If that is not reported as killed, then mutations are
not reaching the tests, or failures are not reaching the verdict, and every
number the run would produce is meaningless.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

from pytestmut.baseline.runner import Baseline
from pytestmut.exceptions import SourceError, ToolFailure
from pytestmut.exec.jobs import MUTATE, Job, TargetIdentity
from pytestmut.exec.runner import JobRunner
from pytestmut.mutation.model import Mutant, SourceSpan, Splice, Status
from pytestmut.source.scopes import ModuleContext
from pytestmut.source.text import SourceText
from pytestmut.verdict.classify import classify

_RAISE = "raise BaseException('pytestmut-selfcheck')"
_MAX_CANDIDATES = 5


def synthetic_mutant(target: TargetIdentity) -> Mutant | None:
    """Build the must-die mutant for a file.

    Arguments:
        target: A target file the tests import.

    Returns:
        A mutant replacing the first module-level statement with a raise, or
        None when the file has no suitable statement.
    """
    try:
        source = SourceText.from_file(Path(target.path))
        ctx = ModuleContext.parse(source)
    except SourceError:
        return None
    for statement in ctx.tree.body:
        if isinstance(statement, ast.Expr) and isinstance(
            statement.value, ast.Constant
        ):
            continue
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__":
            continue
        if not ctx.alone_on_lines(statement) or statement.col_offset != 0:
            continue
        first = ctx.statement_first_line(statement)
        start = source.line_starts[first - 1]
        end = ctx.end(statement)
        lines = (statement.end_lineno or statement.lineno) - first
        replacement = _RAISE + "\n" * lines
        return Mutant(
            id="selfcheck",
            path=target.relpath,
            scope="",
            operator="selfcheck",
            span=SourceSpan(first, 0, statement.end_lineno or first, 0),
            splice=Splice(
                start=source.byte_offset(start),
                end=source.byte_offset(end),
                replacement=source.encode(replacement),
            ),
            original=source.text[start:end],
            replacement=replacement,
            static=True,
            policy="",
            cover_lines=(first, first),
        )
    return None


def run_self_check(
    runner: JobRunner,
    baseline: Baseline,
    targets: tuple[TargetIdentity, ...],
    budget: float,
    cancel: threading.Event,
) -> None:
    """Run the must-die mutant.

    Arguments:
        runner: Runs the test process.
        baseline: The validated baseline.
        targets: Every target file of the run.
        budget: Time budget for one full run of the suite.
        cancel: Set from another thread to abort.

    Raises:
        ToolFailure: If no candidate file reports its synthetic mutant as
            killed.
    """
    candidates = [target for target in targets if target.relpath in baseline.loaded]
    evidence: list[str] = []
    tried = 0
    for target in candidates:
        mutant = synthetic_mutant(target)
        if mutant is None:
            continue
        tried += 1
        job = Job(
            id=f"selfcheck{tried}",
            mode=MUTATE,
            mutant=mutant,
            nodeids=None,
            stop_on_fail=True,
            timeout_s=budget,
        )
        outcome = runner.run(job, cancel)
        verdict = classify(outcome.process, outcome.record, baseline.passing)
        if verdict.status is Status.KILLED:
            return
        evidence.append(
            f"{target.relpath}: expected killed, got {verdict.status.value}"
            f" ({verdict.reason}) {verdict.detail[:200]}"
        )
        if tried >= _MAX_CANDIDATES:
            break
    if tried == 0:
        return
    raise ToolFailure(
        "self-check failed: a mutation that raises at import time was not "
        "detected, so results from this run could not be trusted.\n"
        + "\n".join(evidence)
    )
