<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/martincjespersen/pytestmut/main/docs/assets/logo-wordmark-dark.svg">
  <img alt="pytestmut" src="https://raw.githubusercontent.com/martincjespersen/pytestmut/main/docs/assets/logo-wordmark-light.svg" width="440">
</picture>

### Find the bugs your tests would miss.

[![CI](https://github.com/martincjespersen/pytestmut/actions/workflows/ci.yml/badge.svg)](https://github.com/martincjespersen/pytestmut/actions/workflows/ci.yml)
[![Docs](https://readthedocs.org/projects/pytestmut/badge/?version=latest)](https://pytestmut.readthedocs.io)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-4F46E5)
![pytest](https://img.shields.io/badge/pytest-8%2B-14B8A6)
![Self-test mutation score](https://img.shields.io/badge/self--test%20mutation%20score-100%25-14B8A6)
![Types](https://img.shields.io/badge/types-mypy%20strict-334155)
[![License: MIT](https://img.shields.io/badge/license-MIT-334155)](https://github.com/martincjespersen/pytestmut/blob/main/LICENSE)
![Code style](https://img.shields.io/badge/code%20style-black-000000)

[**Documentation**](https://pytestmut.readthedocs.io) ·
[Getting started](https://pytestmut.readthedocs.io/en/latest/getting-started/) ·
[Coding agents](https://pytestmut.readthedocs.io/en/latest/guides/agents/) ·
[CI/CD](https://pytestmut.readthedocs.io/en/latest/guides/ci/) ·
[Configuration](https://pytestmut.readthedocs.io/en/latest/reference/configuration/)

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
uv add --dev pytestmut             # or: pip install pytestmut
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

The [getting started guide](https://pytestmut.readthedocs.io/en/latest/getting-started/) takes an example project
from its first run to every mutant caught.

## What you can do with it

| Goal | How | Guide |
|---|---|---|
| Find weak tests in a project | `pytestmut run` | [Getting started](https://pytestmut.readthedocs.io/en/latest/getting-started/) |
| Test only what you changed | `pytestmut run --diff main` or `--staged` | [Choosing what to test](https://pytestmut.readthedocs.io/en/latest/guides/scoping/) |
| Judge specific tests | `pytestmut run --tests tests/test_billing.py` | [Choosing what to test](https://pytestmut.readthedocs.io/en/latest/guides/scoping/) |
| Demand more from critical code | `[[tool.pytestmut.policy]]` with its own thresholds | [Strict rules for critical code](https://pytestmut.readthedocs.io/en/latest/guides/policies/) |
| Block weak commits | a pre-commit hook running `pytestmut run --hook` | [Pre-commit](https://pytestmut.readthedocs.io/en/latest/guides/pre-commit/) |
| Fail a pipeline below a threshold | `pytestmut run` in CI; the exit code is the gate | [CI/CD](https://pytestmut.readthedocs.io/en/latest/guides/ci/) |
| Let a coding agent check its own tests | `--tests`, actionable guidance and JSON output | [Coding agents](https://pytestmut.readthedocs.io/en/latest/guides/agents/) |
| Publish the report with your docs | the HTML report as a docs page | [Reports in your docs site](https://pytestmut.readthedocs.io/en/latest/guides/docs-site/) |
| Skip noise | built-in rules, `# pytestmut: skip -- why`, config | [Leaving code alone](https://pytestmut.readthedocs.io/en/latest/guides/suppressing/) |
| Keep runs fast and light | test selection, cache, workers, memory budget | [Speed and memory](https://pytestmut.readthedocs.io/en/latest/guides/performance/) |

**Reference:** [command line](https://pytestmut.readthedocs.io/en/latest/reference/cli/) ·
[configuration](https://pytestmut.readthedocs.io/en/latest/reference/configuration/) ·
[mutation operators](https://pytestmut.readthedocs.io/en/latest/reference/operators/) ·
[how it works](https://pytestmut.readthedocs.io/en/latest/how-it-works/) ·
[troubleshooting](https://pytestmut.readthedocs.io/en/latest/guides/troubleshooting/)

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

Alpha. Requires Python 3.10+ and pytest 8+. CI runs the full test suite on
Linux, macOS and Windows with Python 3.10 to 3.14.

## Development

```bash
uv sync
uv run nox                        # format, imports, licenses, lint, types, tests
uv run nox -s "Mutation Tests"    # pytestmut on its own core modules
uv run nox -s "Serve Docs"        # the docs, with fresh mutation and license reports
```

**Releasing:** bump `__version__` in `pytestmut/_meta.py` and merge to `main`.
Once CI passes, the release workflow builds and checks the package, publishes
it to TestPyPI, verifies what TestPyPI serves, then publishes it to PyPI and
tags a GitHub release. Pushes that keep the version publish nothing.

## License

[MIT](https://github.com/martincjespersen/pytestmut/blob/main/LICENSE)
