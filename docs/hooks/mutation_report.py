"""MkDocs hook: publish the latest mutation testing report with the docs.

``pytestmut run`` writes a self-contained HTML report. This hook copies it into
the site as ``mutation-report/index.html``, where the "Mutation report" page
shows it. The location is read from ``[tool.pytestmut.report]`` so it follows
the configuration. Without a report the page explains how to make one, and
``mkdocs serve`` reloads when a new report is written.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.livereload import LiveReloadServer
from mkdocs.structure.files import File, Files

from pytestmut.config.loader import load_config
from pytestmut.exceptions import PytestmutError

SITE_PATH = "mutation-report/index.html"
PLACEHOLDER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mutation report</title>
<style>
body { margin: 0; padding: 24px 16px; font: 15px/1.5 system-ui, sans-serif;
       color: #1c1d1f; background: #fbfbfa; }
@media (prefers-color-scheme: dark) { body { color: #e8e9eb; background: #16171a; } }
code { font-family: ui-monospace, Menlo, Consolas, monospace; }
</style>
</head>
<body>
<p>No mutation report has been generated yet.</p>
<p>Run <code>uv run nox -s "Serve Docs"</code>, which runs pytestmut before
serving, or run <code>uv run pytestmut run --report html</code> yourself.</p>
</body>
</html>
"""


def _report_path(config: MkDocsConfig) -> Path | None:
    """Where ``pytestmut run`` writes the HTML report for this project."""
    project = Path(config.config_file_path).resolve().parent
    try:
        settings = load_config(project)
    except PytestmutError:
        return None
    return settings.root / settings.report.output_dir / "report.html"


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Add the report, or a placeholder, to the files of the site."""
    report = _report_path(config)
    if report is not None and report.is_file():
        files.append(File.generated(config, SITE_PATH, abs_src_path=str(report)))
    else:
        files.append(File.generated(config, SITE_PATH, content=PLACEHOLDER))
    return files


def on_serve(
    server: LiveReloadServer, config: MkDocsConfig, builder: Callable[..., Any]
) -> LiveReloadServer:
    """Rebuild the served site when a new report is written."""
    report = _report_path(config)
    if report is not None and report.parent.is_dir():
        server.watch(str(report.parent))
    return server
