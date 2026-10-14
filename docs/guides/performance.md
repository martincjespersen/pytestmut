# Speed and memory

A run tests every mutant in a fresh test process, so the main costs are how
many mutants there are, how many tests each one runs, and how many run at
once.

## What is already done for you

- **Test selection.** Each mutant runs only the tests that execute its line,
  stopping at the first failure. Mutants that must face the whole suite
  (import-time and cached code) first try the tests that execute their line.
- **Parallel workers.** By default there is one worker per CPU the process may
  use, container limits included.
- **Caching.** A mutant killed before is checked again with just its one
  killing test. Survivors are reused while nothing they depend on has changed.

## Make a run faster

| Situation | Do this |
|---|---|
| You only care about what you changed | `pytestmut run --diff main` or `--staged` |
| You are working on one area | `pytestmut run src/app/billing` or `--only "src/app/billing/**"` |
| You want to judge specific tests | `pytestmut run --tests tests/test_billing.py` |
| Too many mutants of one kind | `operators.disable = ["num.shift"]` |
| Module-level constants are expensive (they face the whole suite) | `run.static_mutants = "skip"` |
| Slow integration tests are not needed to judge mutants | `[tool.pytestmut.pytest] args = ["-m", "not slow"]` |
| Endless-loop mutants take long to time out | lower `timeout.floor_seconds` or `timeout.factor` |
| A clean slate is not needed every time | keep the cache: don't pass `--no-cache` |
| The run must end by a deadline (docs builders, time-limited CI) | `--time-limit 420`: no mutant starts after 7 minutes, and the report is marked partial |

## Control memory

The suite is run once, alone, to measure how much memory it needs. pytestmut
then gives every worker a hard limit and runs only as many workers as fit in
the total budget:

```text
pytestmut: memory: 184 MiB per run measured, budget 9.0 GiB, limit 512 MiB per worker, 12 worker(s)
```

- **The total budget** defaults to half of the memory available: physical
  memory, or the container's limit when that is smaller (Docker, Kubernetes,
  hosted CI and docs builders).
- **The per-worker limit** defaults to 2.5 times what one run of your suite
  needed, and at least 512 MiB.
- **The worker count is reduced** until workers × limit fits the budget. If
  this cost you workers, the report says so.
- **A worker that exceeds its limit is killed.** Its mutant is reported as
  `oom`, which counts as caught: the mutant made the tests consume unbounded
  memory.

=== "For one run"

    ```bash
    pytestmut run --max-memory 4GiB
    ```

=== "In the config"

    ```toml
    [tool.pytestmut.safety]
    memory_budget = "4GiB"     # all test processes together; "auto" or "off"
    memory_limit = "1GiB"      # one test process and its children; "auto" or "off"
    ```

Sizes accept `K`, `M`, `G`, `T` with an optional `iB`/`B` (all 1024-based),
or plain bytes.

!!! note
    With `memory_limit = "off"` the budget still sizes the worker count, but
    nothing stops a single process that grows past it.

## Control CPU

```toml
[tool.pytestmut.run]
workers = 4          # or "auto"
```

```bash
pytestmut run --workers 4
```

Libraries such as NumPy, OpenMP and Polars are limited to one thread per worker
(`safety.thread_caps`), so N workers really use about N CPUs.

## Timeouts

A mutant's time budget is `factor × (time its tests took in the baseline) +
grace_seconds`, at least `floor_seconds` and at most `max_seconds`. A mutant
that runs out of time is retried on its own with a larger budget. It is then
compared with an unmutated run of the same tests before it is reported as a
timeout, so a busy machine is never mistaken for a caught mutant.

```toml
[tool.pytestmut.timeout]
factor = 3.0
grace_seconds = 5
floor_seconds = 15
max_seconds = 900
retry_factor = 2.0
```
