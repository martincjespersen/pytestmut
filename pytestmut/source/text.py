"""Source text with the three coordinate systems a mutation has to cross.

``ast`` reports columns as UTF-8 byte offsets within a line, ``tokenize``
reports them as character offsets, and a mutation finally has to land on the
raw bytes of the file in whatever encoding it declares. ``SourceText`` uses the
character index into the decoded text as its one canonical coordinate and
converts at the edges.
"""

from __future__ import annotations

import bisect
import codecs
import hashlib
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

from pytestmut.exceptions import SourceError

# Python's own line terminators. ``str.splitlines`` also splits on form feeds
# and Unicode separators, which would shift every later line number.
_NEWLINE = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class SourceText:
    """One decoded source file.

    Attributes:
        path: Where the file was read from.
        raw: The exact bytes on disk.
        encoding: Codec that decodes ``raw`` once the BOM is removed.
        bom: The byte-order mark the file starts with, or empty.
        text: The decoded text with its original line endings.
        normalized: ``text`` with every line ending replaced by ``\\n``; this
            is what ``ast`` and ``tokenize`` are given. Rows and columns are
            identical in both forms.
        line_starts: Character index of the start of each line, 0-based.
    """

    path: Path
    raw: bytes
    encoding: str
    bom: bytes
    text: str
    normalized: str
    line_starts: tuple[int, ...]

    @classmethod
    def from_file(cls, path: Path) -> SourceText:
        """Read and decode a source file.

        Arguments:
            path: File to read.

        Returns:
            The decoded source.

        Raises:
            SourceError: If the file cannot be read or decoded.
        """
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {exc}") from exc
        return cls.from_bytes(raw, path)

    @classmethod
    def from_bytes(cls, raw: bytes, path: Path) -> SourceText:
        """Decode source bytes the way the interpreter would.

        Arguments:
            raw: File content.
            path: Path used in messages.

        Returns:
            The decoded source.

        Raises:
            SourceError: If the declared encoding is unknown, the content does
                not decode, or decoding does not round-trip byte for byte.
        """
        try:
            declared, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        except (SyntaxError, LookupError) as exc:
            raise SourceError(f"{path}: {exc}") from exc
        bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
        encoding = "utf-8" if declared == "utf-8-sig" else declared
        body = raw[len(bom) :]
        try:
            text = body.decode(encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise SourceError(f"{path}: cannot decode as {encoding}: {exc}") from exc
        if text.encode(encoding) != body:
            raise SourceError(
                f"{path}: encoding {encoding} does not round-trip, so offsets "
                "cannot be mapped back to bytes"
            )
        starts = [0]
        starts.extend(match.end() for match in _NEWLINE.finditer(text))
        return cls(
            path=path,
            raw=raw,
            encoding=encoding,
            bom=bom,
            text=text,
            normalized=_NEWLINE.sub("\n", text),
            line_starts=tuple(starts),
        )

    @property
    def sha256(self) -> str:
        """Hex digest of the raw file bytes."""
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def line_count(self) -> int:
        """Number of lines, counting a trailing unterminated line."""
        return len(self.line_starts)

    def line_text(self, line: int) -> str:
        """Return one line without its terminator.

        Arguments:
            line: 1-based line number.

        Returns:
            The text of that line.
        """
        start = self.line_starts[line - 1]
        end = self.line_starts[line] if line < len(self.line_starts) else len(self.text)
        return self.text[start:end].rstrip("\r\n")

    def offset(self, line: int, col: int) -> int:
        """Convert a ``tokenize`` position to a character index.

        Arguments:
            line: 1-based line number.
            col: 0-based character column.

        Returns:
            Index into ``text``.
        """
        if line > len(self.line_starts):
            return len(self.text)
        return self.line_starts[line - 1] + col

    def ast_offset(self, line: int, utf8_col: int) -> int:
        """Convert an ``ast`` position to a character index.

        Arguments:
            line: 1-based line number.
            utf8_col: 0-based column counted in UTF-8 bytes.

        Returns:
            Index into ``text``.
        """
        if line > len(self.line_starts):
            return len(self.text)
        content = self.line_text(line)
        if content.isascii():
            return self.line_starts[line - 1] + utf8_col
        prefix = content.encode("utf-8")[:utf8_col]
        return self.line_starts[line - 1] + len(prefix.decode("utf-8", "ignore"))

    def position(self, index: int) -> tuple[int, int]:
        """Convert a character index to a 1-based line and 0-based column.

        Arguments:
            index: Index into ``text``.

        Returns:
            ``(line, column)`` with the column counted in characters.
        """
        row = max(bisect.bisect_right(self.line_starts, index) - 1, 0)
        return row + 1, index - self.line_starts[row]

    def byte_offset(self, index: int) -> int:
        """Convert a character index to an offset into ``raw``.

        Arguments:
            index: Index into ``text``.

        Returns:
            Offset into the file bytes, BOM included.
        """
        return len(self.bom) + len(self.text[:index].encode(self.encoding))

    def encode(self, fragment: str) -> bytes:
        """Encode replacement text in the file's own encoding.

        Arguments:
            fragment: Text to encode.

        Returns:
            The encoded bytes.

        Raises:
            SourceError: If the fragment cannot be represented.
        """
        try:
            return fragment.encode(self.encoding)
        except UnicodeEncodeError as exc:
            raise SourceError(
                f"{self.path}: replacement is not encodable as {self.encoding}"
            ) from exc
