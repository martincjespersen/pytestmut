"""Source lookups shared by the report renderers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pytestmut.exceptions import SourceError
from pytestmut.mutation.model import Mutant
from pytestmut.report.model import RunResult
from pytestmut.source.text import SourceText


class SourceCache:
    """Reads the files a result refers to, when they are still unchanged.

    Arguments:
        result: The run result.
    """

    def __init__(self, result: RunResult) -> None:
        self._root = Path(result.project_root)
        self._digests = {item.path: item.sha256 for item in result.files}
        self._cache: dict[str, SourceText | None] = {}

    def get(self, relpath: str) -> SourceText | None:
        """Source of a target file.

        Arguments:
            relpath: POSIX path relative to the project root.

        Returns:
            The decoded file, or None when it is gone or has changed since
            the run.
        """
        if relpath not in self._cache:
            source: SourceText | None = None
            try:
                candidate = SourceText.from_file(self._root / relpath)
                digest = hashlib.sha256(candidate.raw).hexdigest()
                if digest == self._digests.get(relpath):
                    source = candidate
            except SourceError:
                source = None
            self._cache[relpath] = source
        return self._cache[relpath]

    def diff(self, mutant: Mutant) -> tuple[list[str], list[str]]:
        """Lines before and after a mutation.

        Arguments:
            mutant: The mutant.

        Returns:
            ``(original lines, mutated lines)``. Without the source file the
            bare original and replacement text are returned.
        """
        source = self.get(mutant.path)
        if source is None:
            return [mutant.original], [mutant.replacement]
        span = mutant.span
        before = [
            source.line_text(line) for line in range(span.line, span.end_line + 1)
        ]
        head = before[0][: span.col]
        tail = before[-1][span.end_col :]
        after = (head + mutant.replacement + tail).split("\n")
        return before, [line.rstrip("\r") for line in after]
