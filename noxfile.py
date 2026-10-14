import sys
from pathlib import Path

import nox
from nox import Session, session

CODE_DIRECTORIES = ["noxfile.py", "pytestmut", "tests", "docs/hooks"]
DEFAULT_PYTHON_VERSION = False
DEFAULT_VENV_BACKEND = "none"
MINIMAL_CODE_COVERAGE = 70
LICENSE_REPORT = Path("build", "licenses.md")
PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]

# Specify default tasks
nox.options.sessions = [
    "Check Code Format",
    "Import Checking",
    "License Checking",
    "Lint Checking",
    "Type Checking",
    "Unit Tests",
]


@session(
    name="Check Code Format",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def format_checking(session: Session) -> None:
    """
    Checks code formatting using black
    """
    session.run("black", "--check", *CODE_DIRECTORIES)


@session(
    name="Import Checking",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def import_checking(session: Session) -> None:
    """
    Checks import formatting using isort with black profile
    """
    session.run("isort", "--check-only", *CODE_DIRECTORIES)


@session(
    name="License Checking",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def license_checking(session: Session) -> None:
    """Checks dependency licenses using loglicense"""
    license_report(session)


def license_report(session: Session, success_codes: list[int] | None = None) -> None:
    """
    Checks dependency licenses against .loglicense and writes the table the
    docs publish as the license report
    """
    LICENSE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    session.run(
        "loglicense",
        "check",
        "--dependency-file",
        "uv.lock",
        "--output-file",
        str(LICENSE_REPORT),
        success_codes=success_codes or [0],
    )


@session(
    name="Lint Checking",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def lint_checking(session: Session) -> None:
    """
    Lints code using flake8
    """
    session.run("flake8", *CODE_DIRECTORIES)


def unit_test(session: Session) -> None:
    session.run(
        "pytest",
        "--cov=pytestmut",
        "--cov-report",
        "html",
        f"--cov-fail-under={MINIMAL_CODE_COVERAGE}",
        "-n",
        "auto",
        *session.posargs,
    )


@session(
    name="Unit Tests",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def current_environment(session: Session) -> None:
    """
    Runs unit testing using pytest with required coverage
    """
    unit_test(session)


@session(name="Unit Tests - venv", python=PYTHON_VERSIONS)
def all_environment(session: Session) -> None:
    """
    Runs unit testing using pytest with required coverage
    """
    session.run_always("uv", "sync", external=True)
    unit_test(session)


@session(
    name="End To End Tests",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def end_to_end(session: Session) -> None:
    """
    Runs the whole tool against generated fixture projects
    """
    session.run("pytest", "-m", "e2e", "-n", "auto", *session.posargs)


@session(
    name="Mutation Tests",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def mutation_tests(session: Session) -> None:
    """
    Runs pytestmut on its own core modules, as configured in pyproject.toml
    """
    session.run("pytestmut", "run", *session.posargs)


@session(
    name="Type Checking",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def type_checking(session: Session) -> None:
    """Checks type information using mypy"""
    args = session.posargs or CODE_DIRECTORIES
    session.run(
        "mypy",
        "--no-strict-optional",
        "--ignore-missing-imports",
        f"--python-executable={sys.executable}",
        *args,
    )


@session(
    name="Format",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def format(session: Session) -> None:
    """Automatically format code using autoflake, black and isort"""
    session.run("autoflake", "--recursive", *CODE_DIRECTORIES)
    session.run("black", *CODE_DIRECTORIES)
    session.run("isort", *CODE_DIRECTORIES)


def mutation_report(session: Session) -> None:
    """
    Refreshes the mutation testing report that the docs embed

    A missed threshold still makes a report worth publishing, so exit code 1
    does not stop the docs. A broken baseline or a result pytestmut cannot
    trust does.
    """
    session.run(
        "pytestmut",
        "run",
        "--report",
        "text",
        "--report",
        "html",
        success_codes=[0, 1],
    )


def docs_reports(session: Session) -> None:
    """
    Refreshes the reports the docs publish

    Like a missed mutation threshold, a failed license check (exit code 1 or
    2) still writes a report worth reading, so it does not stop the docs. The
    License Checking session is the gate.
    """
    mutation_report(session)
    license_report(session, success_codes=[0, 1, 2])


@session(
    name="Serve Docs",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def serve_docs(session: Session) -> None:
    """
    Serves the docs with fresh mutation testing and license reports
    """
    docs_reports(session)
    session.run("mkdocs", "serve", *session.posargs)


@session(
    name="Build Docs",
    python=DEFAULT_PYTHON_VERSION,
    venv_backend=DEFAULT_VENV_BACKEND,
)
def build_docs(session: Session) -> None:
    """
    Builds the docs with fresh mutation testing and license reports
    """
    docs_reports(session)
    session.run("mkdocs", "build")
