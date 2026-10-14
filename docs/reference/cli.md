# Command line

Every command accepts `--help`. All commands look for the configuration in the
current directory and its parents; `--config FILE` uses a specific file.

## pytestmut run

Mutate your code, run the tests against each change, and report what they
missed.

```bash
pytestmut run [PATHS]... [OPTIONS]
```

| Option | What it does |
|---|---|
| `PATHS` | Files, folders or globs to mutate, instead of `paths` from the config |
| `--tests`, `-t TEST` | Run only these tests (files, folders, node ids; repeatable) and mutate only the code they execute |
| `--pytest-args "ARGS"` | Extra pytest arguments as one string, e.g. `"-k refund -m 'not slow'"` |
| `--only PATTERN` | Keep only mutants whose id starts with, or whose path matches, `PATTERN` (repeatable) |
| `--diff REF` | Only mutate lines changed against a git ref, e.g. `main` |
| `--staged` | Only mutate lines staged for the next commit |
| `--hook` | Pre-commit mode: staged lines (or pushed lines on `pre-push`), capped and time-boxed |
| `--workers`, `-j N` | Mutants tested at once |
| `--max-memory SIZE` | Memory all test processes together may use, e.g. `4GiB`, or `off` |
| `--time-limit SECONDS` | No new mutant starts after this many seconds. The rest are reported as `not_run`, the report is marked partial, and the run exits 1 at best |
| `--level LEVEL` | Mutation operators for the default policy: `core`, `extended` or `all` |
| `--min-score PERCENT` | Minimum score of the default policy |
| `--selection MODE` | `coverage` (only tests that reach a mutant) or `all` |
| `--report`, `-r FORMAT` | `text`, `markdown`, `html` or `json` (repeatable; replaces the configured formats) |
| `--no-cache` | Run every mutant; reuse and record nothing |
| `--show-suppressed` | List skipped candidates in the text report |
| `--quiet`, `-q` | No progress output; the report is still printed |
| `--keep-jobs` | Keep each test process's files, for debugging |

Examples:

```bash
pytestmut run
pytestmut run src/app/billing --report html
pytestmut run --tests tests/test_billing.py
pytestmut run --staged --tests tests/test_billing.py
pytestmut run --diff main --max-memory 4GiB
pytestmut run --only 7bf19b97
```

## pytestmut report

Show the last run again in any format, without running tests.

```bash
pytestmut report                                      # text to the terminal
pytestmut report --format markdown --output mutation.md
pytestmut report --format html --output mutation.html
pytestmut report --format json                        # for scripts
```

| Option | What it does |
|---|---|
| `--format`, `-f` | `text` (default), `markdown`, `html` or `json` |
| `--output`, `-o FILE` | Write to a file instead of the terminal |
| `--show-suppressed` | List skipped candidates in the text report |

## pytestmut show

One mutant: the change, what happened, and what test would catch it.

```bash
pytestmut show 7bf19b97
```

Any unique prefix of the id works.

## pytestmut exec

Run pytest yourself with one mutant active. Everything after `--` goes to
pytest.

```bash
pytestmut exec 7bf19b97 -- tests/test_pricing.py -x
```

Exits with pytest's exit code: 1 means a test caught the mutant.

## pytestmut operators

List every mutation operator with its level and an example.

## pytestmut init

```bash
pytestmut init           # print a starter configuration
pytestmut init --write   # append it to ./pyproject.toml
```

## pytestmut clean

Delete `.pytestmut/`: the cache, the last results and the reports.

## pytest --mutate

The pytest plugin runs the same thing from a pytest command line. Everything
except the `--mutate*` options is passed on to the test runs.

| Option | What it does |
|---|---|
| `--mutate` | Run mutation testing instead of the tests |
| `--mutate-reached` | Only mutate code the selected tests execute |
| `--mutate-level LEVEL` | Operator level of the default policy |
| `--mutate-min-score PERCENT` | Minimum score of the default policy |
| `--mutate-diff REF` | Only mutate lines changed against a git ref |

```bash
pytest --mutate
pytest --mutate --mutate-reached tests/test_pricing.py -k discount
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every policy passed |
| 1 | At least one policy missed a threshold |
| 2 | Configuration or usage error |
| 3 | The tests cannot serve as a baseline |
| 4 | pytestmut cannot trust the result |
| 5 | Nothing to mutate, or the selected tests reach no mutable code |
| 130 | Interrupted |

## Environment variables in test processes

Your tests and fixtures can read these:

| Variable | Value |
|---|---|
| `PYTESTMUT_ACTIVE` | `1` in every test process pytestmut starts. Use it to keep tests away from real systems. |
| `PYTESTMUT_WORKER` | `0`, `1`, ...: the worker slot. Use it for per-worker ports or databases. |
| `PYTESTMUT_RUN` | An id for the current run |
