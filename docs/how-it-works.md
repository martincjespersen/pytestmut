# How a run works

Each step exists because skipping it lets a wrong number through.

1. **Probe.** pytest starts once with a small plugin that reports the
   project's `addopts`, installed plugins and test-file patterns, then exits.
   Nothing is collected. This is how inherited options can be rewritten
   instead of trusted.
2. **Discover and generate.** Each target file is parsed with the running
   interpreter. Operators find candidate sites; each candidate becomes a text
   splice on the original bytes, so encoding, byte-order mark and line endings
   are untouched. Suppression rules and inline comments are applied, and the
   rest are compiled: a candidate that does not compile is `invalid`, one with
   the same bytecode as the original is `equivalent`, one with the same
   bytecode as an earlier candidate is a `duplicate`.
3. **Scope.** In diff mode, candidates outside the changed lines are dropped
   and the rest are sampled to the configured cap.
4. **Measure.** The test suite runs once, unmutated. A failing suite stops the
   run here. The peak memory of this run decides the per-worker limit and how
   many workers fit in the memory budget.
5. **Baseline.** The suite runs once per worker, all at the same time. A
   suite that fails only then is not safe to parallelise, and the run falls
   back to one worker. Durations measured here, under load, become the basis
   for time budgets. Tests whose outcome or id differs between runs are
   quarantined.
6. **Audit.** Every target file the tests imported must have come through the
   import hook, from the path being mutated. A same-named module imported
   from elsewhere means the tests use an installed copy, and the run stops.
7. **Trace.** One more unmutated run records which lines each test executes.
   Lines run at import time, and setup of shared fixtures, are attributed to
   every test that depends on them. Mutants in cached functions, and in the
   module's own helpers they call, run against every test: a test that gets
   a cached result never executes them, yet depends on them. Such a mutant
   first runs just the tests that execute its lines, which usually settle it
   in a second; only if they don't does it face the whole suite.
8. **Self-check.** One file gets a mutation that raises at import. If that is
   not reported as killed, the run stops: mutations are not reaching the
   tests.
9. **Execute.** Each mutant runs in a fresh `python -m pytest` process with
   the tests that reach its line, in baseline order, stopping at the first
   failure. Ambiguous outcomes get a follow-up before they are final:
    - a timeout is retried alone with a larger budget, then compared with an
      unmutated run of the same tests;
    - a kill without a failing test (an import or collection error) needs the
      same tests to start cleanly without the mutant;
    - the first kill credited to a test in a narrowed selection needs that
      test to pass on its own;
    - an error is retried once.
10. **Verify.** A sample of survivors is re-run against the whole suite. A
    kill there means test selection missed something, usually a result
    cached between tests, so every other survivor is re-run the same way and
    corrected. The reported numbers are then complete, and the miss is
    reported as a warning.
11. **Post-checks.** Tracked files and target files must be unchanged.
12. **Evaluate and report.** Each policy's thresholds are checked, the result
    is written to `.pytestmut/results.json`, and reports are rendered from it.

## How a mutant is activated

pytestmut prepends a directory holding a `sitecustomize.py` to `PYTHONPATH`
for the test processes. Every interpreter they start imports it, reads the
job description named by `PYTESTMUT_JOB`, and patches
`importlib.machinery.SourceFileLoader.get_code`. When the file being loaded is
the mutated one, matched by device and inode, the splice is applied to its
bytes in memory and the result is compiled directly.

Consequences:

- no mutated file ever exists on disk, and no bytecode for a target is read
  or written;
- `__file__`, `__spec__` and line numbers are those of the real file;
- subprocesses and `multiprocessing` children get the mutant too;
- the file's hash is checked first, so an edit made during the run is
  detected instead of being spliced into.

## What a verdict is based on

A plugin loaded into each test process writes one JSON line per event: the
mutated file being loaded, each test phase and its outcome, the session's own
exit status. The verdict is a pure function of that record and of how the
process ended. The exit code alone decides nothing, because a crash at
startup, a usage error and a killed mutant can all exit non-zero.

## Failures it is built to avoid

Mutation testing tools tend to fail quietly. Each design choice above answers
one of these:

| Risk | What pytestmut does |
|---|---|
| The mutated code is never loaded, so everything "survives" | Files are matched by identity, not module name. Every verdict needs proof that the mutated file was loaded. A must-die mutant runs first. |
| A crash is counted as a kill | Verdicts come from a per-test record written inside pytest, never from the exit code alone. |
| Mutated source is left in your tree after a crash | Nothing is written to your tree. The mutation exists only in memory. |
| Stale bytecode hides the mutant | The mutated file is compiled from memory and never reads or writes a `.pyc`. |
| Module-level code, class bodies and defaults are skipped | Each mutant is active from interpreter start, so import-time code is mutated like any other. |
| Subprocesses run unmutated code | Activation is inherited by every child interpreter. |
| Runaway processes exhaust memory or outlive the run | Every test process tree is contained, killed after every run, and held to a memory limit. |
| A slow machine looks like a caught mutant | Timeouts are retried alone and compared with an unmutated run. |
| A flaky or order-dependent test "kills" mutants | The baseline runs several times at full concurrency. Kills by tests that fail on their own are not trusted. |
| A test depends on cached results it never computes | Mutants in cached functions face every test. Survivors are re-checked against the whole suite. |
| Mutated code reaches real systems | Credential variables are withheld, credential files hidden, and calls that delete, execute or send are not mutated. |
| Tests write into the repository under a mutant | Tracked files are compared during and after the run. |

## Limits

- **Equivalent mutants.** Some changes cannot alter behaviour and will always
  survive. Only trivial ones are detected automatically; mark the rest with a
  skip and a reason.
- **Code pytestmut cannot intercept** is reported as `unsupported`, never
  silently scored: files pytest rewrites itself, compiled extensions, and code
  read with `open()`.
- **Import-time and cached code can need the whole suite**: when the tests
  that execute a mutant's lines don't catch it, every test runs, which costs
  time on large projects.
- **Memory limits on Linux and macOS are enforced by polling** five times a
  second, so a very fast allocation can overshoot briefly.
- **A child process that starts its own session** escapes the process-group
  cleanup on Linux and macOS.
- **`.env` files** loaded by your project can reintroduce withheld credentials.
