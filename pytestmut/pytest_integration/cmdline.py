"""Build the pytest command line for baseline and mutant runs.

The project's own options are inherited, because the tests may need them, but
they are rewritten and never trusted. Options that reorder, deselect,
parallelise, measure coverage or write files into the tree would each make a
mutant's verdict depend on something other than the mutant.

Baseline and mutant runs use the same command line. Which tests run is
decided inside pytest from ``job.json``, so rootdir and ini-file resolution
can never differ between the two.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pytestmut._meta import RECORDER_PLUGIN
from pytestmut.config.model import PytestConfig
from pytestmut.pytest_integration.probe import PytestEnvironment

# Flags without a value that change which tests run or what pytest does.
STRIPPED_FLAGS = frozenset(
    {
        "--lf",
        "--last-failed",
        "--ff",
        "--failed-first",
        "--nf",
        "--new-first",
        "--sw",
        "--stepwise",
        "--sw-skip",
        "--stepwise-skip",
        "--pdb",
        "--trace",
        "-f",
        "--looponfail",
        "--collect-only",
        "--co",
        "--setup-only",
        "--setup-plan",
        "--setup-show",
        "--fixtures",
        "--fixtures-per-test",
        "--snapshot-update",
        "--force-regen",
        "--regen-all",
        "--json-report",
        "--mutate",
        "--mutate-reached",
    }
)
# Flags that take a value and write a report file into the tree.
STRIPPED_VALUE_FLAGS = frozenset(
    {
        "--junitxml",
        "--junit-xml",
        "--html",
        "--report-log",
        "--json-report-file",
        "--result-log",
        "--mutate-level",
        "--mutate-min-score",
        "--mutate-diff",
    }
)


def filter_inherited(tokens: tuple[str, ...], extra: tuple[str, ...]) -> list[str]:
    """Remove unsafe flags from inherited option tokens.

    Arguments:
        tokens: Tokens of ``addopts`` or ``PYTEST_ADDOPTS``.
        extra: Additional no-value flags configured by the user.

    Returns:
        The tokens that are kept, in order.
    """
    stripped = STRIPPED_FLAGS | frozenset(extra)
    kept: list[str] = []
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            continue
        name = token.partition("=")[0]
        if token in stripped:
            continue
        if name in STRIPPED_VALUE_FLAGS:
            skip_value = "=" not in token
            continue
        kept.append(token)
    return kept


def build_argv(
    probe: PytestEnvironment, config: PytestConfig, job_dir: Path
) -> tuple[str, ...]:
    """Build the command line of one test process.

    Arguments:
        probe: The project's pytest setup.
        config: pytest settings from the configuration.
        job_dir: Directory of the job; pytest's cache and temp files go
            there instead of into the project.

    Returns:
        The full command line, interpreter included.
    """
    inherited: list[str] = []
    if config.inherit_addopts:
        inherited += filter_inherited(probe.ini_addopts, config.strip_flags)
        inherited += filter_inherited(probe.env_addopts, config.strip_flags)
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        RECORDER_PLUGIN,
        # Empty the ini addopts; the filtered tokens are passed explicitly.
        "-o",
        "addopts=",
        *inherited,
        *config.args,
        f"--basetemp={job_dir / 'basetemp'}",
    ]
    if "cacheprovider" in probe.plugin_names:
        # With the plugin blocked the option is unknown, which is a warning
        # and, under filterwarnings = error, a crash.
        argv += ["-o", f"cache_dir={job_dir / 'pytest-cache'}"]
    uses_randomly = any(token.startswith("--randomly") for token in inherited)
    if probe.has_plugin("pytest-randomly") and uses_randomly:
        # Blocking a plugin whose flags are still present is a usage error.
        argv += ["--randomly-seed=1", "--randomly-dont-reorganize"]
    else:
        argv += ["-p", "no:randomly"]
    if not any(token.startswith("--random-order") for token in inherited):
        argv += ["-p", "no:random_order"]
    elif probe.has_plugin("pytest-random-order"):
        # Its flags are present, so it cannot be blocked; the "none" bucket,
        # given last, turns shuffling off.
        argv.append("--random-order-bucket=none")
    if probe.has_plugin("pytest-xdist"):
        argv += ["-n", "0"]
    argv += list(config.extra_overrides)
    if probe.has_plugin("pytest-cov"):
        # Must come last: pytest-cov warns if a --cov option follows it.
        argv.append("--no-cov")
    return tuple(argv)
