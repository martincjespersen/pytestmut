---
hide:
  - navigation
  - toc
---

# pytestmut { .md-hidden style="display:none" }

<div class="pm-hero" markdown>

![pytestmut](assets/logo-wordmark-light.svg#only-light)
![pytestmut](assets/logo-wordmark-dark.svg#only-dark)

<p class="pm-tagline">Find the bugs your tests would miss.</p>

<p class="pm-sub">pytestmut makes small, deliberate mistakes in your code and runs
your pytest suite against each one. Every mistake that slips through comes back
with the test that would catch it.</p>

[Get started](getting-started.md){ .md-button .md-button--primary }
[Use it with a coding agent](guides/agents.md){ .md-button }
[GitHub](https://github.com/martincjespersen/pytestmut){ .md-button }

</div>

```text
$ pytestmut run
  src/shop/pricing.py:8  cmp.boundary  [7bf19b971895]  in discount
      - if amount > 100 and loyal:
      + if amount >= 100 and loyal:
      Changing `>` -> `>=` at src/shop/pricing.py:8 in `discount` went unnoticed. Tests
      that ran this code and still passed: tests/test_pricing.py::test_discount. No test
      sits on the boundary of `>`. Add a case where both sides are equal and assert the
      result; ...
```

Line coverage tells you which code your tests **run**. Mutation testing tells
you which code your tests **check**.

## Why pytestmut

<div class="grid cards" markdown>

-   :material-check-decagram:{ .lg .middle } **Correct results first**

    ---

    A mutant only counts as caught when a test that normally passes fails with
    it active. Crashes, flaky tests and slow machines never count.

-   :material-shield-check:{ .lg .middle } **Safe to run anywhere**

    ---

    Your files are never modified. Test processes are contained, cleaned up and
    held to a memory budget, and credentials are kept out of their reach.

-   :material-speedometer:{ .lg .middle } **Fast enough for every day**

    ---

    Each mutant runs only the tests that reach it, in parallel, and verdicts are
    cached between runs.

-   :material-lock-check:{ .lg .middle } **Strict where it matters**

    ---

    Give critical code its own thresholds, mutation types and rules for
    exceptions.

    [:octicons-arrow-right-24: Policies](guides/policies.md)

-   :material-robot:{ .lg .middle } **Built for review**

    ---

    Every missed mutant says what changed, which tests ran it, and what test
    would catch it, for people and coding agents alike.

    [:octicons-arrow-right-24: Coding agents](guides/agents.md)

-   :material-source-branch:{ .lg .middle } **Fits your workflow**

    ---

    Terminal, pre-commit, CI, `pytest --mutate`, or an HTML report in your docs
    site.

    [:octicons-arrow-right-24: CI/CD](guides/ci.md)

</div>

## Find your way

<div class="grid cards" markdown>

- :material-rocket-launch: [**Getting started**](getting-started.md): from first run to every mutant caught
- :material-file-chart: [**Reading the results**](guides/results.md): statuses, scores and reports
- :material-target: [**Choosing what to test**](guides/scoping.md): changed lines, specific tests, specific code
- :material-eye-off: [**Leaving code alone**](guides/suppressing.md): built-in rules and skip comments
- :material-lock-check: [**Strict rules for critical code**](guides/policies.md): per-area thresholds
- :material-source-commit: [**Pre-commit**](guides/pre-commit.md): check changes before each commit
- :material-pipe: [**CI/CD**](guides/ci.md): gate pipelines, publish reports
- :material-robot: [**Coding agents**](guides/agents.md): let an agent review its own tests
- :material-book-open-variant: [**Reports in your docs site**](guides/docs-site.md): MkDocs and Read the Docs
- :material-tune: [**Speed and memory**](guides/performance.md): workers, budgets, timeouts
- :material-lifebuoy: [**Troubleshooting**](guides/troubleshooting.md): every error message, explained
- :material-console: [**Command line**](reference/cli.md) · [**Configuration**](reference/configuration.md) · [**Operators**](reference/operators.md)

</div>

## Requirements

- Python 3.10 or newer, pytest 8 or newer.
- Your project's tests must import your source files directly: an editable
  install (`uv sync`, `pip install -e .`) or a layout pytest can import, such
  as `pythonpath = ["src"]`.
- Linux, macOS and Windows, with Python 3.10 to 3.14: the full test suite
  runs on each in CI.
