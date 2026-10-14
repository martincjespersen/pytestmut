# pytestmut

**Find the bugs your tests would miss.**

pytestmut makes small, deliberate mistakes in your code, one at a time, and
runs your pytest suite against each of them. If a test fails, the mistake was
caught. If every test still passes, you have found a place where a real bug
could hide, and pytestmut tells you what kind of test would catch it.

```python
def discount(amount: float, loyal: bool) -> float:
    if amount > 100 and loyal:      # pytestmut tries: amount >= 100
        return amount * 0.1         #                 amount > 101
    return 0.0                      #                 amount > 100 or loyal
```

```text
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

- **Correct results first.** A mutant only counts as caught when a test that
  normally passes fails with that mutant active. Crashes, flaky tests and
  slow machines are never mistaken for caught mutants.
- **Safe to run.** Your files are never modified, test processes are contained
  and cleaned up, and memory use stays within a budget.
- **Fast enough to use daily.** Each mutant runs only the tests that reach it,
  in parallel, and results are cached between runs.
- **Strict where it matters.** Give critical code its own thresholds, its own
  mutation types and its own rules for exceptions.
- **Built for review.** Every missed mutant says what changed, which tests ran
  it, and what test would catch it, in a form people and coding agents can act
  on.
- **Fits your workflow.** Terminal, pre-commit, CI, an HTML report in your docs
  site, or `pytest --mutate`.

## Find your way

| I want to... | Go to |
|---|---|
| try it on my project for the first time | [Getting started](getting-started.md) |
| understand the report and the score | [Reading the results](guides/results.md) |
| test only some code, or only some tests | [Choosing what to test](guides/scoping.md) |
| hold critical code to stricter rules | [Strict rules for critical code](guides/policies.md) |
| stop certain code from being mutated | [Leaving code alone](guides/suppressing.md) |
| check changes before every commit | [Pre-commit](guides/pre-commit.md) |
| fail a pipeline below a threshold | [CI/CD](guides/ci.md) |
| let a coding agent review its own tests | [Coding agents](guides/agents.md) |
| publish the report with my docs | [Reports in your docs site](guides/docs-site.md) |
| make runs faster or lighter | [Speed and memory](guides/performance.md) |
| fix an error message | [Troubleshooting](guides/troubleshooting.md) |
| look up a command, setting or operator | [Command line](reference/cli.md), [Configuration](reference/configuration.md), [Mutation operators](reference/operators.md) |

## Requirements

- Python 3.10 or newer, pytest 8 or newer.
- Your project's tests must import your source files directly: an editable
  install (`uv sync`, `pip install -e .`) or a layout pytest can import, such
  as `pythonpath = ["src"]`.
- Tested on macOS with Python 3.10 to 3.13. Linux and Windows are covered by
  the project's CI workflow; Windows support has not been run there yet.
