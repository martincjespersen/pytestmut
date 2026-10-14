"""The activation shim, exercised in real interpreters."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pytestmut import _meta
from pytestmut.baseline.testmap import TestMap
from pytestmut.exec.environment import SHIM_DIR
from pytestmut.exec.jobs import (
    MUTATE,
    OBSERVE,
    Job,
    identify,
    read_record,
    write_job,
)
from pytestmut.mutation.model import Mutant, SourceSpan, Splice

MODULE = "LIMIT = 10\n\n\ndef add(a, b):\n    return a + b\n"


def _setup(
    tmp_path: Path, mutate: bool = True, source: str = MODULE
) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    target = project / "calc.py"
    target.write_bytes(source.encode("utf-8"))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    identity = identify(0, "calc.py", target.resolve(), digest)
    job_dir = tmp_path / "job"
    (job_dir / "rec").mkdir(parents=True, exist_ok=True)
    mutant = None
    if mutate:
        start = source.index("a + b") + 2
        mutant = Mutant(
            id="m1",
            path="calc.py",
            scope="add",
            operator="arith.add_sub",
            span=SourceSpan(5, 13, 5, 14),
            splice=Splice(start, start + 1, b"-"),
            original="+",
            replacement="-",
            static=False,
            policy="default",
            cover_lines=(5, 5),
        )
    job = Job(id="j", mode=MUTATE if mutate else OBSERVE, mutant=mutant)
    write_job(job_dir, job, (identity,), "run", own_group=False)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST", "PYTHONPATH", "PYTHONPYCACHEPREFIX"))
    }
    env["PYTHONPATH"] = str(SHIM_DIR)
    env["PYTESTMUT_JOB"] = str(job_dir / "job.json")
    return project, env


def _python(project: Path, env: dict[str, str], code: str, *flags: str) -> str:
    completed = subprocess.run(
        [sys.executable, *flags, "-c", code],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_mutant_is_served_from_memory(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "2"
    record = read_record(tmp_path / "job" / "rec")
    assert [load["mutated"] for load in record.loads] == [True]
    assert record.loads[0]["module"] == "calc" and record.root_seen
    assert record.activation_time is not None
    # The file on disk is untouched and no bytecode was written next to it.
    assert (project / "calc.py").read_text() == MODULE
    assert not (project / "__pycache__").exists()


def test_existing_bytecode_does_not_hide_the_mutant(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    plain = {k: v for k, v in env.items() if k not in {"PYTHONPATH", "PYTESTMUT_JOB"}}
    assert _python(project, plain, "import calc; print(calc.add(5, 3))") == "8"
    cached = list((project / "__pycache__").glob("calc.*.pyc"))
    assert len(cached) == 1
    before = cached[0].read_bytes()
    # A valid .pyc for the original source sits right there; the mutant must
    # still be what runs, and the cache must not be overwritten with it.
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "2"
    assert cached[0].read_bytes() == before
    assert _python(project, plain, "import calc; print(calc.add(5, 3))") == "8"


def test_module_identity_and_source_introspection(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    code = (
        "import calc, inspect, os\n"
        "print(os.path.samefile(calc.__file__, 'calc.py'))\n"
        "print(calc.__spec__.origin == calc.__file__)\n"
        "print(calc.add.__code__.co_firstlineno)\n"
        "print(inspect.getsource(calc.add).splitlines()[1].strip())\n"
    )
    assert _python(project, env, code).splitlines() == [
        "True",
        "True",
        "4",
        "return a - b",
    ]


def test_other_ways_of_loading_the_file(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    code = (
        "import importlib.util, runpy\n"
        "spec = importlib.util.spec_from_file_location('other_name', 'calc.py')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(module.add(5, 3))\n"
        "print(runpy.run_module('calc')['add'](5, 3))\n"
    )
    # Matching is by file identity, so the module name does not matter.
    assert _python(project, env, code).splitlines() == ["2", "2"]


def test_child_interpreters_inherit_the_mutant(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    code = (
        "import subprocess, sys\n"
        "code = 'import calc; print(calc.add(5, 3))'\n"
        "out = subprocess.run([sys.executable, '-c', code],"
        " capture_output=True, text=True).stdout\n"
        "print(out.strip())\n"
    )
    assert _python(project, env, code) == "2"
    record = read_record(tmp_path / "job" / "rec")
    assert len(record.loads) == 1 and record.loads[0]["root"] is False


def test_observe_mode_serves_the_original(tmp_path: Path) -> None:
    project, env = _setup(tmp_path, mutate=False)
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "8"
    record = read_record(tmp_path / "job" / "rec")
    assert [load["mutated"] for load in record.loads] == [False]
    assert record.activation_time is None


def test_changed_source_is_refused(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    (project / "calc.py").write_text(MODULE + "\nEXTRA = 1\n", encoding="utf-8")
    # The mutant was built from other bytes; applying it now would corrupt
    # the file, so the original loads and the problem is recorded.
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "8"
    record = read_record(tmp_path / "job" / "rec")
    assert not record.loads
    assert any("source changed" in error for error in record.boot_errors)


def test_other_interpreter_versions_are_left_alone(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    job_file = Path(env["PYTESTMUT_JOB"])
    job = json.loads(job_file.read_text())
    job["cache_tag"] = "cpython-27"
    job_file.write_text(json.dumps(job))
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "8"
    job["cache_tag"], job["wire"] = sys.implementation.cache_tag, 999
    job_file.write_text(json.dumps(job))
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "8"


def test_shim_is_inert_without_a_job_and_never_raises(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    env["PYTESTMUT_JOB"] = str(tmp_path / "missing.json")
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "8"
    del env["PYTESTMUT_JOB"]
    assert _python(project, env, "import calc; print(calc.add(5, 3))") == "8"
    assert _python(project, env, "import sitecustomize; print('ok')") == "ok"


def test_shadowed_sitecustomize_still_runs(tmp_path: Path) -> None:
    project, env = _setup(tmp_path)
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import builtins\nbuiltins.CHAINED = True\n", encoding="utf-8"
    )
    env["PYTHONPATH"] = str(SHIM_DIR) + os.pathsep + str(site)
    code = "import builtins, calc; print(builtins.CHAINED, calc.add(5, 3))"
    assert _python(project, env, code) == "True 2"


def test_sitecustomize_parses_as_old_python() -> None:
    source = (SHIM_DIR / "sitecustomize.py").read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 4))
    for node in ast.walk(tree):
        # No annotations, f-strings or walrus: anything inheriting PYTHONPATH
        # has to be able to import this file.
        assert not isinstance(node, (ast.AnnAssign, ast.JoinedStr, ast.NamedExpr))
        if isinstance(node, ast.FunctionDef):
            assert node.returns is None
            assert all(arg.annotation is None for arg in node.args.args)
    for name in ("_pytestmut_boot.py", "_pytestmut_tracer.py"):
        text = (SHIM_DIR / name).read_text(encoding="utf-8")
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.ImportFrom)
        }
        # The shim must stay importable before anything else, so no
        # third-party imports and never the package itself.
        assert "pytestmut" not in imported and "pytest" not in imported


def test_wire_contract_between_package_and_shim() -> None:
    boot = (SHIM_DIR / "_pytestmut_boot.py").read_text(encoding="utf-8")
    probe = (SHIM_DIR / "_pytestmut_probe.py").read_text(encoding="utf-8")
    assert f"WIRE_VERSION = {_meta.WIRE_VERSION}\n" in boot
    assert f'ENV_JOB = "{_meta.ENV_JOB}"' in boot
    assert f'ENV_PROBE_OUT = "{_meta.ENV_PROBE_OUT}"' in probe
    assert (SHIM_DIR / f"{_meta.RECORDER_PLUGIN}.py").is_file()
    assert (SHIM_DIR / f"{_meta.PROBE_PLUGIN}.py").is_file()
    assert not (SHIM_DIR / "__init__.py").exists()


TRACED = """\
import threading


def helper(value):
    return value + 1


def uses_thread(value):
    result = []
    worker = threading.Thread(target=lambda: result.append(helper(value)))
    worker.start()
    worker.join()
    return result[0]


def generator(count):
    for index in range(count):
        yield index
"""


def _trace(tmp_path: Path, script: str) -> dict[str, Any]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "traced.py").write_text(TRACED, encoding="utf-8")
    out = tmp_path / "trace.json"
    code = (
        "import json, os, sys\n"
        "from _pytestmut_tracer import Tracer\n"
        "target = os.path.realpath('traced.py')\n"
        "same = lambda name: os.path.realpath(name) == target\n"
        "tracer = Tracer(lambda name: 0 if same(name) else None)\n"
        "tracer.start()\n"
        "import traced\n" + script + "\n"
        "data = {'mechanism': tracer.mechanism, 'contexts': tracer.stop()}\n"
        f"json.dump(data, open({str(out)!r}, 'w'))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env["PYTHONPATH"] = str(SHIM_DIR)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(out.read_text())  # type: ignore[no-any-return]


def test_tracer_attributes_lines_to_each_context(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path,
        "tracer.switch('t:one')\n"
        "traced.helper(1)\n"
        "tracer.switch('t:two')\n"
        "traced.helper(2)\n"
        "list(traced.generator(2))\n"
        "tracer.switch('t:three')\n"
        "traced.uses_thread(3)\n"
        "tracer.switch('')\n",
    )
    contexts = trace["contexts"]
    expected = "monitoring" if sys.version_info >= (3, 12) else "settrace"
    assert trace["mechanism"] == expected
    # Import-time lines belong to no test.
    assert {1, 4, 8, 16} <= set(contexts[""]["0"])
    assert 5 not in contexts[""]["0"]
    # The same line is seen again in the second context, not only the first.
    assert 5 in contexts["t:one"]["0"] and 5 in contexts["t:two"]["0"]
    assert {17, 18} <= set(contexts["t:two"]["0"])
    assert 17 not in contexts["t:one"]["0"]
    # Lines run on another thread still belong to the test that started it.
    assert {5, 9, 10, 13} <= set(contexts["t:three"]["0"])


def test_test_map_selection_inputs() -> None:
    trace = {
        "mechanism": "monitoring",
        "contexts": {
            "": {"0": [1, 2]},
            "t:a": {"0": [5, 6]},
            "t:b": {"0": [6], "1": [3]},
            "f:database": {"1": [10, 11]},
        },
        "fixtures": {"a": ["tmp_path"], "b": ["database"], "c": ["database"]},
        "clobbered": ["t:d", ""],
    }
    test_map = TestMap.from_trace(trace, {1: {"e"}})
    assert test_map.tests_for(0, 5, 5) == {"a"}
    assert test_map.tests_for(0, 5, 6) == {"a", "b"}
    # A shared fixture's setup lines count for every test that uses it.
    assert test_map.tests_for(1, 10, 10) == {"b", "c"}
    assert test_map.tests_for(0, 99, 99) == set()
    assert test_map.is_static(0, 2, 3) and not test_map.is_static(0, 5, 6)
    assert test_map.always == {"d"}
    assert test_map.by_file == {1: {"e"}}
    assert test_map.files_by_test["b"] == {0, 1}
    assert test_map.files_by_test["e"] == {1}


def test_tracer_notices_a_replaced_trace_function(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path,
        "import sys\n"
        "tracer.switch('t:quiet')\n"
        "traced.helper(1)\n"
        "tracer.switch('t:rude')\n"
        "if tracer.mechanism == 'settrace':\n"
        "    sys.settrace(None)\n"
        "traced.helper(2)\n"
        "tracer.switch('t:after')\n"
        "traced.helper(3)\n"
        "tracer.switch('')\n"
        "print(tracer.clobbered)\n"
        "expected = ['t:rude'] if tracer.mechanism == 'settrace' else []\n"
        "assert tracer.clobbered == expected, tracer.clobbered\n",
    )
    contexts = trace["contexts"]
    # An undisturbed test is never reported, and tracing resumes afterwards.
    assert 5 in contexts["t:quiet"]["0"] and 5 in contexts["t:after"]["0"]
