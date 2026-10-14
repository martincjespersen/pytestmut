"""Check each policy's thresholds against the results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pytestmut.config.model import UNLIMITED, PolicyConfig, Thresholds
from pytestmut.mutation.model import Mutant, MutantResult, Status

USER_SUPPRESSIONS = frozenset({"inline", "config-line", "config-symbol"})


@dataclass(frozen=True)
class PolicyResult:
    """How one policy fared.

    Attributes:
        name: Policy name.
        total: Mutants generated in the policy's region, of any status.
        counts: Number of mutants per status value.
        suppressed: Number of suppressed candidates per reason code.
        detected: Mutants counted as caught.
        survived: Survivors that count against the policy.
        tolerated: Survivors of operators the policy tolerates.
        no_coverage: Mutants no test reaches.
        timeouts: Mutants that timed out or exhausted memory.
        score: Detected over detected, survived and uncovered, in percent.
        covered_score: Detected over detected and survived, in percent.
        user_suppressions: Suppressions written by the user.
        failures: Threshold violations, empty when the policy passed.
        thresholds: The gates that were applied.
    """

    name: str
    total: int = 0
    counts: Mapping[str, int] = field(default_factory=dict)
    suppressed: Mapping[str, int] = field(default_factory=dict)
    detected: int = 0
    survived: int = 0
    tolerated: int = 0
    no_coverage: int = 0
    timeouts: int = 0
    score: float | None = None
    covered_score: float | None = None
    user_suppressions: int = 0
    failures: tuple[str, ...] = ()
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def passed(self) -> bool:
        """True when no threshold was violated."""
        return not self.failures


def final_status(mutant: Mutant, results: Mapping[str, MutantResult]) -> Status:
    """Status of a mutant after the run.

    Arguments:
        mutant: The mutant.
        results: Verdicts by mutant id.

    Returns:
        Its pre-run status, its verdict, or ``not_run``.
    """
    if mutant.pre_status is not None:
        return mutant.pre_status
    result = results.get(mutant.id)
    return result.status if result is not None else Status.NOT_RUN


def _percent(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else 100.0 * numerator / denominator


def _over(limit: int, value: int) -> bool:
    return limit != UNLIMITED and value > limit


def evaluate_policy(
    policy: PolicyConfig,
    mutants: Iterable[Mutant],
    results: Mapping[str, MutantResult],
    refused: Iterable[str] = (),
) -> PolicyResult:
    """Evaluate one policy.

    Arguments:
        policy: The policy and its thresholds.
        mutants: Every mutant of the run; those of other policies are
            ignored.
        results: Verdicts by mutant id.
        refused: Messages for inline suppressions the policy did not allow.

    Returns:
        Counts, scores and any threshold violations.
    """
    thresholds = policy.thresholds
    counts: Counter[str] = Counter()
    suppressed: Counter[str] = Counter()
    survived = tolerated = total = 0
    for mutant in mutants:
        if mutant.policy != policy.name:
            continue
        total += 1
        status = final_status(mutant, results)
        counts[status.value] += 1
        if status is Status.SUPPRESSED:
            suppressed[mutant.reason] += 1
        elif status is Status.SURVIVED:
            if mutant.operator in policy.tolerate_survivors:
                tolerated += 1
            else:
                survived += 1
    killed = counts[Status.KILLED.value]
    resource = counts[Status.TIMEOUT.value] + counts[Status.OOM.value]
    no_coverage = counts[Status.NO_COVERAGE.value]
    if thresholds.count_timeouts_as_killed:
        detected, missed = killed + resource, survived
    else:
        detected, missed = killed, survived + resource
    score = _percent(detected, detected + missed + no_coverage)
    covered = _percent(detected, detected + missed)
    user = sum(suppressed[reason] for reason in USER_SUPPRESSIONS)
    failures: list[str] = list(refused)
    if score is not None and score < thresholds.min_score:
        failures.append(
            f"score {score:.1f}% is below the minimum {thresholds.min_score:g}%"
        )
    if covered is not None and covered < thresholds.min_covered_score:
        failures.append(
            f"covered score {covered:.1f}% is below the minimum "
            f"{thresholds.min_covered_score:g}%"
        )
    if _over(thresholds.max_survivors, survived):
        failures.append(
            f"{survived} survivor(s), at most {thresholds.max_survivors} allowed"
        )
    if _over(thresholds.max_no_coverage, no_coverage):
        failures.append(
            f"{no_coverage} mutant(s) without coverage, at most "
            f"{thresholds.max_no_coverage} allowed"
        )
    if _over(thresholds.max_timeouts, resource):
        failures.append(
            f"{resource} timeout(s), at most {thresholds.max_timeouts} allowed"
        )
    if _over(thresholds.max_suppressions, user):
        failures.append(
            f"{user} suppression(s) written in code or config, at most "
            f"{thresholds.max_suppressions} allowed"
        )
    return PolicyResult(
        name=policy.name,
        total=total,
        counts=dict(counts),
        suppressed=dict(suppressed),
        detected=detected,
        survived=survived,
        tolerated=tolerated,
        no_coverage=no_coverage,
        timeouts=resource,
        score=score,
        covered_score=covered,
        user_suppressions=user,
        failures=tuple(failures),
        thresholds=thresholds,
    )


def overall_scores(
    policies: Iterable[PolicyResult],
) -> tuple[float | None, float | None]:
    """Scores over all policies together.

    Arguments:
        policies: Policy results of one run.

    Returns:
        ``(score, covered score)`` in percent, each None when there is
        nothing to score.
    """
    detected = missed = uncovered = 0
    for policy in policies:
        detected += policy.detected
        missed += policy.survived
        if not policy.thresholds.count_timeouts_as_killed:
            missed += policy.timeouts
        uncovered += policy.no_coverage
    return (
        _percent(detected, detected + missed + uncovered),
        _percent(detected, detected + missed),
    )
