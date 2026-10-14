# Troubleshooting

Find the message you see, then the fix. Messages are quoted as pytestmut prints
them.

## Before any mutant runs

### "nothing to mutate: set paths in [tool.pytestmut] or pass them"

There is no `paths` setting and no path on the command line.

```toml
[tool.pytestmut]
paths = ["src/mypackage"]
```

or `pytestmut run src/mypackage`. `pytestmut init --write` adds a starter
configuration.

### "the unmutated test suite cannot serve as a baseline: tests failed"

Your tests fail without any mutation, so their failures would be meaningless.
The message lists the failing tests. Fix them, or set them aside:

```toml
[tool.pytestmut.run]
quarantine = ["tests/test_legacy.py::*"]   # these may fail and never count
# or: on_baseline_failure = "quarantine"   # set aside every failing test
```

If the tests pass when you run `pytest` yourself, look at the line *"these
environment variables are withheld from the tests: ..."*. A test may need one
of them:

```toml
[tool.pytestmut.safety]
env_keep = ["TEST_DATABASE_URL"]
```

### "no tests were collected"

pytest found no tests with the configured arguments. Check
`[tool.pytestmut.pytest] args`, your `--tests` values, or pytest's
`testpaths`.

### "the tests import an installed copy of the code instead of the files being mutated"

The tests load your package from `site-packages`, so no mutation would ever
reach them. Install the project in editable mode, or point pytest at the
sources:

```bash
uv sync            # uv installs the project itself in editable mode
pip install -e .   # with pip
```

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

### "pytest is running with --lf" (or `--pdb`, `--collect-only`, ...)

An option in your `addopts`, or set by a `conftest.py`, would change which
tests run. pytestmut removes the common ones itself; for others, tell it to
strip them:

```toml
[tool.pytestmut.pytest]
strip_flags = ["--my-custom-flag"]
```

### "flaky tests: these failed in some baseline runs and passed in others"

The suite ran several times without mutations and these tests did not give the
same result each time. Fix them, or quarantine them (see above).

### "the test suite passes alone but fails when N copies run at once"

Your tests share something between processes: a fixed port, a file, a
database. By default pytestmut then runs one mutant at a time (and says so).
To run in parallel again, make the shared resource per-worker. Every test
process gets `PYTESTMUT_WORKER` (0, 1, 2, ...):

```python title="conftest.py"
import os

import pytest


@pytest.fixture
def database_name():
    return f"test_db_{os.environ.get('PYTESTMUT_WORKER', '0')}"
```

or keep specific tests out of parallel runs:

```toml
[tool.pytestmut.run]
serial_tests = ["tests/test_db.py::*"]
```

### "one run of the suite needs more memory than allowed"

A single run of your tests exceeds the memory budget or limit. Raise it:

```bash
pytestmut run --max-memory 16GiB
```

### "the test suite passes normally but fails while being traced"

Tracing which lines each test runs disturbed a test, for example a test that
installs its own `sys.settrace`. Run without tracing:

```toml
[tool.pytestmut.run]
selection = "all"
```

### "self-check failed: a mutation that raises at import time was not detected"

pytestmut put a deliberate error into one of your files and the tests did not
notice. Mutations are not reaching your tests. The most common cause is the
same as for "installed copy" above. If that isn't it, please report the
message.

### "policy 'x' matches no code"

A policy's `paths` or `symbols` select nothing, often after a rename. Fix the
selector, or set `allow_empty = true` on the policy.

### "state directory ...: exists, is not empty and has no .pytestmut-state marker"

`state_dir` points at a folder pytestmut did not create. It refuses to use it,
so it can never delete your files. Choose another `state_dir`, or remove that
folder yourself.

### "in use by another run (pid N)"

Another pytestmut run is using the same project. Wait for it. If no such
process exists any more, delete `.pytestmut/lock`.

## Results that look wrong

### A file is reported as "never imported by the tests"

No test imports that file, so all its mutants are `no coverage`. Either add
tests for it, or exclude it:

```toml
[tool.pytestmut]
exclude = ["src/app/scripts/**"]
```

### A file is reported as unsupported

It is loaded in a way pytestmut cannot intercept: a file pytest rewrites
itself (its name matches pytest's test-file patterns), a compiled extension, or
code read with `open()` and executed. Its mutants are left out of the score.
Rename such modules, or exclude them.

### Mutants end in "error"

Look at the "Errors" section; every error has a reason:

| Reason | Meaning |
|---|---|
| `not-activated` | The tests passed, but the mutated file was never loaded |
| `failed-without-mutant` | A test failed before the mutant was active, so it is flaky or order-dependent |
| `crash` | The test process died, for example from a segfault |
| `budget-unsound` | Even unmutated, the same tests were too slow for their time budget |
| `selected-tests-missing` | Tests chosen for the mutant were not collected; their ids may not be stable |

Errors fail the run by default, because an untrustworthy result should not pass.
Allow a few with `run.max_errors`.

### "tracked files changed during the run"

A test wrote into your repository while a mutant was active, for example a
snapshot test updating its snapshots. Check `git status`, restore the files,
and make the test write to a temporary directory.

### "test selection missed N kill(s)"

Some test depends on code it never runs, usually through a cache. pytestmut
already re-checked every survivor against the whole suite, so the numbers are
correct; the runs just took longer. To avoid the extra work:

```toml
[tool.pytestmut.run]
selection = "all"
```

### Many timeouts

Mutants that make loops endless time out. That is normal and counts as
caught. If they make runs slow, see
[Speed and memory](performance.md#timeouts).

## Getting more detail

```bash
pytestmut show <id>                    # one mutant, its verdict and guidance
pytestmut exec <id> -- tests/test_x.py # run pytest yourself with it active
pytestmut run --keep-jobs              # keep each test process's files for inspection
pytestmut clean                        # start over: delete .pytestmut/
```
