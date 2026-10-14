"""MkDocs hook: publish the latest dependency license report with the docs.

``loglicense check --output-file build/licenses.md`` (run by the "License
Checking", "Serve Docs" and "Build Docs" nox sessions) writes a table of every
runtime dependency and its license. This hook turns it into the "Dependency
licenses" page. Without a report the page explains how to make one, and
``mkdocs serve`` reloads when a new report is written.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.livereload import LiveReloadServer
from mkdocs.structure.files import File, Files

PAGE_PATH = "licenses.md"
REPORT = Path("build", "licenses.md")
INTRO = """# Dependency licenses

Every package pytestmut needs at runtime, with its license, checked by
[loglicense](https://pypi.org/project/loglicense/) against the allowed and
banned licenses in `.loglicense` at the root of the repository. Development
tools are not listed: they are not installed with pytestmut.

"""
PLACEHOLDER = """No license report has been generated yet.

Run `uv run nox -s "Serve Docs"`, which checks the licenses before serving, or
run `uv run nox -s "License Checking"` yourself.
"""


def _report_path(config: MkDocsConfig) -> Path:
    """Where the nox sessions write the license table for this project."""
    return Path(config.config_file_path).resolve().parent / REPORT


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Add the license page, built from the report or a placeholder."""
    report = _report_path(config)
    if report.is_file():
        body = report.read_text(encoding="utf-8")
    else:
        body = PLACEHOLDER
    files.append(File.generated(config, PAGE_PATH, content=INTRO + body))
    return files


def on_serve(
    server: LiveReloadServer, config: MkDocsConfig, builder: Callable[..., Any]
) -> LiveReloadServer:
    """Rebuild the served site when a new report is written."""
    report = _report_path(config)
    if report.parent.is_dir():
        server.watch(str(report.parent))
    return server
