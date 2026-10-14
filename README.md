<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-wordmark-dark.svg">
  <img alt="pytestmut" src="docs/assets/logo-wordmark-light.svg" width="440">
</picture>

### Find the bugs your tests would miss.

[![CI](https://github.com/martincjespersen/pytestmut/actions/workflows/ci.yml/badge.svg)](https://github.com/martincjespersen/pytestmut/actions/workflows/ci.yml)
[![Docs](https://readthedocs.org/projects/pytestmut/badge/?version=latest)](https://pytestmut.readthedocs.io)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-4F46E5)
![pytest](https://img.shields.io/badge/pytest-8%2B-14B8A6)
![Self-test mutation score](https://img.shields.io/badge/self--test%20mutation%20score-100%25-14B8A6)
![Types](https://img.shields.io/badge/types-mypy%20strict-334155)
![Code style](https://img.shields.io/badge/code%20style-black-000000)

[**Documentation**](https://pytestmut.readthedocs.io) ·
[Getting started](docs/getting-started.md) ·
[Coding agents](docs/guides/agents.md) ·
[CI/CD](docs/guides/ci.md) ·
[Configuration](docs/reference/configuration.md)

</div>

---

**pytestmut** makes small, deliberate mistakes in your code, one at a time, and
runs your pytest suite against each of them. If a test fails, the mistake was
caught. If every test still passes, you have found a place where a real bug
could hide, and pytestmut tells you which test would catch it.

> Line coverage tells you which code your tests **run**.
> Mutation testing tells you which code your tests **check**.

```text
$ pytestmut run
pytestmut 0.1.0: 15 mutants in 2 file(s), 3 tests, 12 worker(s), 2.3s

  killed            8
  survived          5
  no coverage       2

  score           53.3%  (8 detected of 15 scored: 5 survived, 2 without coverage)

Survivors (5)
  src/shop/pricing.py:8  cmp.boundary  [7bf19b971895]  in discount
      - if amount > 100 and loyal:
      + if amount >= 100 and loyal:
      Changing `>` -> `>=` at src/shop/pricing.py:8 in `discount` went unnoticed. Tests
      that ran this code and still passed: tests/test_pricing.py::test_discount. No test
      sits on the boundary of `>`. Add a case where both sides are equal and assert the
      result; ...
```

## Highlights

|  |  |
|---|---|
| ✅ **Correct results first** | A mutant only counts as caught when a test that normally passes fails with it active. Crashes, flaky tests and slow machines never count. |
| 🛡️ **Safe to run** | Your files are never modified. Test processes are contained, cleaned up and held to a memory budget, and credentials are kept out of their reach. |
| ⚡ **Fast enough for every day** | Each mutant runs only the tests that reach it, in parallel. Verdicts are cached between runs. |
| 🔒 **Strict where it matters** | Give critical code its own thresholds, mutation types and rules for exceptions. |
| 🤖 **Built for review** | Every missed mutant says what changed, which tests ran it, and what test would catch it, for people and coding agents alike. |
| 🔌 **Fits your workflow** | Terminal, pre-commit, CI, `pytest --mutate`, and HTML, markdown or JSON reports. |

## Quick start

```bash
uv add --dev /path/to/pytestmut    # not on PyPI yet: install from a checkout
pytestmut init --write             # add a starter [tool.pytestmut] to pyproject.toml
pytestmut run                      # mutate, test, report
pytestmut show 7bf19b97            # one mutant, and what test would catch it
```

```toml
# pyproject.toml
[tool.pytestmut]
paths = ["src/mypackage"]

[tool.pytestmut.thresholds]
min_score = 80.0
```

The [getting started guide](docs/getting-started.md) takes an example project
from its first run to every mutant caught.

## What you can do with it

| Goal | How | Guide |
|---|---|---|
| Find weak tests in a project | `pytestmut run` | [Getting started](docs/getting-started.md) |
| Test only what you changed | `pytestmut run --diff main` or `--staged` | [Choosing what to test](docs/guides/scoping.md) |
| Judge specific tests | `pytestmut run --tests tests/test_billing.py` | [Choosing what to test](docs/guides/scoping.md) |
| Demand more from critical code | `[[tool.pytestmut.policy]]` with its own thresholds | [Strict rules for critical code](docs/guides/policies.md) |
| Block weak commits | a pre-commit hook running `pytestmut run --hook` | [Pre-commit](docs/guides/pre-commit.md) |
| Fail a pipeline below a threshold | `pytestmut run` in CI; the exit code is the gate | [CI/CD](docs/guides/ci.md) |
| Let a coding agent check its own tests | `--tests`, actionable guidance and JSON output | [Coding agents](docs/guides/agents.md) |
| Publish the report with your docs | the HTML report as a docs page | [Reports in your docs site](docs/guides/docs-site.md) |
| Skip noise | built-in rules, `# pytestmut: skip -- why`, config | [Leaving code alone](docs/guides/suppressing.md) |
| Keep runs fast and light | test selection, cache, workers, memory budget | [Speed and memory](docs/guides/performance.md) |

**Reference:** [command line](docs/reference/cli.md) ·
[configuration](docs/reference/configuration.md) ·
[mutation operators](docs/reference/operators.md) ·
[how it works](docs/how-it-works.md) ·
[troubleshooting](docs/guides/troubleshooting.md)

## Why the numbers can be trusted

- **Every kill is real.** Verdicts come from a per-test record written inside
  pytest, not from exit codes. A must-die mutant proves the pipeline before
  any real one runs.
- **Mutations never touch disk.** They are applied in memory at import time,
  inside subprocesses too, so a crash can't leave mutated code behind.
- **Ambiguity is resolved, not guessed.** Timeouts are confirmed alone and
  against an unmutated run. Kills by order-dependent tests are checked.
  Survivors are re-checked against the whole suite.
- **pytestmut tests itself.** Its own verdict and policy logic is held to
  100% with zero survivors, and the report is published with
  [the docs](https://pytestmut.readthedocs.io).

## Status

Version **0.1.0**, not yet on PyPI. Requires Python 3.10+ and pytest 8+.
Tested on macOS with Python 3.10 to 3.13. Linux and Windows run in CI; Windows
process containment is new and has not been run there yet.

## Development

```bash
uv sync
uv run nox                        # format, imports, licenses, lint, types, tests
uv run nox -s "Mutation Tests"    # pytestmut on its own core modules
uv run nox -s "Serve Docs"        # the docs, with fresh mutation and license reports
```
