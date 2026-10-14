# pytestmut

**Find the bugs your tests would miss.**

pytestmut makes small, deliberate mistakes in your code, one at a time, and
runs your pytest suite against each of them. If a test fails, the mistake was
caught. If every test still passes, you have found a place where a real bug
could hide, and pytestmut tells you what test would catch it.

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

Line coverage tells you which code your tests **run**. Mutation testing tells
you which code your tests **check**.

## Quick start

```bash
uv add --dev /path/to/pytestmut    # not on PyPI yet: install from a checkout
pytestmut init --write             # add a starter [tool.pytestmut] to pyproject.toml
pytestmut run                      # mutate, test, report
pytestmut show 7bf19b97            # one mutant, and what test would catch it
```

The [getting started guide](docs/getting-started.md) walks through a full
example, from the first run to every mutant caught.

## What you can do with it

| Goal | How | Guide |
|---|---|---|
| Find weak tests in a project | `pytestmut run` | [Getting started](docs/getting-started.md) |
| Test only what you changed | `pytestmut run --diff main` / `--staged` | [Choosing what to test](docs/guides/scoping.md) |
| Judge specific tests | `pytestmut run --tests tests/test_billing.py` | [Choosing what to test](docs/guides/scoping.md) |
| Demand more from critical code | `[[tool.pytestmut.policy]]` with its own thresholds | [Strict rules for critical code](docs/guides/policies.md) |
| Block weak commits | pre-commit hook with `pytestmut run --hook` | [Pre-commit](docs/guides/pre-commit.md) |
| Fail a pipeline below a threshold | `pytestmut run` in CI; the exit code is the gate | [CI/CD](docs/guides/ci.md) |
| Let a coding agent check its own tests | `--tests` plus actionable guidance and JSON output | [Coding agents](docs/guides/agents.md) |
| Publish the report with your docs | HTML report as an MkDocs page | [Reports in your docs site](docs/guides/docs-site.md) |
| Skip noise | built-in rules, `# pytestmut: skip -- why`, config | [Leaving code alone](docs/guides/suppressing.md) |
| Keep runs fast and light | test selection, cache, workers, memory budget | [Speed and memory](docs/guides/performance.md) |

Reference: [command line](docs/reference/cli.md),
[configuration](docs/reference/configuration.md),
[mutation operators](docs/reference/operators.md),
[how it works](docs/how-it-works.md),
[troubleshooting](docs/guides/troubleshooting.md).

## Why it can be trusted

- **A kill is real.** A mutant only counts as caught when a test that
  normally passes fails with the mutant active. Crashes, flaky tests and slow
  machines never count.
- **Your files are never touched.** Mutations are applied in memory, at
  import time, also inside subprocesses.
- **Containment.** Every test process is killed as a tree after each run, and
  memory stays within a budget (half of RAM by default).
- **Checks itself.** A self-check runs before any real mutant. Test selection
  is verified against the whole suite. Changes to tracked files are reported.

## Status

Version 0.1.0, not yet on PyPI. Python 3.10 or newer, pytest 8 or newer.
Tested on macOS with Python 3.10 to 3.13. Linux and Windows are covered by the
CI workflow, which has not run yet.

## Development

```bash
uv sync
uv run nox                        # format, imports, lint, types, tests
uv run nox -s "Mutation Tests"    # pytestmut on its own core modules
uv run nox -s "Serve Docs"        # docs, with a fresh mutation report as a page
```
