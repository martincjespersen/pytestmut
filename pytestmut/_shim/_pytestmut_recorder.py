"""pytest plugin loaded with ``-p`` in every test process pytestmut starts.

It writes the structured record that verdicts are derived from, narrows the
session to the tests selected for the job, stops at the first failure, and
refuses to run under options that would make the result meaningless.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from typing import Any

import _pytestmut_boot as boot
import pytest

# Option destinations that must be falsy, with the flag users know them by.
_UNSAFE_OPTIONS = {
    "lf": "--lf",
    "failedfirst": "--ff",
    "newfirst": "--nf",
    "stepwise": "--sw",
    "usepdb": "--pdb",
    "trace": "--trace",
    "collectonly": "--collect-only",
    "setuponly": "--setup-only",
    "setupplan": "--setup-plan",
    "showfixtures": "--fixtures",
    "show_fixtures_per_test": "--fixtures-per-test",
    "numprocesses": "-n",
    "looponfail": "--looponfail",
    "update_snapshots": "--snapshot-update",
    "force_regen": "--force-regen",
    "regen_all": "--regen-all",
    "xmlpath": "--junitxml",
    "testmon": "--testmon",
}
_MESSAGE_LIMIT = 600
_session: Any = None
_tracer: Any = None
_fixtures_by_test: dict[str, list[str]] = {}


def _state() -> Any:
    return boot.state() or boot.install()


def _record(event: dict[str, Any]) -> None:
    current = _state()
    if current is not None:
        current.record(event)


def _job() -> dict[str, Any]:
    current = _state()
    return current.job if current is not None else {}


def _unsafe_options(config: Any) -> list[str]:
    option = config.option
    found = [
        flag for dest, flag in _UNSAFE_OPTIONS.items() if getattr(option, dest, None)
    ]
    if getattr(option, "cov_source", None) and not getattr(option, "no_cov", False):
        found.append("--cov")
    return found


@pytest.hookimpl(trylast=True)
def pytest_configure(config: Any) -> None:
    """Record the session start and refuse unsafe options."""
    global _tracer
    current = _state()
    if current is None:
        return
    unsafe = _unsafe_options(config)
    _record(
        {
            "ev": "configure",
            "pytest": pytest.__version__,
            "rootdir": str(config.rootpath),
            "unsafe": unsafe,
        }
    )
    if unsafe:
        pytest.exit(
            "pytestmut: refusing to run with " + ", ".join(unsafe), returncode=3
        )
    _settle_hypothesis()
    _tracer = getattr(current, "tracer", None)


def _settle_hypothesis() -> None:
    """Make Hypothesis deterministic and keep it away from its database."""
    if "hypothesis" not in sys.modules:
        return
    try:
        from hypothesis import settings

        settings.register_profile(
            "pytestmut",
            parent=settings.default,
            derandomize=True,
            database=None,
            deadline=None,
        )
        settings.load_profile("pytestmut")
    except Exception:  # noqa: BLE001 - optional hardening only
        pass


def pytest_sessionstart(session: Any) -> None:
    """Keep the session so a failure can stop it."""
    global _session
    _session = session


def _wanted() -> set[str] | None:
    nodeids = _job().get("nodeids")
    return None if nodeids is None else set(nodeids)


def pytest_ignore_collect(collection_path: Any, config: Any) -> bool | None:
    """Skip test files that hold none of the selected tests."""
    job = _job()
    files = job.get("prune_files")
    if not files:
        return None
    if collection_path.suffix != ".py" or not collection_path.is_file():
        return None
    if collection_path.name in {"conftest.py", "__init__.py"}:
        return None
    try:
        relative = collection_path.relative_to(config.rootpath).as_posix()
    except ValueError:
        return None
    return None if relative in files else True


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    """Narrow the session to the tests selected for this job."""
    wanted = _wanted()
    if wanted is None:
        _record({"ev": "collected", "ids": [item.nodeid for item in items]})
        return
    keep = [item for item in items if item.nodeid in wanted]
    drop = [item for item in items if item.nodeid not in wanted]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
    order = _job().get("order")
    if order:
        rank = {nodeid: index for index, nodeid in enumerate(order)}
        items.sort(key=lambda item: rank.get(item.nodeid, len(rank)))
    missing = sorted(wanted - {item.nodeid for item in keep})
    _record(
        {
            "ev": "collected",
            "count": len(keep),
            "missing": missing[:20],
            "missing_count": len(missing),
        }
    )


def pytest_collectreport(report: Any) -> None:
    """Record collection errors, which are import failures more often than not."""
    if report.failed:
        _record(
            {
                "ev": "collect_error",
                "id": report.nodeid,
                "msg": str(report.longrepr)[-_MESSAGE_LIMIT:],
            }
        )


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    """Record that a test began, so a hard crash can be attributed to it."""
    current = _state()
    if current is not None:
        current.reseed_linecache()
    _record({"ev": "start", "id": nodeid})


def _failure(report: Any) -> tuple[str, str]:
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    message = getattr(crash, "message", "") or str(report.longrepr)
    name = message.split(":", 1)[0].split("(", 1)[0].strip()
    return name[:80], message[:_MESSAGE_LIMIT]


def pytest_runtest_logreport(report: Any) -> None:
    """Record one phase of one test and stop the session on a failure."""
    event: dict[str, Any] = {
        "ev": "test",
        "id": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "dur": round(float(getattr(report, "duration", 0.0)), 6),
    }
    if hasattr(report, "wasxfail"):
        event["xfail"] = True
    if report.failed:
        event["exc"], event["msg"] = _failure(report)
    _record(event)
    if report.failed and _job().get("stop_on_fail") and _session is not None:
        _session.shouldfail = "pytestmut: stopping at the first failure"


def pytest_internalerror(excrepr: Any) -> None:
    """Record an internal pytest error."""
    _record({"ev": "internal_error", "msg": str(excrepr)[-_MESSAGE_LIMIT:]})


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: Any, nextitem: Any) -> Generator[None, Any, Any]:
    """Attribute the lines a test executes to that test."""
    if _tracer is None:
        return (yield)
    _fixtures_by_test[item.nodeid] = list(getattr(item, "fixturenames", ()))
    _tracer.switch("t:" + item.nodeid)
    try:
        return (yield)
    finally:
        _tracer.switch("")


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef: Any, request: Any) -> Generator[None, Any, Any]:
    """Attribute the setup of shared fixtures to the fixture, not one test."""
    if _tracer is None or fixturedef.scope == "function":
        return (yield)
    previous = _tracer.context
    _tracer.switch("f:" + fixturedef.argname)
    try:
        return (yield)
    finally:
        _tracer.switch(previous)


def _modules_seen(current: Any) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    seen: list[dict[str, str]] = []
    shadows: list[dict[str, str]] = []
    expected = {
        target["module"]: target
        for target in current.job.get("targets", ())
        if target.get("module")
    }
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not isinstance(filename, str) or not filename.endswith(".py"):
            continue
        entry = current.match(filename)
        if entry is not None:
            loader = type(getattr(module, "__loader__", None)).__name__
            seen.append({"path": entry["path"], "name": name, "loader": loader})
        elif name in expected:
            shadows.append(
                {"name": name, "file": filename, "target": expected[name]["path"]}
            )
    return seen, shadows


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    """Record how the session ended and, for baselines, what was imported."""
    current = _state()
    if current is None:
        return
    if current.mode in {"observe", "trace"}:
        seen, shadows = _modules_seen(current)
        _record({"ev": "modules", "seen": seen, "shadows": shadows})
    if _tracer is not None:
        payload = {
            "mechanism": _tracer.mechanism,
            "contexts": _tracer.stop(),
            "fixtures": _fixtures_by_test,
            "clobbered": _tracer.clobbered,
        }
        path = os.path.join(current.job["record_dir"], "trace.json")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle)
    _record(
        {
            "ev": "finish",
            "exitstatus": int(exitstatus),
            "collected": int(getattr(session, "testscollected", 0)),
            "failed": int(getattr(session, "testsfailed", 0)),
        }
    )
