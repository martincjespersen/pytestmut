"""Frozen dataclasses for every configuration table."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pytestmut.mutation.registry import Level

REPORT_FORMATS = ("text", "markdown", "html", "json")
UNLIMITED = -1


@dataclass(frozen=True)
class PytestConfig:
    """How pytest is invoked.

    Attributes:
        args: Arguments appended to every pytest run, e.g. test paths.
        inherit_addopts: Keep the project's ``addopts`` and ``PYTEST_ADDOPTS``.
        strip_flags: Extra no-value flags removed from inherited options.
        extra_overrides: Arguments appended after everything else.
    """

    args: tuple[str, ...] = ()
    inherit_addopts: bool = True
    strip_flags: tuple[str, ...] = ()
    extra_overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunConfig:
    """How mutants are scheduled and checked.

    Attributes:
        workers: Number of parallel workers; None sizes it from the machine.
        selection: ``coverage`` runs only tests that reach the mutant,
            ``all`` runs the whole suite for every mutant.
        test_order: ``stable`` keeps baseline order, ``fastest`` sorts by
            duration.
        static_mutants: ``all-tests`` runs import-time mutants against the
            whole suite, ``skip`` leaves them out.
        on_baseline_failure: ``abort`` or ``quarantine`` failing tests.
        on_parallel_failure: ``serial`` or ``abort`` when the suite only
            passes one worker at a time.
        quarantine: Test id globs never credited with kills.
        serial_tests: Test id globs whose mutants run with nothing else.
        verify_survivors: Fraction of survivors re-run against all tests.
        verify_survivors_max: Upper bound on that sample.
        self_check: Prove the pipeline with a synthetic mutant first.
        max_errors: Mutants allowed to end in ``error`` before failing.
    """

    workers: int | None = None
    selection: str = "coverage"
    test_order: str = "stable"
    static_mutants: str = "all-tests"
    on_baseline_failure: str = "abort"
    on_parallel_failure: str = "serial"
    quarantine: tuple[str, ...] = ()
    serial_tests: tuple[str, ...] = ()
    verify_survivors: float = 0.05
    verify_survivors_max: int = 10
    self_check: bool = True
    max_errors: int = 0


@dataclass(frozen=True)
class TimeoutConfig:
    """Per-mutant time budget: ``factor * estimate + grace``, clamped.

    Attributes:
        factor: Multiplier on the baseline duration of the selected tests.
        grace_seconds: Constant added on top.
        floor_seconds: Smallest budget ever used.
        max_seconds: Largest budget ever used.
        retry_factor: Multiplier applied when a timed-out mutant is retried
            alone.
    """

    factor: float = 3.0
    grace_seconds: float = 5.0
    floor_seconds: float = 15.0
    max_seconds: float = 900.0
    retry_factor: float = 2.0


@dataclass(frozen=True)
class OperatorConfig:
    """Which mutation operators are applied.

    Attributes:
        level: Base tier.
        enable: Ids or globs added to the tier.
        disable: Ids or globs removed afterwards.
    """

    level: Level = Level.CORE
    enable: tuple[str, ...] = ()
    disable: tuple[str, ...] = ()


@dataclass(frozen=True)
class SuppressConfig:
    """What is left unmutated.

    Attributes:
        builtin_disable: Built-in reason codes to switch off.
        lines: Regexes; a matching source line is not mutated.
        symbols: Qualified-name globs, optionally ``path::Qual.name``.
        hazardous_calls: Dotted-name globs added to the built-in list.
        tunable_keywords: Keyword-name globs added to the built-in list.
        honor_no_cover: Leave ``# pragma: no cover`` code alone.
        foreign_pragmas: Comment substrings treated as a bare ``skip``.
        require_reason: Ignore inline skips that give no reason.
        report_unused: ``ignore``, ``warn`` or ``error`` for skips that
            suppressed nothing.
    """

    builtin_disable: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    hazardous_calls: tuple[str, ...] = ()
    tunable_keywords: tuple[str, ...] = ()
    honor_no_cover: bool = True
    foreign_pragmas: tuple[str, ...] = ("pragma: no mutate",)
    require_reason: bool = False
    report_unused: str = "warn"


@dataclass(frozen=True)
class SafetyConfig:
    """Containment of the test processes.

    Attributes:
        env: ``scrub`` drops credential-looking variables, ``allowlist``
            keeps only ``env_keep``, ``inherit`` passes everything.
        env_drop: Name globs added to the built-in credential patterns.
        env_keep: Exceptions to the drop list, or the whole allowlist.
        env_set: Variables set in every test process.
        redirect_credential_stores: Point well-known credential files at a
            path that does not exist.
        memory_budget: Memory all test processes together may use:
            ``auto`` (half of physical memory), ``off`` or a size such as
            ``8GiB``. The worker count is reduced to fit.
        memory_limit: Memory one test process tree may use: ``auto``
            (measured from the baseline, with headroom), ``off`` or a size.
        thread_caps: Limit BLAS/OpenMP thread pools to one thread.
        hash_seed: ``PYTHONHASHSEED`` unless already set; ``inherit`` to
            leave it alone.
        check_worktree: Fail when tracked files change during the run.
        max_output_bytes: Output kept per test process.
        tmp_root: Parent of the run's scratch space, where tests get their
            temporary directories; empty means the system temporary
            directory, outside the project.
    """

    env: str = "scrub"
    env_drop: tuple[str, ...] = ()
    env_keep: tuple[str, ...] = ()
    env_set: tuple[tuple[str, str], ...] = ()
    redirect_credential_stores: bool = True
    memory_budget: str = "auto"
    memory_limit: str = "auto"
    thread_caps: bool = True
    hash_seed: str = "0"
    check_worktree: bool = True
    max_output_bytes: int = 131072
    tmp_root: str = ""


@dataclass(frozen=True)
class Thresholds:
    """Gates applied to one policy.

    Attributes:
        min_score: Minimum mutation score in percent.
        min_covered_score: Minimum score over covered mutants in percent.
        max_survivors: Survivors allowed; -1 is unlimited.
        max_no_coverage: Uncovered mutants allowed; -1 is unlimited.
        max_timeouts: Timeouts allowed; -1 is unlimited.
        count_timeouts_as_killed: Count confirmed timeouts and memory kills
            as detected.
        allow_inline_suppressions: Honour inline skip comments.
        require_reason: Honour only inline skips that give a reason.
        max_suppressions: User-authored suppressions allowed; -1 is
            unlimited.
    """

    min_score: float = 0.0
    min_covered_score: float = 0.0
    max_survivors: int = UNLIMITED
    max_no_coverage: int = UNLIMITED
    max_timeouts: int = UNLIMITED
    count_timeouts_as_killed: bool = True
    allow_inline_suppressions: bool = True
    require_reason: bool = False
    max_suppressions: int = UNLIMITED


DEFAULT_POLICY = "default"


@dataclass(frozen=True)
class PolicyConfig:
    """Rules for one region of code.

    Attributes:
        name: Policy name shown in reports and used in ``policy=`` tags.
        paths: Path globs selecting files; empty selects every file.
        symbols: Qualified-name globs selecting definitions inside those
            files; empty selects everything in them.
        operators: Operator ids applied in the region.
        tolerate_survivors: Operator ids whose survivors do not count
            towards ``max_survivors``.
        thresholds: Gates, with unset values inherited from the default.
        static_mutants: Override of the run-level setting, or None.
        always_full: Never sample or budget-skip this policy in diff mode.
        allow_empty: Accept the policy matching no code.
    """

    name: str
    paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    operators: frozenset[str] = frozenset()
    tolerate_survivors: frozenset[str] = frozenset()
    thresholds: Thresholds = field(default_factory=Thresholds)
    static_mutants: str | None = None
    always_full: bool = False
    allow_empty: bool = False


@dataclass(frozen=True)
class ReportConfig:
    """Which reports are written.

    Attributes:
        formats: Any of ``text``, ``markdown``, ``html``, ``json``.
        output_dir: Directory for report files, relative to the project.
        markdown_max_survivors: Survivors listed in the markdown report.
        html_embed_source: Embed source code in the HTML report.
    """

    formats: tuple[str, ...] = ("text",)
    output_dir: str = ".pytestmut/reports"
    markdown_max_survivors: int = 50
    html_embed_source: bool = True


@dataclass(frozen=True)
class CacheConfig:
    """Reuse of verdicts between runs.

    Attributes:
        enabled: Reuse verdicts whose inputs are unchanged.
        extra_inputs: Globs of extra files whose content invalidates the
            cache, e.g. data files the tests read.
    """

    enabled: bool = True
    extra_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiffConfig:
    """Limits for runs scoped to changed lines.

    Attributes:
        base: Git ref to diff against; empty means a full run.
        max_mutants: Cap on mutants per run.
        per_line: Mutants taken per changed line before sampling.
        budget_seconds: Wall-clock budget.
        on_budget_exceeded: ``pass`` or ``fail`` when the budget runs out.
    """

    base: str = ""
    max_mutants: int = 40
    per_line: int = 1
    budget_seconds: float = 180.0
    on_budget_exceeded: str = "pass"


@dataclass(frozen=True)
class Config:
    """The whole configuration of a run.

    Attributes:
        root: Project root; every relative path is resolved against it.
        config_file: File the configuration came from, if any.
        paths: Files, directories or globs to mutate.
        exclude: Path globs never mutated.
        state_dir: Directory holding all of the tool's own files.
        fail_on_empty: Fail when the scope has no runnable mutant.
        pytest: pytest invocation settings.
        run: Scheduling settings.
        timeout: Time budget settings.
        operators: Globally enabled operators.
        suppress: Suppression settings.
        safety: Containment settings.
        default_policy: Policy for code no other policy matches.
        policies: Policies for critical code, in match order.
        report: Report settings.
        cache: Cache settings.
        diff: Diff-mode settings.
    """

    root: Path
    config_file: Path | None = None
    paths: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    state_dir: str = ".pytestmut"
    fail_on_empty: bool = True
    pytest: PytestConfig = field(default_factory=PytestConfig)
    run: RunConfig = field(default_factory=RunConfig)
    timeout: TimeoutConfig = field(default_factory=TimeoutConfig)
    operators: OperatorConfig = field(default_factory=OperatorConfig)
    suppress: SuppressConfig = field(default_factory=SuppressConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    default_policy: PolicyConfig = field(
        default_factory=lambda: PolicyConfig(name=DEFAULT_POLICY)
    )
    policies: tuple[PolicyConfig, ...] = ()
    report: ReportConfig = field(default_factory=ReportConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    diff: DiffConfig = field(default_factory=DiffConfig)

    @property
    def all_policies(self) -> tuple[PolicyConfig, ...]:
        """Named policies followed by the default one."""
        return (*self.policies, self.default_policy)

    def policy(self, name: str) -> PolicyConfig:
        """Look a policy up by name.

        Arguments:
            name: Policy name.

        Returns:
            The policy, or the default policy for an unknown name.
        """
        for policy in self.policies:
            if policy.name == name:
                return policy
        return self.default_policy
