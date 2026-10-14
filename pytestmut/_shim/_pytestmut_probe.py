"""pytest plugin that reports how pytest is configured, then exits.

Loaded with ``-p`` for one short run before anything else. It tells pytestmut
which options the project injects, which plugins are installed and which files
count as tests, without running or even collecting a test.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from typing import Any

import pytest

ENV_PROBE_OUT = "PYTESTMUT_PROBE_OUT"


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: Any) -> int | None:
    """Write the probe result and end the run successfully."""
    target = os.environ.get(ENV_PROBE_OUT)
    if not target:
        return None
    plugins: dict[str, str] = {}
    for _plugin, dist in config.pluginmanager.list_plugin_distinfo():
        plugins[str(dist.project_name).lower().replace("_", "-")] = str(dist.version)
    data = {
        "pytest": pytest.__version__,
        "python": sys.version.split()[0],
        "cache_tag": sys.implementation.cache_tag,
        "executable": sys.executable,
        "rootdir": str(config.rootpath),
        "inipath": str(config.inipath) if config.inipath else "",
        "ini_addopts": [str(item) for item in config.getini("addopts")],
        "env_addopts": shlex.split(os.environ.get("PYTEST_ADDOPTS", "")),
        "python_files": [str(item) for item in config.getini("python_files")],
        "testpaths": [str(item) for item in config.getini("testpaths")],
        "plugins": plugins,
        "plugin_names": sorted(
            name for name, plugin in config.pluginmanager.list_name_plugin() if plugin
        ),
    }
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle)
    return 0
