"""Path globs and the search for files to mutate.

Globs always use ``/`` and are matched against POSIX paths relative to the
project root, so one configuration behaves the same on every OS. A pattern
without a slash matches at any depth, and any pattern that matches a directory
also matches everything below it.
"""

from __future__ import annotations

import fnmatch
import functools
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pytestmut.exceptions import ConfigError

_GLOB_CHARS = frozenset("*?[")
_SKIPPED_DIRS = frozenset(
    {"__pycache__", "node_modules", "site-packages", "build", "dist"}
)


def _bracket_end(pattern: str, start: int) -> int | None:
    """Index of the ``]`` closing the bracket expression at ``start``.

    As in ``fnmatch``, a ``]`` right after ``[`` or ``[!`` is a member of the
    set, not its end.

    Arguments:
        pattern: The glob.
        start: Index of the opening ``[``.

    Returns:
        The index of the closing ``]``, or None when there is none and the
        ``[`` is an ordinary character.
    """
    first = start + 1
    if pattern[first : first + 1] == "!":
        first += 1
    if pattern[first : first + 1] == "]":
        first += 1
    close = pattern.find("]", first)
    return None if close == -1 else close


def _bracket(body: str) -> str:
    """Regex character set for the inside of a glob bracket expression."""
    negated = body.startswith("!")
    if negated:
        body = body[1:]
    # A leading caret or bracket means itself in a glob. Backslashes cannot
    # occur: patterns have them turned into slashes before this point.
    if body[:1] in {"^", "["}:
        body = "\\" + body
    return f"[{'^' if negated else ''}{body}]"


@functools.lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    cleaned = pattern.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.strip("/") or "**"
    if "/" not in cleaned:
        cleaned = "**/" + cleaned
    parts: list[str] = []
    index = 0
    while index < len(cleaned):
        char = cleaned[index]
        if cleaned.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif cleaned.startswith("**", index):
            parts.append(".*")
            index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        elif char == "[":
            close = _bracket_end(cleaned, index)
            if close is None:
                parts.append(re.escape(char))
                index += 1
            else:
                parts.append(_bracket(cleaned[index + 1 : close]))
                index = close + 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("".join(parts) + r"(?:/.*)?\Z")


def path_matches(relpath: str, patterns: tuple[str, ...]) -> bool:
    """Tell whether a relative POSIX path matches any glob.

    Arguments:
        relpath: Path relative to the project root, with ``/`` separators.
        patterns: Globs as described in the module docstring.

    Returns:
        True on the first match.
    """
    return any(_compile(pattern).match(relpath) for pattern in patterns)


@dataclass(frozen=True)
class TargetFile:
    """A file selected for mutation.

    Attributes:
        path: Absolute path with symlinks resolved.
        relpath: POSIX path relative to the project root.
    """

    path: Path
    relpath: str


@dataclass(frozen=True)
class Discovery:
    """Result of the search for files to mutate.

    Attributes:
        targets: Files to mutate, sorted by relative path.
        excluded: Files left out, as ``(relative path, reason)``.
    """

    targets: tuple[TargetFile, ...]
    excluded: tuple[tuple[str, str], ...]


def discover_targets(
    root: Path,
    paths: tuple[str, ...],
    exclude: tuple[str, ...],
    test_file_patterns: tuple[str, ...],
    test_paths: tuple[str, ...],
    state_dir: Path,
) -> Discovery:
    """Find the source files to mutate.

    Arguments:
        root: Project root.
        paths: Files, directories or globs from the configuration.
        exclude: Path globs to leave out.
        test_file_patterns: pytest's ``python_files`` globs; matching files
            are tests and are never mutated.
        test_paths: pytest's ``testpaths``; files below them are left out.
        state_dir: The tool's own directory, never searched.

    Returns:
        The targets and what was excluded.

    Raises:
        ConfigError: If a configured path does not exist or leaves the
            project root.
    """
    root = root.resolve()
    candidates: dict[str, Path] = {}
    for entry in paths:
        for path in _expand(root, entry, state_dir.resolve()):
            try:
                relpath = PurePosixPath(path.relative_to(root)).as_posix()
            except ValueError:
                raise ConfigError(
                    f"paths: {entry!r} is outside the project root {root}"
                ) from None
            candidates[relpath] = path
    test_dirs = tuple(item.strip("/") for item in test_paths if item.strip("/"))
    targets: list[TargetFile] = []
    excluded: list[tuple[str, str]] = []
    for relpath in sorted(candidates):
        name = relpath.rpartition("/")[2]
        if path_matches(relpath, exclude):
            excluded.append((relpath, "matches exclude"))
        elif name == "conftest.py" or any(
            fnmatch.fnmatchcase(name, pattern) for pattern in test_file_patterns
        ):
            excluded.append((relpath, "test file"))
        elif any(relpath.startswith(item + "/") for item in test_dirs):
            excluded.append((relpath, "inside testpaths"))
        else:
            targets.append(TargetFile(path=candidates[relpath], relpath=relpath))
    return Discovery(targets=tuple(targets), excluded=tuple(excluded))


def _expand(root: Path, entry: str, state_dir: Path) -> list[Path]:
    if _GLOB_CHARS & set(entry):
        return [
            path
            for path in _walk(root, state_dir)
            if path_matches(PurePosixPath(path.relative_to(root)).as_posix(), (entry,))
        ]
    path = (root / entry).resolve()
    if path.is_file():
        return [path]
    if path.is_dir():
        return _walk(path, state_dir)
    raise ConfigError(f"paths: {entry!r} does not exist under {root}")


def _walk(top: Path, state_dir: Path) -> list[Path]:
    found: list[Path] = []
    for directory, names, files in os.walk(top):
        here = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if not name.startswith(".")
            and name not in _SKIPPED_DIRS
            and not name.endswith(".egg-info")
            and here / name != state_dir
            and not (here / name / "pyvenv.cfg").exists()
        )
        found.extend(here / name for name in sorted(files) if name.endswith(".py"))
    return found
