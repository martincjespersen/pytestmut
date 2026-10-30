"""Process supervision, the state directory, environments and command lines."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from pytestmut._meta import SENTINEL_NAME
from pytestmut.config.model import PytestConfig, SafetyConfig
from pytestmut.exceptions import StateDirError
from pytestmut.exec import supervisor
from pytestmut.exec.cpu import (
    GIB,
    MIB,
    children_peak_rss,
    cpu_workers,
    effective_cpus,
    format_size,
    memory_budget,
    plan_memory,
    total_memory,
)
from pytestmut.exec.environment import SHIM_DIR, build_environment
from pytestmut.exec.supervisor import LocalSupervisor, ProcessSpec
from pytestmut.pytest_integration.cmdline import build_argv, filter_inherited
from pytestmut.pytest_integration.probe import PytestEnvironment
from pytestmut.state.statedir import RunDir, StateDir, process_alive


def _spec(
    code: str, tmp_path: Path, timeout: float = 30.0, **extra: object
) -> ProcessSpec:
    return ProcessSpec(
        argv=(sys.executable, "-c", code),
        env=dict(os.environ),
        cwd=tmp_path,
        timeout_s=timeout,
        **extra,  # type: ignore[arg-type]
    )


def _wait_dead(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_normal_exit_and_output(tmp_path: Path) -> None:
    outcome = LocalSupervisor().run(
        _spec(
            "import sys; print('hello'); sys.stderr.write('oops'); sys.exit(3)",
            tmp_path,
        ),
        threading.Event(),
    )
    assert outcome.returncode == 3
    assert not outcome.timed_out and not outcome.oom and not outcome.cancelled
    assert "hello" in outcome.output and "oops" in outcome.output


def test_hung_process_is_killed_at_the_budget(tmp_path: Path) -> None:
    started = time.monotonic()
    outcome = LocalSupervisor().run(
        _spec("import time; time.sleep(60)", tmp_path, timeout=1.0), threading.Event()
    )
    assert outcome.timed_out
    assert time.monotonic() - started < 15


def test_grandchildren_die_after_a_timeout(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = (
        "import subprocess, sys, time\n"
        "sleeper = 'import time; time.sleep(120)'\n"
        "child = subprocess.Popen([sys.executable, '-c', sleeper])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    outcome = LocalSupervisor().run(
        _spec(code, tmp_path, timeout=2.0), threading.Event()
    )
    assert outcome.timed_out
    assert _wait_dead(int(pid_file.read_text()))


def test_grandchildren_die_after_a_normal_exit(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    code = (
        "import subprocess, sys\n"
        "sleeper = 'import time; time.sleep(120)'\n"
        "child = subprocess.Popen([sys.executable, '-c', sleeper])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
    )
    outcome = LocalSupervisor().run(_spec(code, tmp_path), threading.Event())
    assert outcome.returncode == 0 and not outcome.timed_out
    # A clean exit left a worker behind; the sweep must still remove it.
    assert _wait_dead(int(pid_file.read_text()))


def test_output_flood_is_capped(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "sys.stdout.write('HEAD\\n"
        "')\n"
        "for _ in range(4000):\n"
        "    sys.stdout.write('x' * 1000 + '\\n"
        "')\n"
        "sys.stdout.write('TAIL\\n"
        "')\n"
    )
    outcome = LocalSupervisor().run(
        _spec(code, tmp_path, output_cap=20000), threading.Event()
    )
    assert outcome.returncode == 0
    assert len(outcome.output) < 30000
    assert outcome.output.startswith("HEAD") and "TAIL" in outcome.output
    assert "bytes of output dropped" in outcome.output


def test_memory_growth_is_stopped(tmp_path: Path) -> None:
    # The child stops growing by itself at 512 MiB, so even a watchdog that
    # falls behind on a loaded machine cannot let it take the machine down.
    code = (
        "import time\n"
        "blocks = []\n"
        "for _ in range(128):\n"
        "    blocks.append(bytearray(4 * 1024 * 1024))\n"
        "    time.sleep(0.02)\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    limit = 64 * 1024 * 1024
    outcome = LocalSupervisor().run(
        _spec(code, tmp_path, timeout=60.0, memory_limit=limit), threading.Event()
    )
    assert outcome.oom and not outcome.timed_out
    assert outcome.peak_rss is not None and outcome.peak_rss > limit
    # Stopped for memory long before the time budget would have ended it.
    assert time.monotonic() - started < 30


def test_peak_recorded_by_the_platform_counts_after_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows job limits fail allocations instead of killing, often before the
    # next poll; the job's recorded peak is what shows the limit was crossed.
    limit = 64 * 1024 * 1024
    real_spawn = supervisor._spawn

    def spawn(spec: ProcessSpec) -> object:
        handle = real_spawn(spec)
        monkeypatch.setattr(handle, "peak_after_exit", lambda: limit + 1)
        return handle

    monkeypatch.setattr(supervisor, "_spawn", spawn)
    quick = _spec("pass", tmp_path, memory_limit=limit)
    outcome = LocalSupervisor().run(quick, threading.Event())
    assert outcome.oom and outcome.peak_rss == limit + 1
    unlimited = LocalSupervisor().run(_spec("pass", tmp_path), threading.Event())
    assert not unlimited.oom


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no zombies")
def test_a_zombie_counts_as_dead() -> None:
    # What a killed orphan looks like in a container with no init process to
    # reap it: os.kill(pid, 0) still succeeds, but the process is gone.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert process_alive(child.pid)
        child.kill()  # SIGKILL, and not reaped: a zombie
        assert _wait_dead(child.pid)
    finally:
        child.kill()
        child.wait()


def test_cancel_stops_a_running_process(tmp_path: Path) -> None:
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    outcome = LocalSupervisor().run(
        _spec("import time; time.sleep(60)", tmp_path), cancel
    )
    assert outcome.cancelled and not outcome.timed_out


def test_unstartable_command_is_reported(tmp_path: Path) -> None:
    spec = ProcessSpec(
        argv=(str(tmp_path / "no-such-program"),),
        env=dict(os.environ),
        cwd=tmp_path,
        timeout_s=5,
    )
    outcome = LocalSupervisor().run(spec, threading.Event())
    assert outcome.returncode is None and "cannot start" in outcome.output


def test_state_directory_lifecycle(tmp_path: Path) -> None:
    state = StateDir(tmp_path, ".pytestmut")
    state.ensure()
    assert (state.path / SENTINEL_NAME).is_file()
    assert (state.path / ".gitignore").read_text() == "*\n"
    state.lock()
    other = StateDir(tmp_path, ".pytestmut")
    (state.path / "lock").write_text(str(os.getppid()), encoding="utf-8")
    with pytest.raises(StateDirError, match="in use"):
        other.lock()
    (state.path / "lock").write_text("999999999", encoding="utf-8")
    other.lock()
    other.unlock()
    assert state.clean() and not state.path.exists()
    assert not state.clean()


def test_scratch_space_is_outside_the_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = StateDir(project, ".pytestmut")
    state.ensure()
    run = state.new_run("")
    try:
        # Tests walking up from their temp directory must not reach the project.
        assert project.resolve() not in run.path.parents
        assert run.path.parent == Path(tempfile.gettempdir()).resolve()
        job = run.job_dir("m1")
        assert (job / "rec").is_dir() and (job / "tmp").is_dir()
        state.remove_job(run, "m1")
        assert not job.exists()
    finally:
        state.remove_scratch(run)
    assert not run.path.exists()


def test_scratch_parent_can_be_configured_and_is_swept(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    parent = tmp_path / "scratch"
    state = StateDir(project, ".pytestmut")
    state.ensure()
    # Left behind by a run whose process no longer exists.
    stale = parent / "pytestmut-deadbeef"
    stale.mkdir(parents=True)
    (stale / SENTINEL_NAME).write_text(
        json.dumps({"tool": "pytestmut", "run": "deadbeef", "pid": 999999999}),
        encoding="utf-8",
    )
    # Looks similar but is not ours: no sentinel.
    foreign = parent / "pytestmut-notes"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("x", encoding="utf-8")
    run = state.new_run(str(parent))
    assert run.path.parent == parent.resolve()
    assert not stale.exists()
    assert (foreign / "keep.txt").exists()
    state.remove_scratch(run)
    assert not run.path.exists()
    # A directory the tool did not create is never removed this way.
    state.remove_scratch(RunDir("x", foreign))
    state.remove_job(RunDir("x", foreign), "keep.txt")
    assert (foreign / "keep.txt").exists()


def test_state_directory_refuses_unsafe_locations(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    foreign = project / "data"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("precious", encoding="utf-8")
    state = StateDir(project, "data")
    with pytest.raises(StateDirError, match="marker"):
        state.ensure()
    with pytest.raises(StateDirError, match="marker"):
        state.clean()
    with pytest.raises(StateDirError, match="marker"):
        state.remove(foreign / "keep.txt")
    assert (foreign / "keep.txt").read_text() == "precious"
    if sys.platform != "win32":
        link = project / "link"
        link.symlink_to(foreign)
        with pytest.raises(StateDirError, match="symlink"):
            StateDir(project, "link").ensure()


def test_state_directory_refuses_the_project_and_its_parents(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    for folder in (project, tmp_path):
        (folder / SENTINEL_NAME).write_text("{}", encoding="utf-8")
    locations = [".", "..", str(tmp_path)]
    if sys.platform != "win32":
        (project / "up").symlink_to(tmp_path, target_is_directory=True)
        locations.append("up/project")
    case_insensitive = (tmp_path / "PROJECT").exists()
    if case_insensitive:
        locations.append("../PROJECT")
    for location in locations:
        state = StateDir(project, location)
        with pytest.raises(StateDirError, match="project root"):
            state.clean()
        with pytest.raises(StateDirError, match="project root"):
            state.ensure()
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 1\n"


def test_state_directory_only_deletes_inside_itself(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = project / "important.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    state = StateDir(project, ".pytestmut")
    state.ensure()
    with pytest.raises(StateDirError, match="outside"):
        state.remove(outside)
    with pytest.raises(StateDirError, match="outside"):
        state.remove(state.path / ".." / "important.py")
    assert outside.exists()
    if sys.platform != "win32":
        # A symlink inside the directory is removed as a link, never followed.
        (state.path / "escape").symlink_to(project)
        state.remove(state.path / "escape")
        assert outside.exists() and not (state.path / "escape").exists()


def test_environment_scrubs_credentials(tmp_path: Path) -> None:
    parent = {
        "PATH": "/bin",
        "HOME": "/home/me",
        "AWS_SECRET_ACCESS_KEY": "s3cret",
        "GITHUB_TOKEN": "t0ken",
        "DATABASE_URL": "postgres://prod",
        "MY_API_KEY": "k",
        "HARMLESS": "1",
        "KEEP_TOKEN": "needed",
        "PYTEST_ADDOPTS": "--lf",
        "COVERAGE_PROCESS_START": "x",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "/existing",
        "OMP_NUM_THREADS": "4",
    }
    safety = SafetyConfig(
        env_keep=("KEEP_TOKEN",), env_drop=("HARM*",), env_set=(("A", "b"),)
    )
    built = build_environment(
        parent, safety, "run1", tmp_path / "pyc", tmp_path / "void"
    )
    env = built.base
    assert built.scrubbed == (
        "AWS_SECRET_ACCESS_KEY",
        "DATABASE_URL",
        "GITHUB_TOKEN",
        "HARMLESS",
        "MY_API_KEY",
    )
    assert "PYTEST_ADDOPTS" not in env and "COVERAGE_PROCESS_START" not in env
    assert "PYTHONDONTWRITEBYTECODE" not in env
    assert env["KEEP_TOKEN"] == "needed" and env["A"] == "b"
    assert env["PYTHONPATH"] == str(SHIM_DIR) + os.pathsep + "/existing"
    assert env["PYTESTMUT_ACTIVE"] == "1" and env["PYTHONHASHSEED"] == "0"
    assert env["OMP_NUM_THREADS"] == "4" and env["MKL_NUM_THREADS"] == "1"
    assert env["AWS_SHARED_CREDENTIALS_FILE"] == str(tmp_path / "void")
    job = built.for_job(tmp_path / "j1", 3)
    assert job["PYTESTMUT_WORKER"] == "3"
    assert job["TMPDIR"] == job["TEMP"] == str(tmp_path / "j1" / "tmp")
    assert "PYTESTMUT_JOB" not in env


def test_environment_modes(tmp_path: Path) -> None:
    parent = {"PATH": "/bin", "GITHUB_TOKEN": "t", "CUSTOM": "1", "LC_ALL": "C"}
    inherit = build_environment(
        parent, SafetyConfig(env="inherit"), "r", tmp_path, tmp_path / "void"
    )
    assert inherit.base["GITHUB_TOKEN"] == "t" and not inherit.scrubbed
    assert "AWS_SHARED_CREDENTIALS_FILE" not in inherit.base
    allow = build_environment(
        parent,
        SafetyConfig(env="allowlist", env_keep=("CUSTOM",)),
        "r",
        tmp_path,
        tmp_path / "void",
    )
    assert "GITHUB_TOKEN" not in allow.base
    assert allow.base["CUSTOM"] == "1" and allow.base["LC_ALL"] == "C"


def _probe(**changes: object) -> PytestEnvironment:
    values: dict[str, object] = {
        "pytest_version": "9.0",
        "python_version": "3.13",
        "cache_tag": "cpython-313",
        "rootdir": Path("."),
        "inipath": "",
        "ini_addopts": (),
        "env_addopts": (),
        "python_files": ("test_*.py",),
        "testpaths": (),
        "plugins": {},
        "plugin_names": ("cacheprovider",),
    }
    values.update(changes)
    return PytestEnvironment(**values)  # type: ignore[arg-type]


def test_inherited_options_are_filtered() -> None:
    tokens = (
        "-q",
        "--lf",
        "--junitxml",
        "out.xml",
        "--html=report.html",
        "-n",
        "4",
        "--cov=pkg",
        "--strict-markers",
        "--pdb",
        "--custom",
    )
    assert filter_inherited(tokens, ("--custom",)) == [
        "-q",
        "-n",
        "4",
        "--cov=pkg",
        "--strict-markers",
    ]


def test_argv_overrides_follow_the_installed_plugins(tmp_path: Path) -> None:
    plain = build_argv(_probe(), PytestConfig(args=("tests",)), tmp_path)
    assert plain[:5] == (sys.executable, "-m", "pytest", "-p", "_pytestmut_recorder")
    assert ("-o", "addopts=") == plain[5:7]
    assert "-n" not in plain and "--no-cov" not in plain
    assert f"cache_dir={tmp_path / 'pytest-cache'}" in plain
    assert "no:randomly" in plain
    full = build_argv(
        _probe(
            ini_addopts=("-n", "auto", "--cov=pkg", "--lf"),
            env_addopts=("--randomly-seed=3",),
            plugins={"pytest-xdist": "3", "pytest-cov": "5", "pytest-randomly": "3"},
            plugin_names=(),
        ),
        PytestConfig(args=("tests",), extra_overrides=("-W", "ignore")),
        tmp_path,
    )
    assert "--lf" not in full
    assert full[-1] == "--no-cov"
    assert full[full.index("-W") - 2 : full.index("-W")] == ("-n", "0")
    assert "--randomly-dont-reorganize" in full and "no:randomly" not in full
    # Without the cache plugin its option would be unknown, hence a crash
    # under filterwarnings = error.
    assert not any(token.startswith("cache_dir=") for token in full)
    dropped = build_argv(
        _probe(ini_addopts=("--cov=pkg",)),
        PytestConfig(inherit_addopts=False),
        tmp_path,
    )
    assert "--cov=pkg" not in dropped


def test_random_ordering_is_switched_off(tmp_path: Path) -> None:
    # Installed but not asked for: blocked outright.
    quiet = build_argv(
        _probe(plugins={"pytest-randomly": "3"}), PytestConfig(), tmp_path
    )
    assert "no:randomly" in quiet and "no:random_order" in quiet
    assert not any(token.startswith("--randomly-") for token in quiet)
    # Asked for in addopts: blocking would be a usage error, so it is told
    # not to reorder instead.
    asked = _probe(
        ini_addopts=("-q", "--random-order-bucket=module"),
        plugins={"pytest-random-order": "1.2"},
    )
    argv = build_argv(asked, PytestConfig(), tmp_path)
    assert "no:random_order" not in argv
    assert argv.index("--random-order-bucket=none") > argv.index(
        "--random-order-bucket=module"
    )


def test_testmon_is_blocked_and_its_flags_dropped(tmp_path: Path) -> None:
    probe = _probe(
        ini_addopts=("--testmon", "--testmon-env", "ci", "-q"),
        env_addopts=("--testmon-env=local", "--tmnet"),
        plugins={"pytest-testmon": "2.2"},
    )
    argv = build_argv(probe, PytestConfig(), tmp_path)
    assert "no:pytest-testmon" in argv
    assert "-q" in argv
    for gone in ("--testmon", "--testmon-env", "--testmon-env=local", "--tmnet"):
        assert gone not in argv
    assert "ci" not in argv and "local" not in argv


def test_worker_and_budget_defaults() -> None:
    assert effective_cpus() >= 1
    assert cpu_workers(3) == 3 and cpu_workers(None) == effective_cpus()
    assert memory_budget(0) is None
    assert memory_budget(123) == 123
    automatic = memory_budget(None)
    total = total_memory()
    assert automatic is not None and automatic > 0
    if total is not None:
        assert automatic == total // 2 or abs(automatic - total / 2) < 2


def test_memory_plan_keeps_the_total_within_the_budget() -> None:
    plan = plan_memory(8 * GIB, 12, 400 * MIB, None)
    # 2.5x headroom on a 400 MiB suite is 1000 MiB per worker: eight fit.
    assert plan.limit == 1000 * MIB and plan.workers == 8
    assert plan.reduced and not plan.over_budget
    assert plan.limit is not None and plan.workers * plan.limit <= 8 * GIB
    roomy = plan_memory(64 * GIB, 4, 400 * MIB, None)
    assert roomy.workers == 4 and not roomy.reduced
    # A tiny suite still gets a floor an interpreter with pytest can live in.
    small = plan_memory(8 * GIB, 4, 30 * MIB, None)
    assert small.limit == 512 * MIB and small.workers == 4
    unknown = plan_memory(2 * GIB, 8, None, None)
    assert unknown.limit == 512 * MIB and unknown.workers == 4


def test_memory_plan_with_explicit_settings() -> None:
    explicit = plan_memory(4 * GIB, 8, 100 * MIB, 2 * GIB)
    assert explicit.limit == 2 * GIB and explicit.workers == 2
    # No per-process limit: workers are still sized from the measurement.
    unlimited = plan_memory(4 * GIB, 8, 1 * GIB, 0)
    assert unlimited.limit is None and unlimited.workers == 2
    no_budget = plan_memory(None, 8, 1 * GIB, None)
    assert no_budget.workers == 8 and no_budget.limit == int(2.5 * GIB)
    assert no_budget.budget is None and not no_budget.reduced


def test_memory_plan_when_one_run_exceeds_the_budget() -> None:
    # Full headroom does not fit, the run itself does: one worker, capped.
    tight = plan_memory(2 * GIB, 8, 1 * GIB, None)
    assert tight.workers == 1 and tight.limit == 2 * GIB and not tight.over_budget
    # Even a bare run does not fit: still one worker, and it is said out loud.
    over = plan_memory(1 * GIB, 8, 2 * GIB, None)
    assert over.workers == 1 and over.over_budget
    assert over.limit == int(1.25 * 2 * GIB)


def test_kernel_reports_the_peak_of_finished_children(tmp_path: Path) -> None:
    code = "data = bytearray(60 * 1024 * 1024); print(len(data))"
    outcome = LocalSupervisor().run(_spec(code, tmp_path), threading.Event())
    assert outcome.returncode == 0
    peak = children_peak_rss()
    if sys.platform == "win32":
        assert peak is None
    else:
        # The run is over in a moment, too quick to sample reliably.
        assert peak is not None and peak > 60 * 1024 * 1024


def test_container_memory_limits_are_read(tmp_path: Path) -> None:
    from pytestmut.exec.cpu import _cgroup_memory_limit

    assert _cgroup_memory_limit(tmp_path) is None
    (tmp_path / "memory.max").write_text("max\n")
    assert _cgroup_memory_limit(tmp_path) is None
    (tmp_path / "memory.max").write_text("7516192768\n")
    assert _cgroup_memory_limit(tmp_path) == 7 * GIB
    (tmp_path / "memory.max").unlink()
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n")
    assert _cgroup_memory_limit(tmp_path) is None
    (tmp_path / "memory" / "memory.limit_in_bytes").write_text("2147483648\n")
    assert _cgroup_memory_limit(tmp_path) == 2 * GIB


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_interrupts_a_run_like_ctrl_c() -> None:
    import signal

    from pytestmut.session import _terminate_as_interrupt

    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt):
        with _terminate_as_interrupt():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)
    assert signal.getsignal(signal.SIGTERM) is before


def test_format_size() -> None:
    assert format_size(None) == "unlimited"
    assert format_size(512 * MIB) == "512 MiB"
    assert format_size(3 * GIB // 2) == "1.5 GiB"
