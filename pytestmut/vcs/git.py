"""Read-only git queries: changed lines and the state of tracked files.

Nothing here writes to the repository. ``GIT_OPTIONAL_LOCKS=0`` keeps even
``git status`` from refreshing the index, so a run never races an editor or a
commit in progress.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pytestmut.exceptions import GitError

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class TreeSnapshot:
    """State of the tracked files that differ from the index or HEAD.

    Attributes:
        entries: ``(status line, content digest)`` per changed tracked file.
    """

    entries: frozenset[tuple[str, str]]

    def changed_since(self, earlier: TreeSnapshot) -> list[str]:
        """Status lines present now and absent before, and the reverse."""
        return sorted(line for line, _ in self.entries ^ earlier.entries)


class Git:
    """Queries one working tree.

    Arguments:
        root: A directory inside the working tree.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _run(self, *args: str) -> tuple[int, str]:
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self._root,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return 127, ""
        return completed.returncode, completed.stdout.decode("utf-8", "replace")

    def toplevel(self) -> Path | None:
        """Root of the working tree, or None outside a repository."""
        code, out = self._run("rev-parse", "--show-toplevel")
        return Path(out.strip()) if code == 0 and out.strip() else None

    def snapshot(self) -> TreeSnapshot | None:
        """Capture which tracked files are modified, and their content.

        Returns:
            The snapshot, or None outside a repository.
        """
        top = self.toplevel()
        if top is None:
            return None
        code, out = self._run("status", "--porcelain=v1", "-z", "--untracked-files=no")
        if code != 0:
            return None
        entries: set[tuple[str, str]] = set()
        fields = [field for field in out.split("\0") if field]
        skip = False
        for field in fields:
            if skip:
                # The source path of a rename follows its entry.
                skip = False
                continue
            status, path = field[:2], field[3:]
            skip = "R" in status or "C" in status
            try:
                digest = hashlib.sha256((top / path).read_bytes()).hexdigest()
            except OSError:
                digest = "missing"
            entries.add((f"{status} {path}", digest))
        return TreeSnapshot(entries=frozenset(entries))

    def changed_lines(self, base: str, staged: bool) -> dict[str, frozenset[int]]:
        """Lines added or changed relative to a base.

        Arguments:
            base: Ref to compare with; ignored when ``staged`` is set.
            staged: Compare the index with HEAD instead of the working tree
                with ``base``.

        Returns:
            Changed line numbers by POSIX path relative to this root.

        Raises:
            GitError: Outside a repository, or when the ref is unknown.
        """
        top = self.toplevel()
        if top is None:
            raise GitError("diff mode needs a git repository")
        args = ["diff", "--unified=0", "--no-color", "--no-ext-diff", "--relative"]
        args += ["--cached"] if staged else [base]
        code, out = self._run(*args, "--", "*.py")
        if code != 0:
            what = "the index" if staged else repr(base)
            raise GitError(f"cannot diff against {what}; is the ref valid?")
        changed: dict[str, set[int]] = {}
        current: set[int] | None = None
        for line in out.splitlines():
            if line.startswith("+++ "):
                name = line[4:].strip()
                if name == "/dev/null":
                    current = None
                else:
                    current = changed.setdefault(name.removeprefix("b/"), set())
            elif current is not None:
                match = _HUNK.match(line)
                if match:
                    start = int(match.group(1))
                    count = int(match.group(2) or "1")
                    current.update(range(start, start + count))
        return {path: frozenset(lines) for path, lines in changed.items() if lines}
