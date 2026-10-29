"""End-to-end: policies, suppressions, diff mode, the cache and the commands."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from pytestmut._meta import RESULT_SCHEMA_VERSION
from pytestmut.mutation.model import Status
from tests.conftest import ProjectFactory, git, run_tool

pytestmark = pytest.mark.e2e

BILLING = textwrap.dedent("""\
    def invoice_total(net, vat):
        if net < 0:
            raise ValueError("negative")
        return net + net * vat


    def round_up(value):
        return int(value) + 1
    """)
REPORTING = textwrap.dedent("""\
    def headline(count):
        if count > 10:
            return "many"
        return "few"
    """)
TESTS = textwrap.dedent("""\
    import pytest
    from app.billing import invoice_total, round_up
    from app.reporting import headline


    def test_invoice_total():
        assert invoice_total(100, 0.25) == 125


    def test_negative():
        with pytest.raises(ValueError):
            invoice_total(-1, 0.25)


    def test_round_up():
        assert round_up(1.5) == 2


    def test_headline():
        assert headline(50) == "many"
        assert headline(1) == "few"
    """)


def _project(make_project: ProjectFactory, config: str, billing: str = BILLING) -> Path:
    return make_project(
        {
            "pyproject.toml": textwrap.dedent(config),
            "app/__init__.py": "",
            "app/billing.py": billing,
            "app/reporting.py": REPORTING,
            "tests/test_app.py": TESTS,
        }
    )


def test_critical_policy_fails_on_a_survivor(make_project: ProjectFactory) -> None:
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [[tool.pytestmut.policy]]
        name = "billing-core"
        paths = ["app/billing.py"]
        max_survivors = 0
        min_score = 100
        """,
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 1, run.stderr
    by_name = {policy.name: policy for policy in run.result.policies}
    assert not by_name["billing-core"].passed
    assert by_name["default"].passed
    assert by_name["billing-core"].survived >= 1
    assert "billing-core" in run.stdout and "FAIL" in run.stdout
    billing = [m for m in run.result.mutants if m.path == "app/billing.py"]
    assert {m.policy for m in billing} == {"billing-core"}


def test_policy_selects_symbols_and_operators(make_project: ProjectFactory) -> None:
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [tool.pytestmut.operators]
        disable = ["num.shift"]

        [[tool.pytestmut.policy]]
        name = "money"
        symbols = ["invoice_*"]
        enable = ["num.shift"]
        tolerate_survivors = ["cmp.boundary", "num.shift"]
        max_survivors = 0
        """,
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stdout + run.stderr
    mutants = run.result.mutants
    money = [m for m in mutants if m.policy == "money"]
    assert {m.scope for m in money} == {"invoice_total"}
    assert any(m.operator == "num.shift" for m in money)
    assert not any(m.operator == "num.shift" for m in mutants if m.policy == "default")
    policy = {p.name: p for p in run.result.policies}["money"]
    assert policy.tolerated >= 1 and policy.survived == 0 and policy.passed


def test_inline_comments_suppress_and_tag(make_project: ProjectFactory) -> None:
    billing = textwrap.dedent("""\
        def invoice_total(net, vat):  # pytestmut: policy=strict
            if net < 0:  # pytestmut: skip[cmp.boundary, num.*] -- zero is fine
                raise ValueError("negative")
            return net + net * vat


        def round_up(value):
            return int(value) + 1  # pragma: no mutate
        """)
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [[tool.pytestmut.policy]]
        name = "strict"
        symbols = ["never_matches_by_itself"]
        allow_empty = true
        require_reason = true
        max_survivors = 0
        """,
        billing,
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stdout + run.stderr
    assert run.status(2, "cmp.boundary", path="app/billing.py") == "suppressed"
    assert run.status(2, "cmp.negate", path="app/billing.py") == "killed"
    line_eight = [m for m in run.result.mutants if m.span.line == 8]
    assert line_eight and all(m.reason == "inline" for m in line_eight)
    tagged = [m for m in run.result.mutants if m.scope == "invoice_total"]
    assert {m.policy for m in tagged} == {"strict"}


def test_skip_without_reason_is_refused_where_required(
    make_project: ProjectFactory,
) -> None:
    billing = BILLING.replace("if net < 0:", "if net < 0:  # pytestmut: skip")
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [[tool.pytestmut.policy]]
        name = "billing-core"
        paths = ["app/billing.py"]
        require_reason = true
        """,
        billing,
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 1
    assert run.status(2, "cmp.negate", path="app/billing.py") == "killed"
    assert "needs a reason" in run.stdout


def test_bad_configuration_is_exit_code_two(make_project: ProjectFactory) -> None:
    root = _project(make_project, '[tool.pytestmut]\npaths = ["app"]\nworkerz = 3\n')
    run = run_tool(root, "run")
    assert run.exit_code == 2
    assert "workerz" in run.stderr
    root.joinpath("pyproject.toml").write_text(
        '[tool.pytestmut]\npaths = ["app"]\n\n[[tool.pytestmut.policy]]\n'
        'name = "ghost"\npaths = ["nowhere/**"]\n',
        encoding="utf-8",
    )
    run = run_tool(root, "run")
    assert run.exit_code == 2
    assert "matches no code" in run.stderr


def test_min_score_gate(make_project: ProjectFactory) -> None:
    root = _project(make_project, '[tool.pytestmut]\npaths = ["app"]\n')
    assert run_tool(root, "run", "--no-cache", "--min-score", "100").exit_code == 1
    assert run_tool(root, "run", "--no-cache", "--min-score", "10").exit_code == 0


def test_reports_are_written_and_rerendered(make_project: ProjectFactory) -> None:
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [tool.pytestmut.report]
        formats = ["text", "markdown", "html", "json"]
        """,
    )
    run = run_tool(root, "run", "--no-cache")
    assert run.exit_code == 0, run.stderr
    reports = root / ".pytestmut" / "reports"
    markdown = (reports / "report.md").read_text(encoding="utf-8")
    assert "## Mutation testing: passed" in markdown and "| default |" in markdown
    html = (reports / "report.html").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "__DATA__" not in html
    assert "app/billing.py" in html and "http://" not in html and "https://" not in html
    data = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    assert data["schema"] == RESULT_SCHEMA_VERSION
    assert len(data["mutants"]) == len(run.result.mutants)
    again = run_tool(root, "report", "--format", "markdown")
    assert again.exit_code == 0 and "## Mutation testing" in again.stdout


def test_show_exec_init_and_clean(make_project: ProjectFactory) -> None:
    root = _project(make_project, '[tool.pytestmut]\npaths = ["app"]\n')
    run = run_tool(root, "run", "--no-cache")
    survivors = [
        m for m in run.result.mutants if run.result.status_of(m).value == "survived"
    ]
    killed = [
        m for m in run.result.mutants if run.result.status_of(m).value == "killed"
    ]
    shown = run_tool(root, "show", survivors[0].id[:8])
    assert shown.exit_code == 0 and survivors[0].operator in shown.stdout
    assert "reproduce: pytestmut exec" in shown.stdout
    # Running pytest by hand under a killed mutant fails, under a survivor passes.
    assert run_tool(root, "exec", killed[0].id, "--", "-q").exit_code == 1
    assert run_tool(root, "exec", survivors[0].id, "--", "-q").exit_code == 0
    assert "[tool.pytestmut]" in run_tool(root, "init").stdout
    assert run_tool(root, "operators").stdout.count("\n") > 30
    assert run_tool(root, "clean").exit_code == 0
    assert not (root / ".pytestmut").exists()


def test_cache_reuses_and_revalidates(make_project: ProjectFactory) -> None:
    root = _project(make_project, '[tool.pytestmut]\npaths = ["app"]\n')
    first = run_tool(root, "run")
    second = run_tool(root, "run")
    assert first.statuses() == second.statuses()
    results = second.result.results
    assert results and all(result.from_cache for result in results.values())
    killed = [r for r in results.values() if r.status.value == "killed"]
    # A cached kill is confirmed again by its one killing test.
    assert killed and all(r.tests_selected == 1 for r in killed)
    # A new test can kill an old survivor, so survivors must run again.
    tests = root / "tests" / "test_app.py"
    tests.write_text(
        tests.read_text(encoding="utf-8")
        + "\n\ndef test_zero_is_allowed():\n    assert invoice_total(0, 0.25) == 0\n",
        encoding="utf-8",
    )
    third = run_tool(root, "run")
    assert third.status(2, "cmp.boundary", path="app/billing.py") == "killed"
    assert first.status(2, "cmp.boundary", path="app/billing.py") == "survived"


def test_diff_mode_only_touches_changed_lines(make_project: ProjectFactory) -> None:
    root = _project(make_project, '[tool.pytestmut]\npaths = ["app"]\n')
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    untouched = run_tool(root, "run", "--no-cache", "--diff", "HEAD")
    assert untouched.exit_code == 0
    assert untouched.result.mutants == ()
    assert any("nothing to test" in w for w in untouched.result.warnings)
    reporting = root / "app" / "reporting.py"
    reporting.write_text(
        reporting.read_text(encoding="utf-8").replace("count > 10", "count > 20"),
        encoding="utf-8",
    )
    changed = run_tool(root, "run", "--no-cache", "--diff", "HEAD")
    assert changed.exit_code == 0, changed.stderr
    mutants = changed.result.mutants
    assert mutants and {m.path for m in mutants} == {"app/reporting.py"}
    assert {m.span.line for m in mutants} == {2}
    assert changed.result.scope == "lines changed against HEAD"
    assert "not_run" not in changed.statuses()
    # With a cap of one, a single mutant runs and the others are sampled out.
    config = root / "pyproject.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[tool.pytestmut.diff]\nmax_mutants = 1\n",
        encoding="utf-8",
    )
    capped = run_tool(root, "run", "--no-cache", "--diff", "HEAD")
    assert capped.statuses().get("not_run", 0) == len(capped.result.mutants) - 1
    assert capped.result.partial
    git(root, "add", "-A")
    staged = run_tool(root, "run", "--no-cache", "--staged")
    assert {m.path for m in staged.result.mutants} == {"app/reporting.py"}
    assert staged.result.scope == "staged changes"


def test_fastest_order_and_only_filter(make_project: ProjectFactory) -> None:
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [tool.pytestmut.run]
        test_order = "fastest"
        """,
    )
    ordered = run_tool(root, "run", "--no-cache")
    assert ordered.exit_code == 0, ordered.stderr
    assert "error" not in ordered.statuses()
    assert ordered.statuses()["killed"] >= 6
    only = run_tool(root, "run", "--no-cache", "--only", "app/reporting.py")
    assert {m.path for m in only.result.mutants} == {"app/reporting.py"}
    one = only.result.mutants[0]
    single = run_tool(root, "run", "--no-cache", "--only", one.id[:10])
    assert [m.id for m in single.result.mutants] == [one.id]


def test_time_budget_leaves_mutants_unrun(make_project: ProjectFactory) -> None:
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [tool.pytestmut.diff]
        budget_seconds = 1
        on_budget_exceeded = "fail"
        """,
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    reporting = root / "app" / "reporting.py"
    reporting.write_text(
        reporting.read_text(encoding="utf-8").replace("count > 10", "count > 20"),
        encoding="utf-8",
    )
    # The baseline alone takes longer than a one second budget.
    run = run_tool(root, "run", "--no-cache", "--diff", "HEAD", "--workers", "1")
    assert run.exit_code == 4
    assert run.statuses() == {"not_run": len(run.result.mutants)}
    assert run.result.partial
    assert any("time budget" in failure for failure in run.result.failures)


AGENT_TESTS = textwrap.dedent("""\
    from app.billing import invoice_total


    def test_invoice_total():
        assert invoice_total(100, 0.25) == 125
    """)


def _agent_project(make_project: ProjectFactory) -> Path:
    root = _project(
        make_project,
        """\
        [tool.pytestmut]
        paths = ["app"]

        [tool.pytestmut.thresholds]
        max_no_coverage = 0
        """,
    )
    (root / "tests" / "test_agent.py").write_text(AGENT_TESTS, encoding="utf-8")
    return root


def test_judging_only_the_tests_an_agent_wrote(make_project: ProjectFactory) -> None:
    root = _agent_project(make_project)
    run = run_tool(root, "run", "--no-cache", "--tests", "tests/test_agent.py")
    assert run.exit_code == 0, run.stdout + run.stderr
    result = run.result
    assert result.scope == "code reached by the selected tests"
    assert result.baseline.tests == 1
    # Only invoice_total runs; round_up and the reporting module are out of
    # scope, not "no coverage".
    assert {m.scope for m in result.mutants} == {"invoice_total"}
    assert "no_coverage" not in run.statuses()
    survivors = result.with_status(Status.SURVIVED)
    assert survivors, "the agent's single test leaves the boundary unchecked"
    hint = result.results[survivors[0].id].hint
    assert "tests/test_agent.py::test_invoice_total" in hint
    assert "went unnoticed" in hint and "# pytestmut: skip[" in hint
    assert "Tests that ran this code and still passed" in " ".join(run.stdout.split())


def test_tests_flag_combines_with_pytest_args_and_diff(
    make_project: ProjectFactory,
) -> None:
    root = _agent_project(make_project)
    narrowed = run_tool(
        root,
        "run",
        "--no-cache",
        "--tests",
        "tests/test_app.py",
        "--pytest-args",
        "-k headline",
    )
    assert narrowed.exit_code == 0, narrowed.stderr
    assert {m.scope for m in narrowed.result.mutants} == {"headline"}
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    billing = root / "app" / "billing.py"
    billing.write_text(
        billing.read_text(encoding="utf-8").replace("net * vat", "vat * net"),
        encoding="utf-8",
    )
    changed = run_tool(
        root, "run", "--no-cache", "--diff", "HEAD", "--tests", "tests/test_agent.py"
    )
    assert changed.exit_code == 0, changed.stderr
    assert {m.span.line for m in changed.result.mutants} == {4}
    assert changed.result.scope == (
        "lines changed against HEAD, code reached by the selected tests"
    )
    nothing = run_tool(
        root,
        "run",
        "--no-cache",
        "--tests",
        "tests/test_app.py::test_headline",
        "--only",
        "app/billing.py",
    )
    assert nothing.exit_code == 5
    assert "reach no mutable code" in nothing.stderr


def test_scoped_and_full_runs_share_the_cache(make_project: ProjectFactory) -> None:
    root = _agent_project(make_project)
    full = run_tool(root, "run")
    scoped = run_tool(root, "run", "--tests", "tests/test_agent.py")
    assert scoped.exit_code == 0, scoped.stderr
    # Kills from the full run are confirmed with their one killing test.
    killed = [r for r in scoped.result.results.values() if r.status is Status.KILLED]
    assert killed and all(r.from_cache for r in killed)
    again = run_tool(root, "run")
    assert again.statuses() == full.statuses()
    assert all(r.from_cache for r in again.result.results.values())


def test_report_formats_carry_the_guidance(make_project: ProjectFactory) -> None:
    root = _agent_project(make_project)
    run = run_tool(root, "run", "--no-cache", "--tests", "tests/test_agent.py")
    survivor = run.result.with_status(Status.SURVIVED)[0]
    markdown = run_tool(root, "report", "--format", "markdown").stdout
    assert "went unnoticed" in markdown
    data = json.loads(run_tool(root, "report", "--format", "json").stdout)
    entry = data["results"][survivor.id]
    assert entry["hint"].startswith("Changing")
    assert entry["tests"] == ["tests/test_agent.py::test_invoice_total"]
    shown = run_tool(root, "show", survivor.id)
    assert "went unnoticed" in shown.stdout


def test_show_explains_why_a_verdict_changed_between_runs(
    make_project: ProjectFactory,
) -> None:
    root = make_project(
        {
            "pyproject.toml": '[tool.pytestmut]\npaths = ["calc.py"]\n',
            "calc.py": "def is_adult(age):\n    return age >= 18\n",
            "tests/test_calc.py": textwrap.dedent("""\
                from calc import is_adult


                def test_adult():
                    assert is_adult(30)
                    assert not is_adult(5)
                """),
        }
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "start")
    first = run_tool(root, "run")
    survivor = next(
        mutant
        for mutant in first.result.with_status(Status.SURVIVED)
        if mutant.operator == "cmp.boundary"
    )
    provenance = first.result.provenance
    assert provenance.run_id and len(provenance.commit) == 40
    assert not provenance.dirty
    assert first.result.results[survivor.id].selection
    # What a coding agent does next: add the missing boundary test.
    with (root / "tests" / "test_calc.py").open("a", encoding="utf-8") as handle:
        handle.write(
            "\n\ndef test_boundary():\n"
            "    assert is_adult(18)\n"
            "    assert not is_adult(17)\n"
        )
    second = run_tool(root, "run")
    assert second.result.status_of(survivor) is Status.KILLED
    assert second.result.provenance.tests != provenance.tests
    assert second.result.provenance.dirty
    shown = run_tool(root, "show", survivor.id).stdout
    assert f"previously: survived in run {provenance.run_id}, now killed" in shown
    assert "test files changed" in shown
    assert "uncommitted changes" in shown
    history = root / ".pytestmut" / "history"
    assert len(list(history.glob("*.json"))) == 2
