"""Build the environment test processes run in.

The same environment is used for the baseline and for every mutant, so a
variable the tests need shows up as a baseline failure with a clear message,
never as a wave of false kills.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pytestmut._meta import ENV_ACTIVE, ENV_JOB, ENV_RUN, ENV_WORKER
from pytestmut.config.model import SafetyConfig

SHIM_DIR = Path(__file__).resolve().parent.parent / "_shim"

# Names that usually hold credentials. Mutated code can turn a guarded call
# into an unguarded one, so the tests should not be able to reach anything
# real with them.
CREDENTIAL_PATTERNS = (
    "AWS_*",
    "AZURE_*",
    "ARM_CLIENT_*",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GCP_*",
    "GCLOUD_*",
    "CLOUDSDK_*",
    "*_TOKEN",
    "*_SECRET",
    "*_SECRET_*",
    "*_PASSWORD",
    "*_PASSWD",
    "*_API_KEY",
    "*_APIKEY",
    "*_ACCESS_KEY",
    "*_PRIVATE_KEY",
    "*_CREDENTIALS",
    "DATABASE_URL",
    "*_DATABASE_URL",
    "*_DSN",
    "SSH_AUTH_SOCK",
    "TWINE_*",
    "PYPI_*",
)
# Variables whose value would change how the test process itself behaves.
_ALWAYS_REMOVED = (
    "PYTEST_ADDOPTS",
    "PYTEST_CURRENT_TEST",
    "PYTEST_XDIST_*",
    "PYTEST_PLUGINS",
    "COVERAGE_PROCESS_START",
    "COVERAGE_PROCESS_CONFIG",
    "COVERAGE_CORE",
    "COV_CORE_*",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONINSPECT",
    "PYTESTMUT_*",
)
_ALLOWLIST_BASE = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_*",
    "TZ",
    "TERM",
    "VIRTUAL_ENV",
    "PYTHON*",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES*",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_*",
    "OS",
)
_CREDENTIAL_STORES = (
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
    "CLOUDSDK_CONFIG",
    "KUBECONFIG",
    "DOCKER_CONFIG",
    "NETRC",
    "AZURE_CONFIG_DIR",
)
_THREAD_CAPS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
    "POLARS_MAX_THREADS",
    "RAYON_NUM_THREADS",
)


def _any(name: str, patterns: tuple[str, ...]) -> bool:
    upper = name.upper()
    return any(fnmatch.fnmatchcase(upper, pattern.upper()) for pattern in patterns)


@dataclass(frozen=True)
class WorkerEnvironment:
    """The environment shared by every test process of a run.

    Attributes:
        base: Variables common to all jobs.
        scrubbed: Names removed because they look like credentials.
    """

    base: Mapping[str, str]
    scrubbed: tuple[str, ...]

    def for_job(self, job_dir: Path, worker: int) -> dict[str, str]:
        """The environment of one job.

        Arguments:
            job_dir: Directory of the job.
            worker: Worker slot the job runs in.

        Returns:
            A fresh mapping with the per-job variables added.
        """
        env = dict(self.base)
        scratch = str(job_dir / "tmp")
        env[ENV_JOB] = str(job_dir / "job.json")
        env[ENV_WORKER] = str(worker)
        env["TMPDIR"] = env["TEMP"] = env["TMP"] = scratch
        env["COVERAGE_FILE"] = str(job_dir / ".coverage")
        env["HYPOTHESIS_STORAGE_DIRECTORY"] = str(job_dir / "hypothesis")
        return env


def build_environment(
    parent: Mapping[str, str],
    safety: SafetyConfig,
    run_id: str,
    pycache_prefix: Path,
    void: Path,
) -> WorkerEnvironment:
    """Derive the test-process environment from the tool's own.

    Arguments:
        parent: The environment pytestmut was started with.
        safety: Containment settings.
        run_id: Id of the run.
        pycache_prefix: Bytecode cache shared by the run's processes.
        void: A path that does not exist, used to hide credential files.

    Returns:
        The environment and the names that were scrubbed.
    """
    env: dict[str, str] = {}
    scrubbed: list[str] = []
    drop = CREDENTIAL_PATTERNS + safety.env_drop
    for name, value in parent.items():
        if _any(name, _ALWAYS_REMOVED):
            continue
        if safety.env == "allowlist":
            if _any(name, _ALLOWLIST_BASE + safety.env_keep):
                env[name] = value
        elif (
            safety.env == "scrub"
            and _any(name, drop)
            and not _any(name, safety.env_keep)
        ):
            scrubbed.append(name)
        else:
            env[name] = value
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SHIM_DIR) + (os.pathsep + existing if existing else "")
    env[ENV_ACTIVE] = "1"
    env[ENV_RUN] = run_id
    env["PYTHONPYCACHEPREFIX"] = str(pycache_prefix)
    env["PYTHONBREAKPOINT"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    if safety.hash_seed != "inherit":
        env.setdefault("PYTHONHASHSEED", safety.hash_seed)
    if safety.thread_caps:
        for name in _THREAD_CAPS:
            env.setdefault(name, "1")
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
    if safety.redirect_credential_stores and safety.env != "inherit":
        for name in _CREDENTIAL_STORES:
            if not _any(name, safety.env_keep):
                env[name] = str(void)
    env.update(dict(safety.env_set))
    return WorkerEnvironment(base=env, scrubbed=tuple(sorted(scrubbed)))
