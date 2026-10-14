"""Narrow a run to the lines that changed.

Whole-project mutation testing is too slow for a commit hook. Diff mode keeps
only mutants on changed lines, takes one per line before any others, caps the
total and sets a wall-clock budget. Policies marked ``always_full`` are the
exception: when one of their files changes they run completely, and neither
the cap nor the budget touches them.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace

from pytestmut.config.model import Config
from pytestmut.mutation.model import Mutant, Status
from pytestmut.vcs.git import Git


@dataclass(frozen=True)
class DiffScope:
    """The mutants of a run after scoping.

    Attributes:
        mutants: Mutants in scope; ones left out by sampling carry the
            ``not_run`` status.
        is_diff: True when the run is limited to changed lines.
        description: ``full`` or what the diff was taken against.
        partial: True when sampling left mutants out.
        protected: Ids of mutants the time budget does not apply to.
        warnings: Things the user should know.
        budget_seconds: Wall-clock budget, or None for no limit.
    """

    mutants: list[Mutant]
    is_diff: bool = False
    description: str = "full"
    partial: bool = False
    protected: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()
    budget_seconds: float | None = None

    def deadline(self, started: float) -> float | None:
        """Monotonic time after which unstarted mutants are left unrun."""
        if self.budget_seconds is None:
            return None
        return started + self.budget_seconds


def _rank(mutant: Mutant) -> str:
    return hashlib.sha256(mutant.id.encode()).hexdigest()


def scope_to_diff(
    config: Config, git: Git, mutants: list[Mutant], staged: bool, hook: bool
) -> DiffScope:
    """Apply diff scoping when it was asked for.

    Arguments:
        config: The run configuration.
        git: The project's repository.
        mutants: Every generated mutant.
        staged: Limit to lines staged in the index.
        hook: Running as a pre-commit hook.

    Returns:
        The scoped mutants. Without ``staged``, ``hook`` or a configured
        base this is the full set, untouched.

    Raises:
        GitError: If the diff cannot be computed.
    """
    base = config.diff.base
    if hook:
        pushed_from = os.environ.get("PRE_COMMIT_FROM_REF", "")
        if pushed_from:
            base, staged = pushed_from, False
        elif not base:
            staged = True
    if not staged and not base:
        return DiffScope(mutants=mutants)
    changed = git.changed_lines(base, staged)
    description = "staged changes" if staged else f"lines changed against {base}"
    touched_policies = {mutant.policy for mutant in mutants if mutant.path in changed}
    full_policies = {
        policy.name
        for policy in config.policies
        if policy.always_full and policy.name in touched_policies
    }
    protected: list[Mutant] = []
    by_line: dict[tuple[str, int], list[Mutant]] = {}
    for mutant in mutants:
        if mutant.policy in full_policies:
            protected.append(mutant)
            continue
        lines = changed.get(mutant.path, frozenset())
        hit = [
            line
            for line in range(mutant.span.line, mutant.span.end_line + 1)
            if line in lines
        ]
        if hit:
            by_line.setdefault((mutant.path, hit[0]), []).append(mutant)
    first_picks: list[Mutant] = []
    later: list[Mutant] = []
    passive: list[Mutant] = []
    for group in by_line.values():
        runnable = [mutant for mutant in group if mutant.runnable]
        passive += [mutant for mutant in group if not mutant.runnable]
        first_picks += runnable[: config.diff.per_line]
        later += runnable[config.diff.per_line :]
    ordered = sorted(first_picks, key=_rank) + sorted(later, key=_rank)
    kept = ordered[: config.diff.max_mutants]
    dropped = [
        replace(mutant, pre_status=Status.NOT_RUN, reason="sampled out")
        for mutant in ordered[config.diff.max_mutants :]
    ]
    scoped = sorted(
        [*protected, *kept, *dropped, *passive],
        key=lambda mutant: (mutant.path, mutant.span.line, mutant.span.col, mutant.id),
    )
    warnings: list[str] = []
    if not scoped:
        warnings.append(f"no mutable code in {description}; nothing to test")
    if dropped:
        warnings.append(
            f"{len(dropped)} mutant(s) on changed lines were sampled out "
            f"(diff.max_mutants = {config.diff.max_mutants})"
        )
    return DiffScope(
        mutants=scoped,
        is_diff=True,
        description=description,
        partial=bool(dropped),
        protected=frozenset(mutant.id for mutant in protected),
        warnings=tuple(warnings),
        budget_seconds=config.diff.budget_seconds,
    )
