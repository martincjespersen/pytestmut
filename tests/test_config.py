"""Configuration loading: strictness, inheritance and overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pytestmut.config.loader import (
    find_config,
    load_config,
    parse_config,
    parse_size,
    with_overrides,
)
from pytestmut.config.model import Config
from pytestmut.exceptions import ConfigError
from pytestmut.mutation.registry import Level
from pytestmut.policy.matcher import PolicyMatcher, symbol_matches


def _parse(data: dict[str, Any], root: Path = Path(".")) -> Config:
    return parse_config(data, root, None, "test")


def test_defaults() -> None:
    config = _parse({})
    assert config.paths == ()
    assert config.run.workers is None and config.run.selection == "coverage"
    assert config.operators.level is Level.CORE
    assert config.default_policy.thresholds.min_score == 0.0
    assert config.default_policy.thresholds.count_timeouts_as_killed
    assert config.report.formats == ("text",)
    assert "cmp.negate" in config.default_policy.operators
    assert "str.empty" not in config.default_policy.operators


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({"workerz": 1}, "unknown setting(s): workerz"),
        ({"run": {"selektion": "all"}}, "unknown setting(s): selektion"),
        ({"run": {"selection": "some"}}, "not one of coverage, all"),
        ({"run": {"workers": 0}}, "'auto' or an integer >= 1"),
        ({"run": {"workers": "many"}}, "'auto' or an integer >= 1"),
        ({"run": {"verify_survivors": 2}}, "at most 1"),
        ({"paths": "src"}, "must be a list of strings"),
        ({"fail_on_empty": "yes"}, "must be true or false"),
        ({"operators": {"level": "max"}}, "not one of core, extended, all"),
        ({"operators": {"disable": ["nope.*"]}}, "unknown mutation operator 'nope.*'"),
        ({"suppress": {"builtin_disable": ["nonsense"]}}, "unknown reason code"),
        ({"suppress": {"lines": ["("]}}, "bad regex"),
        ({"thresholds": {"min_score": 120}}, "percentage"),
        ({"thresholds": {"max_survivors": -2}}, "integer >= -1"),
        ({"thresholds": {"oops": 1}}, "unknown setting(s): oops"),
        ({"report": {"formats": ["pdf"]}}, "not one of text"),
        ({"safety": {"memory_limit": "lots"}}, "not 'auto', 'off' or a size"),
        ({"safety": {"memory_budget": "plenty"}}, "not 'auto', 'off' or a size"),
        ({"safety": {"hash_seed": -1}}, "non-negative integer or 'inherit'"),
        ({"timeout": {"factor": 0.5}}, ">= 1.0"),
        ({"policy": [{"paths": ["a"]}]}, "name is required"),
        ({"policy": [{"name": "x"}]}, "must select something"),
        ({"policy": [{"name": "default", "paths": ["a"]}]}, "reserved or repeated"),
        (
            {"policy": [{"name": "x", "paths": ["a"]}, {"name": "x", "paths": ["b"]}]},
            "reserved or repeated",
        ),
        (
            {"policy": [{"name": "x", "paths": ["a"], "bogus": 1}]},
            "unknown setting(s): bogus",
        ),
        (
            {"policy": [{"name": "x", "paths": ["a"], "enable": ["zz"]}]},
            "unknown mutation operator",
        ),
    ],
)
def test_invalid_settings_are_errors(data: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ConfigError) as caught:
        _parse(data)
    assert fragment in str(caught.value)


def test_policy_inherits_thresholds_and_layers_operators() -> None:
    config = _parse(
        {
            "operators": {"level": "core", "disable": ["num.shift"]},
            "thresholds": {"min_score": 70, "max_survivors": 5},
            "policy": [
                {
                    "name": "strict",
                    "paths": ["src/core/**"],
                    "level": "extended",
                    "enable": ["loop.break_continue"],
                    "disable": ["str.*"],
                    "tolerate_survivors": ["cmp.boundary"],
                    "max_survivors": 0,
                    "always_full": True,
                },
                {"name": "loose", "symbols": ["legacy_*"], "enable": ["num.shift"]},
            ],
        }
    )
    strict, loose = config.policies
    assert strict.thresholds.min_score == 70
    assert strict.thresholds.max_survivors == 0
    assert strict.always_full
    assert "assign.none" in strict.operators
    assert "loop.break_continue" in strict.operators
    assert "str.empty" not in strict.operators
    # The global disable still applies when a policy changes level.
    assert "num.shift" not in strict.operators
    assert strict.tolerate_survivors == {"cmp.boundary"}
    assert loose.thresholds.max_survivors == 5
    assert "num.shift" in loose.operators and "assign.none" not in loose.operators
    assert "num.shift" not in config.default_policy.operators


def test_policy_resolution_order() -> None:
    config = _parse(
        {
            "policy": [
                {"name": "billing", "paths": ["src/billing/**"]},
                {"name": "money", "symbols": ["Invoice", "compute_*"]},
                {"name": "everything-in-src", "paths": ["src"]},
            ]
        }
    )
    matcher = PolicyMatcher(config)
    assert matcher.resolve("src/billing/a.py", "f", None).name == "billing"
    assert matcher.resolve("lib/a.py", "Invoice.total", None).name == "money"
    assert matcher.resolve("lib/a.py", "compute_tax.inner", None).name == "money"
    assert matcher.resolve("src/other.py", "f", None).name == "everything-in-src"
    assert matcher.resolve("lib/a.py", "f", None).name == "default"
    # An inline tag wins over selectors; an unknown tag falls back to them.
    assert matcher.resolve("src/billing/a.py", "f", "money").name == "money"
    assert matcher.resolve("lib/a.py", "f", "nope").name == "default"
    assert matcher.resolve("src/billing/a.py", "f", "nope").name == "billing"
    assert not symbol_matches("", ("*",))
    assert symbol_matches("A.b.c", ("A",))
    assert not symbol_matches("AB.c", ("A",))


def test_overrides() -> None:
    config = _parse({"paths": ["src"], "thresholds": {"min_score": 50}})
    changed = with_overrides(
        config,
        paths=("lib",),
        workers=3,
        level="extended",
        min_score=90.0,
        formats=("html", "json"),
        selection="all",
        diff_base="main",
        cache=False,
        memory_budget="2GiB",
    )
    assert changed.paths == ("lib",)
    assert changed.safety.memory_budget == "2GiB"
    assert changed.run.workers == 3 and changed.run.selection == "all"
    assert changed.default_policy.thresholds.min_score == 90.0
    assert "str.empty" in changed.default_policy.operators
    assert changed.report.formats == ("html", "json")
    assert changed.diff.base == "main" and not changed.cache.enabled
    assert with_overrides(config) == config
    for bad in (
        {"workers": 0},
        {"min_score": 101.0},
        {"formats": ("pdf",)},
        {"memory_budget": "lots"},
        {"level": "x"},
    ):
        with pytest.raises(ConfigError):
            with_overrides(config, **bad)


def test_config_file_discovery(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    assert find_config(nested) is None
    assert load_config(nested).paths == ()
    pyproject = project / "pyproject.toml"
    pyproject.write_text("[tool.other]\nx = 1\n", encoding="utf-8")
    assert find_config(nested) is None
    pyproject.write_text('[tool.pytestmut]\npaths = ["src"]\n', encoding="utf-8")
    assert find_config(nested) == pyproject
    loaded = load_config(nested)
    assert loaded.paths == ("src",) and loaded.root == project.resolve()
    standalone = project / "pytestmut.toml"
    standalone.write_text('paths = ["lib"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="keep one"):
        find_config(nested)
    pyproject.unlink()
    assert load_config(nested).paths == ("lib",)
    standalone.write_text("paths = [", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(nested)
    with pytest.raises(ConfigError, match="not found"):
        load_config(nested, project / "missing.toml")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("auto", None),
        ("off", 0),
        ("512MiB", 512 * 1024**2),
        ("2G", 2 * 1024**3),
        ("1024", 1024),
    ],
)
def test_parse_size(text: str, expected: int | None) -> None:
    assert parse_size(text, "x") == expected
