# Choosing what to test

A run has two dials: **which code is mutated** and **which tests judge it**.
Both default to your configuration; everything below narrows them for one run
or for good.

## Which code is mutated

### For good, in the config

```toml
[tool.pytestmut]
paths = ["src/app"]                                 # files, folders or globs
exclude = ["**/migrations/**", "_version.py", "src/app/cli.py"]
```

- Globs use `/` on every OS, and `**` crosses folders.
- A pattern without a `/` matches at any depth: `_version.py` matches
  `src/app/_version.py`.
- A pattern that matches a folder covers everything in it.
- Test files (pytest's `test_*.py` and `*_test.py` patterns), `conftest.py` and
  anything under pytest's `testpaths` are never mutated.

### For one run

| Goal | Command |
|---|---|
| Different paths than the config | `pytestmut run src/app/billing src/app/tax.py` |
| Only some files of the configured paths | `pytestmut run --only "src/app/billing/**"` |
| One mutant from the last report | `pytestmut run --only 7bf19b97` |
| Only lines you changed since `main` | `pytestmut run --diff main` |
| Only lines staged for the next commit | `pytestmut run --staged` |

`--only` takes path globs or mutant id prefixes, and may be repeated.

### Changed lines only

`--diff` and `--staged` keep only mutants on changed lines. Then they apply
the [`[tool.pytestmut.diff]`](../reference/configuration.md#toolpytestmutdiff)
limits, so a large change stays quick:

```toml
[tool.pytestmut.diff]
per_line = 1           # one mutant per changed line first
max_mutants = 40       # then sample up to this many
budget_seconds = 180   # stop starting new mutants after this
```

The report's scope line says what was tested, e.g.
`scope: lines changed against main`. Mutants left out by sampling show as
`not run`, and the run is marked *partial*.

## Which tests judge it

### For good, in the config

```toml
[tool.pytestmut.pytest]
args = ["tests/unit", "-m", "not slow"]   # passed to every pytest run
```

### For one run: judge specific tests

```bash
pytestmut run --tests tests/test_pricing.py
pytestmut run --tests tests/test_pricing.py::test_discount --tests tests/test_tax.py
pytestmut run --tests tests --pytest-args "-k 'refund and not slow'"
```

`--tests` does two things:

1. **Runs only those tests**: files, folders or node ids, repeatable.
2. **Mutates only the code they execute.** Everything else is out of scope,
   not reported as "no coverage", so the result describes those tests alone.

```text
pytestmut 0.1.0: 8 mutants in 2 file(s), 1 tests, 12 worker(s), 1.4s
scope: code reached by the selected tests
```

`--pytest-args` adds any other pytest options (`-k`, `-m`, `-p`, ...) as one
string.

!!! tip "Which tests did I just write?"
    `--tests` combines with `--staged` and `--diff`:
    `pytestmut run --staged --tests tests/test_pricing.py` judges your new
    tests against the lines you changed. This is the loop a
    [coding agent](agents.md) should use.

### The same from pytest

```bash
pytest --mutate                                    # everything, as configured
pytest --mutate tests/test_pricing.py -k discount  # these tests, all configured code
pytest --mutate --mutate-reached tests/test_pricing.py  # these tests, only code they reach
pytest --mutate --mutate-diff main --mutate-min-score 90
```

Everything on the pytest command line other than the `--mutate*` options is
passed on to the test runs.

## How each mutant's tests are chosen

You don't normally need to think about this: pytestmut traces which lines
each test executes and runs a mutant only against the tests that reach it.
Mutants in code that runs at import time, or inside cached functions, face the
whole suite, because tests can depend on that code without visibly running it.

To always run every test against every mutant (slower, but no tracing):

```toml
[tool.pytestmut.run]
selection = "all"
```
