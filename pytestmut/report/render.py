"""Render a result in the configured formats."""

from __future__ import annotations

from pathlib import Path

from pytestmut.exceptions import ConfigError
from pytestmut.report.html import render_html
from pytestmut.report.markdown import render_markdown
from pytestmut.report.model import RunResult, to_json
from pytestmut.report.text import render_text

FILE_NAMES = {
    "text": "report.txt",
    "markdown": "report.md",
    "html": "report.html",
    "json": "report.json",
}


def render(
    result: RunResult,
    fmt: str,
    show_suppressed: bool = False,
    markdown_max_survivors: int = 50,
    embed_source: bool = True,
) -> str:
    """Render a result in one format.

    Arguments:
        result: The run result.
        fmt: ``text``, ``markdown``, ``html`` or ``json``.
        show_suppressed: List suppressed candidates in the text report.
        markdown_max_survivors: Survivors listed in the markdown report.
        embed_source: Embed source code in the HTML report.

    Returns:
        The rendered report.

    Raises:
        ConfigError: If the format is unknown.
    """
    if fmt == "text":
        return render_text(result, show_suppressed=show_suppressed)
    if fmt == "markdown":
        return render_markdown(result, max_survivors=markdown_max_survivors)
    if fmt == "html":
        return render_html(result, embed_source=embed_source)
    if fmt == "json":
        return to_json(result)
    raise ConfigError(f"unknown report format {fmt!r}")


def write_reports(
    result: RunResult,
    formats: tuple[str, ...],
    output_dir: Path,
    markdown_max_survivors: int = 50,
    embed_source: bool = True,
) -> dict[str, Path]:
    """Write one file per format.

    Arguments:
        result: The run result.
        formats: Formats to write.
        output_dir: Directory for the files; created if missing.
        markdown_max_survivors: Survivors listed in the markdown report.
        embed_source: Embed source code in the HTML report.

    Returns:
        The written file per format.
    """
    written: dict[str, Path] = {}
    if not formats:
        return written
    output_dir.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        path = output_dir / FILE_NAMES[fmt]
        text = render(
            result,
            fmt,
            markdown_max_survivors=markdown_max_survivors,
            embed_source=embed_source,
        )
        path.write_text(text, encoding="utf-8", newline="\n")
        written[fmt] = path
    return written
