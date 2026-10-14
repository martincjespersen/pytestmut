"""Source text coordinates, path globs and file discovery."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from pytestmut.exceptions import ConfigError, SourceError
from pytestmut.source.discovery import discover_targets, path_matches
from pytestmut.source.scopes import ModuleContext
from pytestmut.source.text import SourceText

SAMPLES = {
    "lf": "x = 1\ny = x + 2\n".encode(),
    "crlf": "x = 1\r\ny = x + 2\r\n".encode(),
    "cr": "x = 1\ry = x + 2\r".encode(),
    "no-final-newline": "x = 1\ny = x + 2".encode(),
    "bom": b"\xef\xbb\xbf" + "x = 1\ny = x + 2\n".encode(),
    "latin-1": b"# coding: latin-1\nname = 'caf\xe9'\ny = name + 'x'\n",
    "non-ascii": "naïve = 'ünï'\ny = naïve + 'x'\n".encode(),
    "form-feed": "x = 1\n\x0c\ny = x + 2\n".encode(),
    "unicode-separator": "s = 'a\u2028b'\ny = s + 'x'\n".encode(),
    "tabs": "if True:\n\ty = 1 + 2\n".encode(),
}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_binary_operator_is_found_and_spliced(name: str, tmp_path: Path) -> None:
    raw = SAMPLES[name]
    source = SourceText.from_bytes(raw, tmp_path / "sample.py")
    ctx = ModuleContext.parse(source)
    additions = [node for node in ast.walk(ctx.tree) if isinstance(node, ast.BinOp)]
    assert len(additions) == 1
    node = additions[0]
    tokens = [
        token
        for token in ctx.tokens_between(ctx.end(node.left), ctx.start(node.right))
        if token.string == "+"
    ]
    assert len(tokens) == 1
    token = tokens[0]
    assert source.text[token.start : token.end] == "+"
    start, end = source.byte_offset(token.start), source.byte_offset(token.end)
    assert raw[start:end] == b"+"
    mutated = raw[:start] + b"-" + raw[end:]
    # Everything except the one byte is untouched: BOM, encoding, newlines.
    assert len(mutated) == len(raw)
    assert [a for a, b in zip(raw, mutated) if a != b] == [ord("+")]
    compile(mutated, "sample.py", "exec")


def test_line_table_follows_python_not_splitlines(tmp_path: Path) -> None:
    raw = "a = 1\n\x0cb = 2\nc = 'x\u2028y'\nd = 4\n".encode()
    source = SourceText.from_bytes(raw, tmp_path / "s.py")
    tree = ast.parse(source.normalized)
    for statement in tree.body:
        assert isinstance(statement, ast.Assign)
        target = statement.targets[0]
        assert isinstance(target, ast.Name)
        offset = source.ast_offset(target.lineno, target.col_offset)
        assert source.text[offset] == target.id
    assert source.line_count == 5
    assert source.position(source.offset(4, 0)) == (4, 0)


def test_offsets_on_non_ascii_lines_and_past_the_end(tmp_path: Path) -> None:
    raw = "a = 'é'\nb = 'ü' + c\nd = 1 + 2\n".encode()
    source = SourceText.from_bytes(raw, tmp_path / "s.py")
    assert source.sha256 == hashlib.sha256(raw).hexdigest()
    # ast counts UTF-8 bytes: "ü" is two of them, so byte and character
    # columns of the "+" differ by one on this line.
    utf8_col = "b = 'ü' + c".encode().index(b"+")
    assert source.text[source.ast_offset(2, utf8_col)] == "+"
    assert source.text[source.ast_offset(3, 6)] == "+"
    assert source.offset(99, 0) == len(source.text)
    assert source.ast_offset(99, 0) == len(source.text)
    # Every character maps to a line and column and back.
    for index in range(len(source.text) + 1):
        line, col = source.position(index)
        assert source.offset(line, col) == index
    assert source.position(0) == (1, 0)
    assert source.position(source.line_starts[1]) == (2, 0)
    assert source.position(source.line_starts[1] - 1) == (1, 7)


def test_undecodable_source_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        SourceText.from_bytes(b"x = '\xff\xfe'\n", tmp_path / "bad.py")
    with pytest.raises(SourceError):
        SourceText.from_bytes(b"# coding: no-such-codec\nx = 1\n", tmp_path / "bad.py")
    with pytest.raises(SourceError):
        ModuleContext.parse(SourceText.from_bytes(b"def f(:\n", tmp_path / "bad.py"))


def test_scopes_and_cover_lines(tmp_path: Path) -> None:
    text = (
        "class A:\n"
        "    @property\n"
        "    def size(self):\n"
        "        if (self.a and\n"
        "                self.b):\n"
        "            return 1\n"
        "        return compute(\n"
        "            2,\n"
        "        )\n"
    )
    ctx = ModuleContext.parse(SourceText.from_bytes(text.encode(), tmp_path / "m.py"))
    assert [(s.qualname, s.kind, s.first_line, s.def_line) for s in ctx.scopes] == [
        ("A", "class", 1, 1),
        ("A.size", "function", 2, 3),
    ]
    boolop = next(n for n in ast.walk(ctx.tree) if isinstance(n, ast.BoolOp))
    assert ctx.scope_of(boolop) == "A.size"
    # A mutant in a compound header is reached when the header runs.
    assert ctx.cover_lines(boolop) == (4, 5)
    constant = next(
        n for n in ast.walk(ctx.tree) if isinstance(n, ast.Constant) and n.value == 2
    )
    # A mutant inside a multi-line simple statement covers the whole statement.
    assert ctx.cover_lines(constant) == (7, 9)


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("src", "src/pkg/mod.py", True),
        ("src", "srcs/pkg/mod.py", False),
        ("src/pkg/mod.py", "src/pkg/mod.py", True),
        ("src/pkg", "src/pkg/sub/mod.py", True),
        ("src/pkg/**", "src/pkg/sub/mod.py", True),
        ("src/*/mod.py", "src/pkg/mod.py", True),
        ("src/*/mod.py", "src/pkg/sub/mod.py", False),
        ("**/migrations/**", "app/db/migrations/0001.py", True),
        ("**/migrations/**", "migrations/0001.py", True),
        ("migrations", "app/db/migrations/0001.py", True),
        ("_version.py", "src/pkg/_version.py", True),
        ("*.py", "src/pkg/mod.py", True),
        ("test_*.py", "src/pkg/mod.py", False),
        ("./src", "src/a.py", True),
        ("src\\pkg", "src/pkg/a.py", True),
        ("src/[ab].py", "src/a.py", True),
        ("src/[!ab].py", "src/a.py", False),
        ("src/?.py", "src/ab.py", False),
        ("src/?.py", "src/a.py", True),
        ("src/**.py", "src/a/b.py", True),
        ("src/**.py", "src/a_py", False),
        ("src/a[b.py", "src/a[b.py", True),
        ("src/a[b.py", "src/ab.py", False),
        ("src/x][ab].py", "src/x]a.py", True),
        # As in fnmatch: "]" first in a set is a member, and a leading "^"
        # or "[" means itself.
        ("src/[]a].py", "src/].py", True),
        ("src/[]a].py", "src/b.py", False),
        ("src/[!]].py", "src/a.py", True),
        ("src/[!]].py", "src/].py", False),
        ("src/[^a].py", "src/^.py", True),
        ("src/[^a].py", "src/b.py", False),
        ("src/[[a].py", "src/[.py", True),
        ("src/[a].py", "src/a.py", True),
        ("src/[]].py", "src/].py", True),
        ("src/[]].py", "src/a.py", False),
    ],
)
def test_path_globs(pattern: str, path: str, expected: bool) -> None:
    assert path_matches(path, (pattern,)) is expected


def test_discovery_skips_tests_and_tooling(tmp_path: Path) -> None:
    files = [
        "src/pkg/__init__.py",
        "src/pkg/core.py",
        "src/pkg/core_test.py",
        "src/pkg/conftest.py",
        "src/pkg/migrations/0001.py",
        "src/pkg/__pycache__/core.py",
        "src/pkg/.hidden/x.py",
        "src/venv/pyvenv.cfg",
        "src/venv/lib.py",
        "tests/test_core.py",
        "tests/helpers.py",
        ".pytestmut/runs/x.py",
    ]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    found = discover_targets(
        tmp_path,
        ("src", "tests"),
        ("**/migrations/**",),
        ("test_*.py", "*_test.py"),
        ("tests",),
        tmp_path / ".pytestmut",
    )
    assert [target.relpath for target in found.targets] == [
        "src/pkg/__init__.py",
        "src/pkg/core.py",
    ]
    assert dict(found.excluded) == {
        "src/pkg/conftest.py": "test file",
        "src/pkg/core_test.py": "test file",
        "src/pkg/migrations/0001.py": "matches exclude",
        "tests/helpers.py": "inside testpaths",
        "tests/test_core.py": "test file",
    }
    globbed = discover_targets(
        tmp_path, ("src/pkg/c*.py",), (), ("test_*.py",), (), tmp_path / ".pytestmut"
    )
    assert [target.relpath for target in globbed.targets] == [
        "src/pkg/core.py",
        "src/pkg/core_test.py",
    ]
    single = discover_targets(
        tmp_path, ("src/pkg/core.py",), (), ("test_*.py",), (), tmp_path / ".pytestmut"
    )
    assert [target.relpath for target in single.targets] == ["src/pkg/core.py"]
    assert single.targets[0].path == (tmp_path / "src/pkg/core.py").resolve()
    with pytest.raises(ConfigError):
        discover_targets(tmp_path, ("missing",), (), (), (), tmp_path / ".pytestmut")
