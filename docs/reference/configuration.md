# Configuration

Every setting, its default and what it does. For worked examples, see the
[guides](../getting-started.md).

pytestmut reads `[tool.pytestmut]` from `pyproject.toml`, or a standalone
`pytestmut.toml` with the same keys at the top level. Having both in one
directory is an error. The nearest file at or above the current directory is
used, and its directory is the project root.

- Unknown keys, operator ids and reason codes are errors, not warnings.
- Command line flags override the file.
- Path globs use `/` on every OS. `**` crosses directories; a pattern without a
  slash matches at any depth; a pattern that matches a directory also matches
  everything below it.

## Top level

| Key | Default | Meaning |
|---|---|---|
| `paths` | required | Files, directories or globs to mutate |
| `exclude` | `[]` | Path globs never mutated |
| `state_dir` | `".pytestmut"` | Where the cache, results and reports live |
| `fail_on_empty` | `true` | Exit 5 when there is nothing to mutate |

Test files (pytest's `python_files` patterns), `conftest.py` and anything under
pytest's `testpaths` are never mutated.

## `[tool.pytestmut.pytest]`

| Key | Default | Meaning |
|---|---|---|
| `args` | `[]` | Arguments added to every pytest run, such as test paths or `-m "not slow"` |
| `inherit_addopts` | `true` | Keep the project's `addopts` and `PYTEST_ADDOPTS`, minus unsafe flags |
| `strip_flags` | `[]` | Extra no-value flags to remove from inherited options |
| `extra_overrides` | `[]` | Arguments appended after everything else |

Removed from inherited options: `--lf`, `--ff`, `--nf`, `--sw`, `--pdb`,
`--trace`, `--collect-only`, `--setup-only`, `--setup-plan`, `--fixtures`,
`--snapshot-update`, `--force-regen`, `--regen-all`, and report-file options
such as `--junitxml` and `--html`. xdist is forced to `-n 0`, coverage is
switched off with `--no-cov`, and random ordering is disabled.

## `[tool.pytestmut.run]`

| Key | Default | Meaning |
|---|---|---|
| `workers` | `"auto"` | Mutants tested at once; `auto` is one per usable CPU, reduced to fit the memory budget |
| `selection` | `"coverage"` | `coverage` runs only tests that execute the mutated line; `all` runs every test for every mutant |
| `test_order` | `"stable"` | `stable` keeps the baseline order; `fastest` runs quick tests first |
| `static_mutants` | `"all-tests"` | Import-time mutants run against the whole suite, or `skip` them |
| `on_baseline_failure` | `"abort"` | `quarantine` continues without the failing tests |
| `on_parallel_failure` | `"serial"` | What to do when the suite only passes one copy at a time; `abort` stops |
| `quarantine` | `[]` | Test id globs that may fail and can never kill |
| `serial_tests` | `[]` | Test id globs whose mutants run with nothing else running |
| `verify_survivors` | `0.05` | Fraction of survivors re-run against the full suite as a check on test selection; if one is killed there, all survivors are re-run and corrected |
| `verify_survivors_max` | `10` | Upper bound on that sample |
| `self_check` | `true` | Run a mutant that must die before real ones |
| `max_errors` | `0` | Mutants allowed to end in `error` |

## `[tool.pytestmut.timeout]`

A mutant's budget is `factor x estimate + grace_seconds`, clamped to
`[floor_seconds, max_seconds]`. The estimate is the baseline duration of the
selected tests, measured under the same concurrency.

| Key | Default |
|---|---|
| `factor` | `3.0` |
| `grace_seconds` | `5` |
| `floor_seconds` | `15` |
| `max_seconds` | `900` |
| `retry_factor` | `2.0` (budget multiplier when a timeout is retried alone) |

## `[tool.pytestmut.operators]`

| Key | Default | Meaning |
|---|---|---|
| `level` | `"core"` | `core`, `extended` or `all` |
| `enable` | `[]` | Operator ids or globs added to the level |
| `disable` | `[]` | Operator ids or globs removed afterwards |

## `[tool.pytestmut.suppress]`

| Key | Default | Meaning |
|---|---|---|
| `builtin_disable` | `[]` | Built-in reason codes to switch off |
| `lines` | `[]` | Regexes; a matching source line is not mutated |
| `symbols` | `[]` | Qualified-name globs, optionally `path::Name` |
| `hazardous_calls` | `[]` | Dotted-name globs added to the built-in list |
| `tunable_keywords` | `[]` | Keyword-name globs added to the built-in list |
| `honor_no_cover` | `true` | Leave `# pragma: no cover` code alone |
| `foreign_pragmas` | `["pragma: no mutate"]` | Comment text treated as a bare skip |
| `require_reason` | `false` | Ignore inline skips without `-- reason` |
| `report_unused` | `"warn"` | `ignore`, `warn` or `error` for skips that suppress nothing |

Built-in reason codes: `docstring`, `annotation`, `type-checking`,
`module-metadata`, `main-guard`, `platform-check`, `logging`,
`exception-message`, `assert`, `tunable`, `hazard`, `hazard-guard`,
`cleanup-call`, `decorator`, `repr`, `no-cover`.

## `[tool.pytestmut.safety]`

| Key | Default | Meaning |
|---|---|---|
| `memory_budget` | `"auto"` | Memory all test processes together may use: `auto` (half of physical memory), `off`, or a size such as `8GiB` |
| `memory_limit` | `"auto"` | Memory one test process tree may use: `auto` (2.5 times what the baseline needed, at least 512 MiB), `off`, or a size |
| `env` | `"scrub"` | `scrub` withholds credential-looking variables, `allowlist` passes only `env_keep`, `inherit` passes everything |
| `env_drop` | `[]` | Name globs added to the withheld list |
| `env_keep` | `[]` | Exceptions, or the whole allowlist |
| `env_set` | `{}` | Variables set in every test process |
| `redirect_credential_stores` | `true` | Point `AWS_SHARED_CREDENTIALS_FILE`, `KUBECONFIG`, `NETRC` and similar at a path that does not exist |
| `thread_caps` | `true` | Limit BLAS and OpenMP pools to one thread per worker |
| `hash_seed` | `0` | `PYTHONHASHSEED` unless already set; `"inherit"` leaves it alone |
| `check_worktree` | `true` | Fail when tracked files change during the run |
| `max_output_bytes` | `131072` | Output kept per test process |
| `tmp_root` | `""` | Parent of the run's scratch space; empty means the system temp directory |

Withheld by default: `AWS_*`, `AZURE_*`, `GCP_*`, `*_TOKEN`, `*_SECRET`,
`*_PASSWORD`, `*_API_KEY`, `*_ACCESS_KEY`, `*_PRIVATE_KEY`, `*_CREDENTIALS`,
`DATABASE_URL`, `*_DSN`, `SSH_AUTH_SOCK` and a few more. The baseline runs with
the same environment, so a variable your tests need shows up as a baseline
failure that names it. Allow it with `env_keep`.

Every test process gets `PYTESTMUT_ACTIVE=1` and `PYTESTMUT_WORKER=<n>`. Use
them in fixtures to refuse real systems or to pick a per-worker database.

## `[tool.pytestmut.thresholds]`

The default policy. Every `[[policy]]` inherits these and may override them.

| Key | Default | Meaning |
|---|---|---|
| `min_score` | `0.0` | Minimum score in percent |
| `min_covered_score` | `0.0` | Minimum covered score in percent |
| `max_survivors` | `-1` | Survivors allowed; `-1` is unlimited |
| `max_no_coverage` | `-1` | Uncovered mutants allowed |
| `max_timeouts` | `-1` | Timeouts and memory kills allowed |
| `count_timeouts_as_killed` | `true` | Count confirmed timeouts and memory kills as detected |
| `allow_inline_suppressions` | `true` | Honour inline skip comments |
| `require_reason` | `false` | Honour only skips that give a reason |
| `max_suppressions` | `-1` | Skips written in code or config that are allowed |

## `[[tool.pytestmut.policy]]`

| Key | Meaning |
|---|---|
| `name` | Required. Shown in reports and used in `policy=` tags |
| `paths` | Path globs selecting files |
| `symbols` | Qualified-name globs selecting definitions; a name covers everything nested in it |
| `level`, `enable`, `disable` | Operators applied in this region, layered on the global selection |
| `tolerate_survivors` | Operators whose survivors are neither counted nor scored here |
| any threshold key | Overrides the default for this region |
| `static_mutants` | Overrides `run.static_mutants` |
| `always_full` | In diff mode, run the whole policy when one of its files changed |
| `allow_empty` | Accept a policy that matches no code |

At least one of `paths` and `symbols` is required. A policy that matches no
code is an error unless `allow_empty` is set, so a renamed directory cannot
silently drop its rules.

## `[tool.pytestmut.report]`

| Key | Default |
|---|---|
| `formats` | `["text"]` |
| `output_dir` | `".pytestmut/reports"` |
| `markdown_max_survivors` | `50` |
| `html_embed_source` | `true` |

## `[tool.pytestmut.cache]`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Reuse verdicts between runs |
| `extra_inputs` | `[]` | Globs of extra files, such as test data, whose content invalidates the cache |

The cache is discarded when pytestmut, Python, the platform, any installed
package, the pytest configuration or the timeout settings change.

## `[tool.pytestmut.diff]`

Used by `--diff`, `--staged` and `--hook`.

| Key | Default | Meaning |
|---|---|---|
| `base` | `""` | Git ref to diff against on every run; empty means full runs |
| `max_mutants` | `40` | Cap on mutants per run |
| `per_line` | `1` | Mutants taken per changed line before any others |
| `budget_seconds` | `180` | Wall-clock budget |
| `on_budget_exceeded` | `"pass"` | `fail` makes an exhausted budget an error |

## Complete example

```toml
[tool.pytestmut]
paths = ["src/app"]
exclude = ["**/migrations/**", "_version.py"]

[tool.pytestmut.pytest]
args = ["tests", "-m", "not slow"]

[tool.pytestmut.run]
workers = "auto"
serial_tests = ["tests/test_db.py::*"]

[tool.pytestmut.operators]
level = "core"
disable = ["num.shift"]

[tool.pytestmut.suppress]
lines = ['^\s*assert_never\(']
symbols = ["*.debug_dump", "src/app/cli.py::main"]

[tool.pytestmut.safety]
memory_budget = "8GiB"
env_keep = ["TEST_DATABASE_URL"]

[tool.pytestmut.thresholds]
min_score = 80.0

[[tool.pytestmut.policy]]
name = "billing-core"
paths = ["src/app/billing/**"]
level = "extended"
min_score = 95.0
max_survivors = 0
max_no_coverage = 0
require_reason = true
always_full = true

[tool.pytestmut.report]
formats = ["text", "html", "markdown"]
```
