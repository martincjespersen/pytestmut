"""Ask pytest how it is configured before running anything."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pytestmut._meta import ENV_PROBE_OUT, PROBE_PLUGIN
from pytestmut.exceptions import BaselineError


@dataclass(frozen=True)
class PytestEnvironment:
    """What the probe learned about the project's pytest setup.

    Attributes:
        pytest_version: Version of pytest.
        python_version: Version of the interpreter running the tests.
        cache_tag: Bytecode cache tag of that interpreter.
        rootdir: pytest's rootdir.
        inipath: The ini file pytest picked, or empty.
        ini_addopts: Tokens of the ini ``addopts`` setting.
        env_addopts: Tokens of ``PYTEST_ADDOPTS``.
        python_files: Globs that mark a file as a test file.
        testpaths: Directories pytest searches by default.
        plugins: Installed plugin distributions and their versions.
        plugin_names: Names of the registered plugins.
    """

    pytest_version: str
    python_version: str
    cache_tag: str
    rootdir: Path
    inipath: str
    ini_addopts: tuple[str, ...]
    env_addopts: tuple[str, ...]
    python_files: tuple[str, ...]
    testpaths: tuple[str, ...]
    plugins: Mapping[str, str]
    plugin_names: tuple[str, ...]

    def has_plugin(self, distribution: str) -> bool:
        """Tell whether a plugin distribution is installed.

        Arguments:
            distribution: Distribution name such as ``pytest-xdist``.

        Returns:
            True when the plugin is registered with pytest.
        """
        return distribution.lower().replace("_", "-") in self.plugins


def run_probe(
    project_root: Path,
    pytest_args: tuple[str, ...],
    env: Mapping[str, str],
    scratch: Path,
    timeout_s: float = 120.0,
) -> PytestEnvironment:
    """Run pytest with the probe plugin and read its report.

    Arguments:
        project_root: Directory pytest is started in.
        pytest_args: The user's pytest arguments.
        env: Environment for the probe process; the caller's addopts must
            still be in it, since reading them is the point.
        scratch: Directory for the probe's output file.
        timeout_s: Budget for the probe.

    Returns:
        The pytest setup.

    Raises:
        BaselineError: If pytest cannot start in this project.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    out = scratch / "probe.json"
    probe_env = dict(env)
    probe_env[ENV_PROBE_OUT] = str(out)
    argv = [sys.executable, "-m", "pytest", "-p", PROBE_PLUGIN, *pytest_args]
    try:
        completed = subprocess.run(
            argv,
            cwd=project_root,
            env=probe_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BaselineError(
            f"pytest did not start within {timeout_s:.0f}s in {project_root}"
        ) from exc
    except OSError as exc:
        raise BaselineError(f"cannot run pytest: {exc}") from exc
    if completed.returncode != 0 or not out.is_file():
        output = (completed.stdout + completed.stderr).decode("utf-8", "replace")
        raise BaselineError(
            "pytest cannot start in this project "
            f"(exit code {completed.returncode}):\n{output.strip()[-2000:]}"
        )
    data = json.loads(out.read_text(encoding="utf-8"))
    return PytestEnvironment(
        pytest_version=data["pytest"],
        python_version=data["python"],
        cache_tag=data["cache_tag"],
        rootdir=Path(data["rootdir"]),
        inipath=data["inipath"],
        ini_addopts=tuple(data["ini_addopts"]),
        env_addopts=tuple(data["env_addopts"]),
        python_files=tuple(data["python_files"]),
        testpaths=tuple(data["testpaths"]),
        plugins=dict(data["plugins"]),
        plugin_names=tuple(data["plugin_names"]),
    )


def probe_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Environment for the probe: the worker environment plus user addopts.

    Arguments:
        env: The worker environment, from which ``PYTEST_ADDOPTS`` was
            removed.

    Returns:
        A copy with the caller's ``PYTEST_ADDOPTS`` restored so the probe
        can report it.
    """
    probe_env = dict(env)
    addopts = os.environ.get("PYTEST_ADDOPTS")
    if addopts:
        probe_env["PYTEST_ADDOPTS"] = addopts
    return probe_env
