"""pytest plugin: ``pytest --mutate`` runs mutation testing instead of the tests.

The plugin is registered for every pytest run in an environment where
pytestmut is installed, so it imports nothing heavy until the flag is given.
Inside the test processes pytestmut itself starts, it does nothing at all.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

_ACTIVE = "PYTESTMUT_ACTIVE"
_OWN_FLAGS = frozenset({"--mutate", "--mutate-reached"})
_OWN_VALUE_FLAGS = frozenset({"--mutate-level", "--mutate-min-score", "--mutate-diff"})


def pytest_addoption(parser: Any) -> None:
    """Register the mutation testing options."""
    group = parser.getgroup("pytestmut", "mutation testing")
    group.addoption(
        "--mutate",
        action="store_true",
        default=False,
        help="Run mutation testing with pytestmut instead of the tests.",
    )
    group.addoption(
        "--mutate-reached",
        action="store_true",
        default=False,
        help="Only mutate code the selected tests execute.",
    )
    group.addoption(
        "--mutate-level",
        default=None,
        help="Operator level: core, extended or all.",
    )
    group.addoption(
        "--mutate-min-score",
        type=float,
        default=None,
        help="Minimum mutation score of the default policy, in percent.",
    )
    group.addoption(
        "--mutate-diff",
        default=None,
        help="Only mutate lines changed against this git ref.",
    )


def _test_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    """The invocation's arguments without the plugin's own options."""
    kept: list[str] = []
    skip = False
    for argument in arguments:
        if skip:
            skip = False
            continue
        name = argument.split("=", 1)[0]
        if argument in _OWN_FLAGS:
            continue
        if name in _OWN_VALUE_FLAGS:
            skip = "=" not in argument
            continue
        kept.append(argument)
    return tuple(kept)


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: Any) -> int | None:
    """Hand the run over to pytestmut when ``--mutate`` was given."""
    if not config.getoption("mutate", False) or os.environ.get(_ACTIVE):
        return None
    from dataclasses import replace

    from pytestmut.config.loader import load_config, with_overrides
    from pytestmut.exceptions import PytestmutError
    from pytestmut.orchestrator import RunOptions
    from pytestmut.session import run_and_report

    arguments = _test_arguments(tuple(config.invocation_params.args))
    try:
        loaded = load_config(config.rootpath)
        if arguments:
            loaded = replace(loaded, pytest=replace(loaded.pytest, args=arguments))
        loaded = with_overrides(
            loaded,
            level=config.getoption("mutate_level"),
            min_score=config.getoption("mutate_min_score"),
            diff_base=config.getoption("mutate_diff"),
        )
    except PytestmutError as exc:
        print(f"pytestmut: error: {exc}")
        return exc.exit_code
    options = RunOptions(reached_only=bool(config.getoption("mutate_reached", False)))
    return run_and_report(loaded, options)
