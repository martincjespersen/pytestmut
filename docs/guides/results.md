# Reading the results

## The summary

```text
pytestmut 0.1.0: 15 mutants in 2 file(s), 3 tests, 12 worker(s), 2.3s

  killed            8
  survived          5
  no coverage       2

  score           53.3%  (8 detected of 15 scored: 5 survived, 2 without coverage)
  covered score   61.5%  (8 detected of 13 that a test reaches)

  Policy   Mutants  Score  Covered  Survived  No cov  Result
  default       15  53.3%    61.5%       5/-     2/-    FAIL
```

- **Counts per status.** How each mutant ended (see below).
- **Two scores**, each with the numbers it was computed from.
- **One row per policy.** `5/-` means 5 survivors and no limit; `0/0` would
  mean none allowed and none found.

## What each status means

| Status | What happened | What to do |
|---|---|---|
| **killed** | A test that normally passes failed with the mutant active. | Nothing. |
| **survived** | The tests ran the changed code and all passed. | Add or tighten a test; the guidance says how. |
| **no coverage** | No test runs that line at all. | Write a test that calls the code. |
| **timeout** | The mutant made the tests hang (usually an endless loop). Confirmed by a retry on its own and an unmutated control run. | Nothing; counts as caught. |
| **oom** | The mutant made the tests exhaust their memory limit. | Nothing; counts as caught. |
| **suppressed** | Left alone on purpose: a built-in rule (docstrings, logging...) or your own skip. Shown with its reason. | Nothing, unless the reason is wrong. |
| **equivalent** / **duplicate** | Compiles to the same code as the original, or as another mutant. | Nothing. |
| **invalid** | The change does not compile. | Nothing. |
| **unsupported** | The file is loaded in a way pytestmut cannot intercept. | See [Troubleshooting](troubleshooting.md#a-file-is-reported-as-unsupported). |
| **not run** | Left out by sampling or a time budget; the run is marked partial. | Run without the limit if you need the full picture. |
| **error** | pytestmut could not get a trustworthy answer. Fails the run by default. | See the error's detail and [Troubleshooting](troubleshooting.md). |

## The two scores

```text
score          = detected / (detected + survived + no coverage)
covered score  = detected / (detected + survived)
```

*Detected* is killed, plus timeout and oom (set
`count_timeouts_as_killed = false` to treat those as missed). Suppressed,
equivalent, duplicate, invalid, unsupported, not run and error mutants are left
out of both scores and counted separately, so they can neither flatter nor
punish you.

- **Score** answers "how much of this code do my tests check?".
- **Covered score** answers "where my tests do run, do they check?", which
  separates *untested* code from *weak* tests.

## Guidance on missed mutants

Every survivor and every uncovered mutant comes with a paragraph to act on:

```text
  src/shop/pricing.py:8  bool.and_or  [76a067fea1ac]  in discount
      - if amount > 100 and loyal:
      + if amount > 100 or loyal:
      Changing `and` -> `or` at src/shop/pricing.py:8 in `discount` went unnoticed.
      Tests that ran this code and still passed: tests/test_pricing.py::test_discount.
      Add a case where exactly one of the two conditions is true; when both are true or
      both are false, `and` and `or` agree. If no input can tell the two versions apart,
      the mutant is equivalent: mark the line `# pytestmut: skip[bool.and_or] -- <why>`
      instead of writing a test.
```

It always gives:

1. **what changed and where:** file, line and function;
2. **which tests ran that code** and still passed, which are the tests to
   extend;
3. **what kind of test would catch it**, specific to the kind of change;
4. **what to do if no test can**, because some mutants behave exactly like the
   original.

## One mutant in detail

```bash
pytestmut show b40f4160
```

```text
b40f41600dd0  cmp.boundary  survived
src/shop/pricing.py:2  in total  policy default

-     if quantity < 0:
+     if quantity <= 0:

tests selected: 2, run: 2, attempts: 1, 0.32s

Changing `<` -> `<=` at src/shop/pricing.py:2 in `total` went unnoticed. ...

reproduce: pytestmut exec b40f41600dd0 -- -x
```

`pytestmut exec <id> -- <pytest arguments>` runs pytest yourself with that one
mutant active. A test you just wrote should fail there; that is how you know it
catches the mutant.

```bash
pytestmut exec b40f4160 -- tests/test_pricing.py -x
```

## What changed since the last run

Every result records what produced it: a run id, the git commit (and whether
there were uncommitted changes), and digests of the mutated files, the test
files, the configuration and the environment. pytestmut keeps the results of
the last 10 runs in `.pytestmut/history/`, so `show` can say why a verdict
differs from the previous run:

```text
b40f41600dd0  cmp.boundary  killed
src/shop/pricing.py:2  in total  policy default
run 6e0da60a, commit af444a3d with uncommitted changes, 2026-10-29T09:14:18+00:00
...
previously: survived in run c0b500e0, now killed (2026-10-29T09:14:16+00:00)
changed since: test files changed; different tests selected (1 -> 2)
```

| Change named | Meaning |
|---|---|
| `<file> changed` | The file holding the mutant was edited |
| `test files changed` | A test file that ran, or a `conftest.py` above it, was edited |
| `configuration changed` | Settings that affect verdicts changed (cache and report settings don't count) |
| `environment changed` | pytestmut, Python, an installed package or the pytest setup changed |
| `different tests selected` | Another set of tests was chosen for the mutant |
| `re-checked with just the test that killed it before` | A known kill was confirmed with its earlier killing test only |
| `nothing recorded` | Nothing changed; if the verdict did, the tests are flaky or order-dependent |

A mutant keeps its id across edits elsewhere in its file, so it can be
followed from run to run.

## Report formats

Choose formats in the config or per run. `text` goes to the terminal; the
others are written to `.pytestmut/reports/`.

=== "Config"

    ```toml
    [tool.pytestmut.report]
    formats = ["text", "html", "markdown", "json"]
    ```

=== "Command line"

    ```bash
    pytestmut run --report text --report html
    ```

| Format | File | Best for |
|---|---|---|
| `text` | terminal | Reading locally, logs |
| `markdown` | `report.md` | Pull-request comments, CI job summaries |
| `html` | `report.html` | Browsing: your source with every mutant shown in place, filters by status, per-policy table. One self-contained file. |
| `json` | `report.json` | Scripts and agents: every mutant, verdict, hint and test list |

Re-render the last run in any format without running a test:

```bash
pytestmut report --format html --output mutation.html
pytestmut report --format markdown
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every policy passed. |
| 1 | At least one policy missed a threshold. |
| 2 | Configuration or usage error. |
| 3 | Your tests cannot serve as a baseline (they fail, are flaky, or test an installed copy). |
| 4 | pytestmut cannot trust the result (see the "Failures" section). |
| 5 | Nothing to mutate. |
| 130 | Interrupted. |

## Warnings worth reading

| Warning | Meaning |
|---|---|
| `withheld from the tests: ...` | These environment variables look like credentials and were hidden from the tests. Allow one with `safety.env_keep` if a test needs it. |
| `the memory budget ... limits the run to N worker(s)` | Fewer workers than CPUs, to stay within memory. See [Speed and memory](performance.md). |
| `the test suite fails when N copies run at once ...` | Your tests share something (a port, a file, a database); mutants run one at a time. The failing tests are named. |
| `test selection missed N kill(s) ...` | Some test depends on code it never runs, usually through a cache. Every survivor was re-checked against the whole suite, so the numbers are still right. |
| `N test(s) are quarantined ...` | Tests that fail or are flaky without any mutant are set aside and listed. |
