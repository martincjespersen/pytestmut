"""Locate, parse and strictly validate the configuration.

Unknown keys, operator ids and enum values are hard errors: a setting that is
silently ignored produces results that look right and are not.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from pytestmut._meta import CONFIG_TABLE, STANDALONE_CONFIG
from pytestmut.config.model import (
    DEFAULT_POLICY,
    REPORT_FORMATS,
    CacheConfig,
    Config,
    DiffConfig,
    OperatorConfig,
    PolicyConfig,
    PytestConfig,
    ReportConfig,
    RunConfig,
    SafetyConfig,
    SuppressConfig,
    Thresholds,
    TimeoutConfig,
)
from pytestmut.exceptions import ConfigError
from pytestmut.mutation.registry import (
    expand_operators,
    operators_at,
    parse_level,
    resolve_operators,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib

BUILTIN_REASONS = (
    "docstring",
    "annotation",
    "type-checking",
    "module-metadata",
    "main-guard",
    "platform-check",
    "logging",
    "exception-message",
    "assert",
    "tunable",
    "hazard",
    "hazard-guard",
    "cleanup-call",
    "decorator",
    "repr",
    "no-cover",
)


class _Table:
    """A TOML table whose keys must all be consumed."""

    def __init__(self, data: object, where: str) -> None:
        if not isinstance(data, Mapping):
            raise ConfigError(f"{where} must be a table")
        self._data = dict(data)
        self._where = where

    def _pop(self, key: str) -> tuple[bool, Any]:
        if key in self._data:
            return True, self._data.pop(key)
        return False, None

    def name(self, key: str) -> str:
        return f"{self._where}.{key}"

    def boolean(self, key: str, default: bool) -> bool:
        found, value = self._pop(key)
        if not found:
            return default
        if not isinstance(value, bool):
            raise ConfigError(f"{self.name(key)} must be true or false")
        return value

    def integer(self, key: str, default: int, minimum: int = 0) -> int:
        found, value = self._pop(key)
        if not found:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ConfigError(f"{self.name(key)} must be an integer >= {minimum}")
        return value

    def number(self, key: str, default: float, minimum: float = 0.0) -> float:
        found, value = self._pop(key)
        if not found:
            return default
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{self.name(key)} must be a number")
        if value < minimum:
            raise ConfigError(f"{self.name(key)} must be >= {minimum}")
        return float(value)

    def string(self, key: str, default: str) -> str:
        found, value = self._pop(key)
        if not found:
            return default
        if not isinstance(value, str):
            raise ConfigError(f"{self.name(key)} must be a string")
        return value

    def choice(self, key: str, default: str, choices: tuple[str, ...]) -> str:
        value = self.string(key, default)
        if value not in choices:
            raise ConfigError(
                f"{self.name(key)}: {value!r} is not one of {', '.join(choices)}"
            )
        return value

    def strings(self, key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
        found, value = self._pop(key)
        if not found:
            return default
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{self.name(key)} must be a list of strings")
        return tuple(value)

    def has(self, key: str) -> bool:
        return key in self._data

    def raw(self, key: str, default: object) -> object:
        found, value = self._pop(key)
        return value if found else default

    def table(self, key: str) -> _Table:
        _, value = self._pop(key)
        return _Table(value if value is not None else {}, self.name(key))

    def tables(self, key: str) -> list[_Table]:
        found, value = self._pop(key)
        if not found:
            return []
        if not isinstance(value, list):
            raise ConfigError(f"{self.name(key)} must be an array of tables")
        return [
            _Table(item, f"{self.name(key)}[{index}]")
            for index, item in enumerate(value)
        ]

    def mapping(self, key: str) -> tuple[tuple[str, str], ...]:
        found, value = self._pop(key)
        if not found:
            return ()
        if not isinstance(value, Mapping) or not all(
            isinstance(v, str) for v in value.values()
        ):
            raise ConfigError(f"{self.name(key)} must map names to strings")
        return tuple(sorted((str(k), str(v)) for k, v in value.items()))

    def finish(self) -> None:
        if self._data:
            unknown = ", ".join(sorted(self._data))
            raise ConfigError(f"{self._where}: unknown setting(s): {unknown}")


def find_config(start: Path) -> Path | None:
    """Find the nearest configuration file at or above a directory.

    Arguments:
        start: Directory to start from.

    Returns:
        The config file, or None when there is none.

    Raises:
        ConfigError: If one directory holds both a standalone file and a
            ``[tool.pytestmut]`` table.
    """
    for directory in (start, *start.parents):
        standalone = directory / STANDALONE_CONFIG
        pyproject = directory / "pyproject.toml"
        in_pyproject = pyproject.is_file() and CONFIG_TABLE in _read(pyproject).get(
            "tool", {}
        )
        if standalone.is_file() and in_pyproject:
            raise ConfigError(
                f"both {standalone} and [tool.{CONFIG_TABLE}] in {pyproject} "
                "exist; keep one"
            )
        if standalone.is_file():
            return standalone
        if in_pyproject:
            return pyproject
    return None


def load_config(start: Path, config_file: Path | None = None) -> Config:
    """Load the configuration for a project.

    Arguments:
        start: Directory the tool was started in.
        config_file: Explicit file to use instead of searching.

    Returns:
        The validated configuration; defaults when no file exists.

    Raises:
        ConfigError: If the file is missing, malformed or holds an unknown or
            invalid setting.
    """
    path = config_file or find_config(start)
    if path is None:
        return Config(root=start.resolve())
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    data = _read(path)
    if path.name == "pyproject.toml":
        data = data.get("tool", {}).get(CONFIG_TABLE, {})
        where = f"[tool.{CONFIG_TABLE}]"
    else:
        where = path.name
    return parse_config(data, path.parent.resolve(), path.resolve(), where)


def parse_config(
    data: Mapping[str, Any], root: Path, config_file: Path | None, where: str
) -> Config:
    """Validate a configuration table.

    Arguments:
        data: The parsed TOML table.
        root: Project root.
        config_file: File the table came from.
        where: Name of the table for error messages.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If any setting is unknown or invalid.
    """
    top = _Table(data, where)
    top.integer("config_version", 1, minimum=1)
    paths = top.strings("paths")
    exclude = top.strings("exclude")
    state_dir = top.string("state_dir", ".pytestmut")
    fail_on_empty = top.boolean("fail_on_empty", True)
    pytest = _pytest(top.table("pytest"))
    run = _run(top.table("run"))
    timeout = _timeout(top.table("timeout"))
    operators = _operators(top.table("operators"))
    suppress = _suppress(top.table("suppress"))
    safety = _safety(top.table("safety"))
    report = _report(top.table("report"))
    cache = _cache(top.table("cache"))
    diff = _diff(top.table("diff"))
    enabled = resolve_operators(
        operators.level, operators.enable, operators.disable, f"{where}.operators"
    )
    thresholds_table = top.table("thresholds")
    thresholds = _thresholds(thresholds_table, Thresholds())
    thresholds_table.finish()
    default_policy = PolicyConfig(
        name=DEFAULT_POLICY, operators=enabled, thresholds=thresholds
    )
    policies = tuple(
        _policy(table, operators, enabled, thresholds) for table in top.tables("policy")
    )
    names = [policy.name for policy in policies]
    for name in names:
        if name == DEFAULT_POLICY or names.count(name) > 1:
            raise ConfigError(f"{where}.policy: name {name!r} is reserved or repeated")
    top.finish()
    return Config(
        root=root,
        config_file=config_file,
        paths=paths,
        exclude=exclude,
        state_dir=state_dir,
        fail_on_empty=fail_on_empty,
        pytest=pytest,
        run=run,
        timeout=timeout,
        operators=operators,
        suppress=suppress,
        safety=safety,
        default_policy=default_policy,
        policies=policies,
        report=report,
        cache=cache,
        diff=diff,
    )


def with_overrides(
    config: Config,
    *,
    paths: tuple[str, ...] = (),
    workers: int | None = None,
    level: str | None = None,
    min_score: float | None = None,
    formats: tuple[str, ...] = (),
    selection: str | None = None,
    diff_base: str | None = None,
    cache: bool | None = None,
    memory_budget: str | None = None,
    pytest_args: tuple[str, ...] | None = None,
) -> Config:
    """Apply command-line overrides on top of the file configuration.

    Arguments:
        config: Configuration from the file.
        paths: Replacement for the paths to mutate.
        workers: Replacement worker count.
        level: Replacement operator tier.
        min_score: Replacement minimum score of the default policy.
        formats: Replacement report formats.
        selection: Replacement test selection mode.
        diff_base: Git ref to diff against.
        cache: Replacement for the cache switch.
        memory_budget: Replacement for the total memory budget.
        pytest_args: Replacement for the pytest arguments, e.g. test paths.

    Returns:
        The updated configuration.

    Raises:
        ConfigError: If an override is invalid.
    """
    if paths:
        config = replace(config, paths=paths)
    if workers is not None:
        if workers < 1:
            raise ConfigError("--workers must be at least 1")
        config = replace(config, run=replace(config.run, workers=workers))
    if selection is not None:
        if selection not in ("coverage", "all"):
            raise ConfigError("--selection must be 'coverage' or 'all'")
        config = replace(config, run=replace(config.run, selection=selection))
    if formats:
        for name in formats:
            if name not in REPORT_FORMATS:
                raise ConfigError(
                    f"--report: {name!r} is not one of {', '.join(REPORT_FORMATS)}"
                )
        config = replace(config, report=replace(config.report, formats=formats))
    if diff_base is not None:
        config = replace(config, diff=replace(config.diff, base=diff_base))
    if cache is not None:
        config = replace(config, cache=replace(config.cache, enabled=cache))
    if pytest_args is not None:
        config = replace(config, pytest=replace(config.pytest, args=pytest_args))
    if memory_budget is not None:
        parse_size(memory_budget, "--max-memory")
        config = replace(
            config, safety=replace(config.safety, memory_budget=memory_budget)
        )
    default = config.default_policy
    if level is not None:
        operators = replace(config.operators, level=parse_level(level, "--level"))
        enabled = resolve_operators(
            operators.level, operators.enable, operators.disable, "--level"
        )
        default = replace(default, operators=enabled)
        config = replace(config, operators=operators)
    if min_score is not None:
        if not 0 <= min_score <= 100:
            raise ConfigError("--min-score must be between 0 and 100")
        default = replace(
            default, thresholds=replace(default.thresholds, min_score=min_score)
        )
    return replace(config, default_policy=default)


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def _pytest(table: _Table) -> PytestConfig:
    config = PytestConfig(
        args=table.strings("args"),
        inherit_addopts=table.boolean("inherit_addopts", True),
        strip_flags=table.strings("strip_flags"),
        extra_overrides=table.strings("extra_overrides"),
    )
    table.finish()
    return config


def _run(table: _Table) -> RunConfig:
    workers: int | None = None
    raw = table.raw("workers", "auto")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
        workers = raw
    elif raw != "auto":
        raise ConfigError(f"{table.name('workers')} must be 'auto' or an integer >= 1")
    config = RunConfig(
        workers=workers,
        selection=table.choice("selection", "coverage", ("coverage", "all")),
        test_order=table.choice("test_order", "stable", ("stable", "fastest")),
        static_mutants=table.choice(
            "static_mutants", "all-tests", ("all-tests", "skip")
        ),
        on_baseline_failure=table.choice(
            "on_baseline_failure", "abort", ("abort", "quarantine")
        ),
        on_parallel_failure=table.choice(
            "on_parallel_failure", "serial", ("serial", "abort")
        ),
        quarantine=table.strings("quarantine"),
        serial_tests=table.strings("serial_tests"),
        verify_survivors=table.number("verify_survivors", 0.05),
        verify_survivors_max=table.integer("verify_survivors_max", 10),
        self_check=table.boolean("self_check", True),
        max_errors=table.integer("max_errors", 0),
    )
    table.finish()
    if config.verify_survivors > 1:
        raise ConfigError(f"{table.name('verify_survivors')} must be at most 1")
    return config


def _timeout(table: _Table) -> TimeoutConfig:
    config = TimeoutConfig(
        factor=table.number("factor", 3.0, minimum=1.0),
        grace_seconds=table.number("grace_seconds", 5.0),
        floor_seconds=table.number("floor_seconds", 15.0),
        max_seconds=table.number("max_seconds", 900.0, minimum=1.0),
        retry_factor=table.number("retry_factor", 2.0, minimum=1.0),
    )
    table.finish()
    return config


def _operators(table: _Table) -> OperatorConfig:
    config = OperatorConfig(
        level=parse_level(table.string("level", "core"), table.name("level")),
        enable=table.strings("enable"),
        disable=table.strings("disable"),
    )
    table.finish()
    return config


def _suppress(table: _Table) -> SuppressConfig:
    config = SuppressConfig(
        builtin_disable=table.strings("builtin_disable"),
        lines=table.strings("lines"),
        symbols=table.strings("symbols"),
        hazardous_calls=table.strings("hazardous_calls"),
        tunable_keywords=table.strings("tunable_keywords"),
        honor_no_cover=table.boolean("honor_no_cover", True),
        foreign_pragmas=table.strings("foreign_pragmas", ("pragma: no mutate",)),
        require_reason=table.boolean("require_reason", False),
        report_unused=table.choice(
            "report_unused", "warn", ("ignore", "warn", "error")
        ),
    )
    table.finish()
    for code in config.builtin_disable:
        if code not in BUILTIN_REASONS:
            raise ConfigError(
                f"{table.name('builtin_disable')}: unknown reason code {code!r}; "
                f"known codes: {', '.join(BUILTIN_REASONS)}"
            )
    for pattern in config.lines:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(
                f"{table.name('lines')}: bad regex {pattern!r}: {exc}"
            ) from exc
    return config


def _safety(table: _Table) -> SafetyConfig:
    seed = table.raw("hash_seed", 0)
    valid_int = isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0
    if not valid_int and seed != "inherit":
        raise ConfigError(
            f"{table.name('hash_seed')} must be a non-negative integer or 'inherit'"
        )
    hash_seed = str(seed)
    config = SafetyConfig(
        env=table.choice("env", "scrub", ("scrub", "allowlist", "inherit")),
        env_drop=table.strings("env_drop"),
        env_keep=table.strings("env_keep"),
        env_set=table.mapping("env_set"),
        redirect_credential_stores=table.boolean("redirect_credential_stores", True),
        memory_budget=table.string("memory_budget", "auto"),
        memory_limit=table.string("memory_limit", "auto"),
        thread_caps=table.boolean("thread_caps", True),
        hash_seed=hash_seed,
        check_worktree=table.boolean("check_worktree", True),
        max_output_bytes=table.integer("max_output_bytes", 131072, minimum=1024),
        tmp_root=table.string("tmp_root", ""),
    )
    table.finish()
    parse_size(config.memory_limit, table.name("memory_limit"))
    parse_size(config.memory_budget, table.name("memory_budget"))
    return config


def _thresholds(table: _Table, base: Thresholds) -> Thresholds:
    thresholds = Thresholds(
        min_score=table.number("min_score", base.min_score),
        min_covered_score=table.number("min_covered_score", base.min_covered_score),
        max_survivors=table.integer("max_survivors", base.max_survivors, minimum=-1),
        max_no_coverage=table.integer(
            "max_no_coverage", base.max_no_coverage, minimum=-1
        ),
        max_timeouts=table.integer("max_timeouts", base.max_timeouts, minimum=-1),
        count_timeouts_as_killed=table.boolean(
            "count_timeouts_as_killed", base.count_timeouts_as_killed
        ),
        allow_inline_suppressions=table.boolean(
            "allow_inline_suppressions", base.allow_inline_suppressions
        ),
        require_reason=table.boolean("require_reason", base.require_reason),
        max_suppressions=table.integer(
            "max_suppressions", base.max_suppressions, minimum=-1
        ),
    )
    for name in ("min_score", "min_covered_score"):
        if getattr(thresholds, name) > 100:
            raise ConfigError(f"{table.name(name)} is a percentage, at most 100")
    return thresholds


def _policy(
    table: _Table,
    operators: OperatorConfig,
    enabled: frozenset[str],
    thresholds: Thresholds,
) -> PolicyConfig:
    name = table.string("name", "")
    if not name:
        raise ConfigError(f"{table.name('name')} is required")
    paths = table.strings("paths")
    symbols = table.strings("symbols")
    if not paths and not symbols:
        raise ConfigError(f"{table.name('paths')} or 'symbols' must select something")
    selected = set(enabled)
    if table.has("level"):
        level = parse_level(table.string("level", "core"), table.name("level"))
        selected = set(operators_at(level))
        selected |= expand_operators(operators.enable, table.name("level"))
        selected -= expand_operators(operators.disable, table.name("level"))
    selected |= expand_operators(table.strings("enable"), table.name("enable"))
    selected -= expand_operators(table.strings("disable"), table.name("disable"))
    tolerated = expand_operators(
        table.strings("tolerate_survivors"), table.name("tolerate_survivors")
    )
    static: str | None = None
    if table.has("static_mutants"):
        static = table.choice("static_mutants", "all-tests", ("all-tests", "skip"))
    policy = PolicyConfig(
        name=name,
        paths=paths,
        symbols=symbols,
        operators=frozenset(selected),
        tolerate_survivors=tolerated,
        thresholds=_thresholds(table, thresholds),
        static_mutants=static,
        always_full=table.boolean("always_full", False),
        allow_empty=table.boolean("allow_empty", False),
    )
    table.finish()
    return policy


def _report(table: _Table) -> ReportConfig:
    config = ReportConfig(
        formats=table.strings("formats", ("text",)),
        output_dir=table.string("output_dir", ".pytestmut/reports"),
        markdown_max_survivors=table.integer("markdown_max_survivors", 50),
        html_embed_source=table.boolean("html_embed_source", True),
    )
    table.finish()
    for name in config.formats:
        if name not in REPORT_FORMATS:
            raise ConfigError(
                f"{table.name('formats')}: {name!r} is not one of "
                f"{', '.join(REPORT_FORMATS)}"
            )
    return config


def _cache(table: _Table) -> CacheConfig:
    config = CacheConfig(
        enabled=table.boolean("enabled", True),
        extra_inputs=table.strings("extra_inputs"),
    )
    table.finish()
    return config


def _diff(table: _Table) -> DiffConfig:
    config = DiffConfig(
        base=table.string("base", ""),
        max_mutants=table.integer("max_mutants", 40, minimum=1),
        per_line=table.integer("per_line", 1, minimum=1),
        budget_seconds=table.number("budget_seconds", 180.0, minimum=1.0),
        on_budget_exceeded=table.choice("on_budget_exceeded", "pass", ("pass", "fail")),
    )
    table.finish()
    return config


_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]i?B?)?\s*$", re.IGNORECASE)
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(value: str, where: str) -> int | None:
    """Parse a memory size setting.

    Arguments:
        value: ``auto``, ``off`` or a size such as ``512MiB`` or ``2G``.
        where: Name of the setting for error messages.

    Returns:
        Bytes, 0 for ``off`` and None for ``auto``.

    Raises:
        ConfigError: If the value is not a size.
    """
    if value == "auto":
        return None
    if value == "off":
        return 0
    match = _SIZE.match(value)
    if match is None:
        raise ConfigError(f"{where}: {value!r} is not 'auto', 'off' or a size")
    unit = (match.group(2) or "").upper().rstrip("B").rstrip("I")
    return int(float(match.group(1)) * _UNITS[unit])
