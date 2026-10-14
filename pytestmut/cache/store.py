"""The journal of reusable verdicts.

Reuse rules, chosen so that a stale cache can cost time and never correctness:

- A cached *kill* is never trusted as is. The test that killed the mutant last
  time runs first, alone; if it still fails the mutant is killed again for the
  price of one test, and otherwise the mutant goes through the full process.
- A cached *survivor* or *timeout* is reused only when nothing it depends on
  has changed: the mutated file, the files of the tests selected for it, their
  ``conftest.py`` chain, the target files those tests execute and the helper
  modules next to the tests.
- Errors are never cached.

Entries of the last few configurations are kept side by side, so a run
narrowed to a few tests (as a coding agent would make) and a full run do not
evict each other. A kill recorded under any of them still serves as the
first test to try.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from pytestmut.baseline.runner import Baseline
from pytestmut.baseline.testmap import Selection, Selector
from pytestmut.cache.fingerprint import FileHashes, environment_fingerprint
from pytestmut.config.model import Config
from pytestmut.exec.jobs import TargetIdentity
from pytestmut.mutation.model import Mutant, MutantResult, Status
from pytestmut.pytest_integration.probe import PytestEnvironment
from pytestmut.source.discovery import Discovery
from pytestmut.state.statedir import StateDir

_DEPENDENT = frozenset({Status.SURVIVED, Status.TIMEOUT, Status.OOM})
_CACHED = _DEPENDENT | {Status.KILLED}
# Configurations whose entries are kept, most recently written first.
KEPT_ENVIRONMENTS = 4


@dataclass(frozen=True)
class _Entry:
    status: str
    reason: str
    killing_test: str | None
    kill_phase: str | None
    tests_selected: int
    environment: str
    dependencies: str
    written: float = 0.0


class VerdictCache:
    """Reads and writes cached verdicts for one run.

    Arguments:
        path: Journal file, or None to disable the cache.
        environment: Environment fingerprint of this run.
        root: pytest's rootdir, which test node ids are relative to.
        targets: Digest of each target file by index.
        target_index: Index of each target file by relative path.
        support: Digest of helper modules next to the tests.
        hashes: File digest cache.
    """

    def __init__(
        self,
        path: Path | None,
        environment: str,
        root: Path,
        targets: dict[int, str],
        target_index: dict[str, int],
        support: str,
        hashes: FileHashes,
    ) -> None:
        self._path = path
        self._environment = environment
        self._root = root
        self._targets = targets
        self._target_index = target_index
        self._support = support
        self._hashes = hashes
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._hints: dict[str, str] = {}
        if path is not None and path.is_file():
            self._load(path)

    @classmethod
    def open(
        cls,
        state: StateDir,
        config: Config,
        probe: PytestEnvironment,
        discovery: Discovery,
        identities: tuple[TargetIdentity, ...],
        baseline: Baseline,
    ) -> VerdictCache:
        """Open the cache for a run.

        Arguments:
            state: The state directory.
            config: The run configuration.
            probe: The project's pytest setup.
            discovery: The files being mutated.
            identities: The target files as the run indexes them.
            baseline: The validated baseline.

        Returns:
            The cache; a disabled one when caching is off.
        """
        hashes = FileHashes()
        targets = {target.index: target.sha256 for target in identities}
        target_index = {target.relpath: target.index for target in identities}
        if not config.cache.enabled:
            return cls(None, "", probe.rootdir, targets, target_index, "", hashes)
        support = _support_digest(probe, discovery, baseline, config.root, hashes)
        return cls(
            state.cache_file,
            environment_fingerprint(config, probe, hashes),
            probe.rootdir,
            targets,
            target_index,
            support,
            hashes,
        )

    @property
    def hints(self) -> dict[str, str]:
        """Test that killed each mutant last time, by mutant id."""
        return self._hints

    def _load(self, path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                data = json.loads(line)
                self._entries[(data["id"], data["environment"])] = _Entry(
                    status=data["status"],
                    reason=data.get("reason", ""),
                    killing_test=data.get("killing_test"),
                    kill_phase=data.get("kill_phase"),
                    tests_selected=int(data.get("tests_selected", 0)),
                    environment=data["environment"],
                    dependencies=data["dependencies"],
                    written=float(data.get("written", 0.0)),
                )
            except (ValueError, KeyError, TypeError):
                continue

    def _dependencies(
        self, mutant: Mutant, selection: Selection, selector: Selector
    ) -> str:
        digest = hashlib.sha256()

        def add(text: str) -> None:
            digest.update(text.encode("utf-8", "replace"))
            digest.update(b"\0")

        add(self._environment)
        add(self._support)
        own = self._target_index.get(mutant.path, -1)
        touched = selector.files_touched(selection.nodeids)
        files = set(self._targets) if touched is None or selection.full else touched
        for index in sorted(files | {own}):
            add(f"{index}:{self._targets.get(index, '')}")
        test_files = sorted({nodeid.split("::", 1)[0] for nodeid in selection.nodeids})
        conftests: set[Path] = set()
        for name in test_files:
            path = self._root / name
            add(name + self._hashes.of(path))
            directory = path.parent
            while directory == self._root or self._root in directory.parents:
                conftests.add(directory / "conftest.py")
                if directory == self._root:
                    break
                directory = directory.parent
        for conftest in sorted(conftests):
            add(str(conftest) + self._hashes.of(conftest))
        add("\n".join(selection.nodeids))
        return digest.hexdigest()

    def split(
        self, mutants: list[Mutant], selector: Selector
    ) -> tuple[dict[str, MutantResult], list[Mutant]]:
        """Separate mutants with a reusable verdict from those that must run.

        Arguments:
            mutants: Runnable mutants of this run.
            selector: Chooses the tests of each mutant.

        Returns:
            Reused verdicts by mutant id, and the mutants still to run.
        """
        reused: dict[str, MutantResult] = {}
        pending: list[Mutant] = []
        killers: dict[str, str] = {}
        for (identifier, _), entry in sorted(
            self._entries.items(), key=lambda item: item[1].written
        ):
            if entry.status == Status.KILLED.value and entry.killing_test:
                killers[identifier] = entry.killing_test
        for mutant in mutants:
            entry = self._entries.get((mutant.id, self._environment))
            if mutant.id in killers:
                # Any earlier kill is worth trying first; it is re-run anyway.
                self._hints[mutant.id] = killers[mutant.id]
            if entry is None:
                pending.append(mutant)
                continue
            status = Status(entry.status)
            if status is Status.KILLED:
                pending.append(mutant)
                continue
            selection = selector.select(mutant)
            if (
                selection.status is None
                and status in _DEPENDENT
                and entry.dependencies
                == self._dependencies(mutant, selection, selector)
            ):
                reused[mutant.id] = MutantResult(
                    mutant_id=mutant.id,
                    status=status,
                    reason=entry.reason,
                    tests_selected=entry.tests_selected,
                    from_cache=True,
                )
            else:
                pending.append(mutant)
        return reused, pending

    def record(self, mutant: Mutant, result: MutantResult, selector: Selector) -> None:
        """Remember a fresh verdict.

        Arguments:
            mutant: The mutant.
            result: Its final verdict.
            selector: Chooses the tests of each mutant.
        """
        if self._path is None or result.from_cache or result.status not in _CACHED:
            return
        selection = selector.select(mutant)
        if selection.status is not None:
            return
        self._entries[(mutant.id, self._environment)] = _Entry(
            status=result.status.value,
            reason=result.reason,
            killing_test=result.killing_test,
            kill_phase=result.kill_phase,
            tests_selected=result.tests_selected,
            environment=self._environment,
            dependencies=self._dependencies(mutant, selection, selector),
            written=time.time(),
        )

    def close(self) -> None:
        """Write the journal back, keeping only recent configurations."""
        if self._path is None:
            return
        latest: dict[str, float] = {self._environment: time.time()}
        for entry in self._entries.values():
            latest[entry.environment] = max(
                latest.get(entry.environment, 0.0), entry.written
            )
        kept = set(sorted(latest, key=latest.__getitem__)[-KEPT_ENVIRONMENTS:])
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f".{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for (identifier, _), entry in sorted(self._entries.items()):
                if entry.environment not in kept:
                    continue
                handle.write(
                    json.dumps(
                        {
                            "id": identifier,
                            "status": entry.status,
                            "reason": entry.reason,
                            "killing_test": entry.killing_test,
                            "kill_phase": entry.kill_phase,
                            "tests_selected": entry.tests_selected,
                            "environment": entry.environment,
                            "dependencies": entry.dependencies,
                            "written": entry.written,
                        }
                    )
                    + "\n"
                )
        os.replace(temporary, self._path)


def _support_digest(
    probe: PytestEnvironment,
    discovery: Discovery,
    baseline: Baseline,
    project_root: Path,
    hashes: FileHashes,
) -> str:
    """Digest of code the tests rely on that is neither a target nor a test.

    Covers helper modules in the test directories and source files excluded
    from mutation; a change to either can change any verdict.
    """
    digest = hashlib.sha256()
    targets = {target.path for target in discovery.targets}
    directories = {
        (probe.rootdir / nodeid.split("::", 1)[0]).parent for nodeid in baseline.nodeids
    }
    top_level = {
        directory
        for directory in directories
        if not any(other in directory.parents for other in directories)
    }
    helpers: set[Path] = set()
    for directory in top_level:
        if directory == probe.rootdir or not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            is_test = path.name == "conftest.py" or any(
                fnmatch.fnmatchcase(path.name, pattern)
                for pattern in probe.python_files
            )
            if not is_test and path.resolve() not in targets:
                helpers.add(path)
    for relpath, reason in discovery.excluded:
        if reason == "matches exclude":
            helpers.add(project_root / relpath)
    for path in sorted(helpers):
        digest.update(str(path).encode("utf-8", "replace"))
        digest.update(hashes.of(path).encode())
    return digest.hexdigest()
