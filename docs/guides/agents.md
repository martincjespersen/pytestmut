# Coding agents

A coding agent that writes code and tests can use pytestmut to check its own
work. Passing tests alone don't show they would catch mistakes, so the agent
runs pytestmut on the tests it wrote or touched, reads the guidance on what
they miss, improves them, and repeats.

## The loop

```bash
# 1. Judge the tests you wrote or changed, against the code they exercise.
pytestmut run --tests tests/test_pricing.py --quiet

# 2. For a survivor, try your new test against that single mutant.
pytestmut exec 7bf19b97 -- tests/test_pricing.py -x

# 3. Run step 1 again until the exit code is 0.
```

| Exit code | Meaning for the agent |
|---|---|
| 0 | Done: the tests catch every mutant they should. |
| 1 | Some mutants survived: read the guidance and improve the tests. |
| 2 | The command or configuration is wrong: read the error. |
| 3 | The tests fail or cannot run without any mutation: fix them first. |
| 4 | pytestmut could not trust the result: read the "Failures" section. |
| 5 | The named tests reach no code that can be mutated: check the test names and `paths`. |

## What the agent sees

```text
$ pytestmut run --tests tests/test_pricing.py::test_discount --quiet
pytestmut 0.1.0: 8 mutants in 2 file(s), 1 tests, 12 worker(s), 1.4s
scope: code reached by the selected tests

  killed            5
  survived          3
...
Survivors (3)
  src/shop/pricing.py:8  cmp.boundary  [7bf19b971895]  in discount
      - if amount > 100 and loyal:
      + if amount >= 100 and loyal:
      Changing `>` -> `>=` at src/shop/pricing.py:8 in `discount` went unnoticed. Tests
      that ran this code and still passed: tests/test_pricing.py::test_discount. No test
      sits on the boundary of `>`. Add a case where both sides are equal and assert the
      result; the current tests only use values clearly on one side. If no input can
      tell the two versions apart, the mutant is equivalent: mark the line
      `# pytestmut: skip[cmp.boundary] -- <why>` instead of writing a test.
```

Every message names:

- **the change and its place:** file, line and function;
- **the tests that ran it and still passed:** the ones to extend;
- **the kind of test that would catch it;**
- **the escape hatch** for mutants that no test can catch: a skip comment with
  a reason.

## Scoping to the agent's own work

| The agent... | Run |
|---|---|
| wrote new tests | `pytestmut run --tests tests/test_new.py` |
| changed code and tests, not committed | `pytestmut run --diff HEAD --tests tests/test_new.py` |
| staged its work | `pytestmut run --staged --tests tests/test_new.py` |
| wants a subset by keyword | `pytestmut run --tests tests --pytest-args "-k refund"` |
| works through pytest | `pytest --mutate --mutate-reached tests/test_new.py` |

With `--tests`, code the named tests never execute is out of scope rather
than "no coverage". The agent is judged on the tests it wrote, not on the rest
of the project. Add `--diff` or `--staged` to narrow further to the lines it
changed.

## Reading results from a program

`.pytestmut/results.json` is written after every run.
`pytestmut report --format json` prints the same data.

```python
import json
import subprocess

run = subprocess.run(
    ["pytestmut", "run", "--tests", "tests/test_pricing.py", "--quiet"],
    capture_output=True,
    text=True,
)
result = json.load(open(".pytestmut/results.json", encoding="utf-8"))

for mutant in result["mutants"]:
    verdict = result["results"].get(mutant["id"], {})
    if verdict.get("status") in {"survived", "no_coverage"}:
        print(f'{mutant["path"]}:{mutant["span"]["line"]} [{mutant["id"]}]')
        print(f'  {mutant["original"]!r} -> {mutant["replacement"]!r}')
        print(f'  tests: {verdict["tests"]}')
        print(f'  {verdict["hint"]}')
```

Useful fields:

| Field | Where | Meaning |
|---|---|---|
| `id`, `path`, `span.line`, `scope`, `operator` | `mutants[]` | Which mutant, where, in which function, what kind |
| `original`, `replacement` | `mutants[]` | The exact change |
| `status` | `results[id]` | `killed`, `survived`, `no_coverage`, ... |
| `hint` | `results[id]` | The guidance paragraph |
| `tests` | `results[id]` | Tests that ran against the mutant (up to 10) |
| `killing_test` | `results[id]` | The test that caught it, for kills |
| `exit_code`, `failures`, `policies` | top level | The overall outcome |

## Instructions to give the agent

Put this in your `AGENTS.md`, `CLAUDE.md` or system prompt:

```markdown
## Mutation testing

After writing or changing tests, check that they would catch mistakes:

    pytestmut run --tests <every test file you created or changed> --quiet

- Exit code 0: done.
- Exit code 1: for each survivor, follow its guidance: add the missing case
  or assertion to the named tests, then run again. Use
  `pytestmut exec <id> -- <test file>` to confirm a new test fails on that
  mutant.
- Only if no input can distinguish the mutant from the original, mark the
  line `# pytestmut: skip[<operator>] -- <reason>`. Never add a skip
  without a reason, and never lower thresholds in pyproject.toml.
- Exit codes 2-5: a setup problem; read the message and fix the cause.
```

## Keeping it fast in a loop

- **Repeated runs reuse verdicts.** A mutant killed before is confirmed with
  its one killing test, so after a small change a run takes seconds.
- **Agent and CI runs share the cache.** Narrowed runs and full runs don't
  evict each other's cached verdicts.
- **On a busy machine, cap memory:** `--max-memory 2GiB`.
