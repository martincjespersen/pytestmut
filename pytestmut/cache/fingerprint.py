"""Fingerprints that decide whether a cached verdict may be reused.

A cached verdict is only as good as the list of things it is checked against.
The environment fingerprint covers everything that could change every verdict
at once: the tool, the interpreter, the platform, installed packages and the
pytest configuration. Per-mutant dependencies are added by the store.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import sys
from pathlib import Path

from pytestmut._meta import VERDICT_SEMANTICS_VERSION, WIRE_VERSION, __version__
from pytestmut.config.model import Config
from pytestmut.pytest_integration.probe import PytestEnvironment
from pytestmut.source.discovery import path_matches


class FileHashes:
    """Content digests of files, each read once."""

    def __init__(self) -> None:
        self._known: dict[Path, str] = {}

    def of(self, path: Path) -> str:
        """Digest of a file, or ``missing`` when it cannot be read."""
        if path not in self._known:
            try:
                self._known[path] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                self._known[path] = "missing"
        return self._known[path]


def _installed() -> list[str]:
    found = set()
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"] if distribution.metadata else None
        if name:
            found.add(f"{name.lower()}=={distribution.version}")
    return sorted(found)


def environment_fingerprint(
    config: Config, probe: PytestEnvironment, hashes: FileHashes
) -> str:
    """Digest of everything that could change every verdict at once.

    Arguments:
        config: The run configuration.
        probe: The project's pytest setup.
        hashes: File digest cache.

    Returns:
        A hex digest.
    """
    parts: list[str] = [
        __version__,
        str(VERDICT_SEMANTICS_VERSION),
        str(WIRE_VERSION),
        sys.version,
        sys.implementation.cache_tag or "",
        str(sys.flags.optimize),
        platform.system(),
        platform.machine(),
        probe.pytest_version,
        repr(sorted(probe.plugins.items())),
        repr(probe.ini_addopts),
        repr(probe.env_addopts),
        repr(config.pytest),
        repr(config.timeout),
        repr((config.safety.env, config.safety.env_drop, config.safety.env_keep)),
        repr(config.safety.env_set),
        repr(config.run.quarantine),
    ]
    parts += _installed()
    if probe.inipath:
        parts.append(hashes.of(Path(probe.inipath)))
    if config.cache.extra_inputs:
        for path in sorted(config.root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(config.root).as_posix()
            if path_matches(relative, config.cache.extra_inputs):
                parts.append(relative + hashes.of(path))
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()
