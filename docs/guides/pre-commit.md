# Pre-commit

Run mutation testing on the lines you are about to commit, and stop the
commit if they are not tested well enough.

## Set it up

Add a local hook to `.pre-commit-config.yaml`:

```yaml title=".pre-commit-config.yaml"
repos:
  - repo: local
    hooks:
      - id: pytestmut
        name: Mutation Testing (pytestmut)
        entry: uv run pytestmut run --hook
        language: system
        pass_filenames: false
        require_serial: true
        types_or: [python]
```

Then install the hook as usual: `pre-commit install`.

!!! warning "Use `language: system`"
    The hook must run in your project's environment, where your code and
    test dependencies are installed. pre-commit's isolated `language: python`
    environments cannot import your project. pytestmut stops with exit code 3
    in that case rather than report nonsense. Newer pre-commit versions also
    accept `language: unsupported`, which means the same.

## What `--hook` does

On `git commit` the hook:

1. **Tests only staged lines.** It mutates only the lines you staged. Unstaged
   edits are put aside by pre-commit and are not tested.
2. **Stays bounded:** one mutant per changed line first, then sampling, at
   most `diff.max_mutants` (40), and no new mutants after
   `diff.budget_seconds` (180 s).
3. **Reuses earlier work.** Mutants that haven't changed reuse cached verdicts.
4. **Fails the commit** if any policy misses its thresholds.

A commit with no mutable Python changes returns in about a second.

Example of a blocked commit:

```text
mutation testing.........................................................Failed
- hook id: pytestmut
- exit code: 1

scope: staged changes
  killed            2
  no coverage       3
...
No coverage (3)
  calc.py:10  return.none  [8337c89ff7ec]  in untested
      - return a - 1
      + return None
      No test executes this line. Add a test that calls `untested` so that line 10 runs, ...

FAILED: at least one policy missed its thresholds
```

## Choosing thresholds for commits

A diff is small, so a percentage is noisy: one survivor out of three is 67%.
Counts work better for commit checks:

```toml
[tool.pytestmut.thresholds]
max_survivors = 0       # new code must not leave survivors
max_no_coverage = 0     # and must be run by some test
```

To keep the strict rules for critical code only, put them in a
[policy](policies.md) with `always_full = true`. The whole policy area is then
tested whenever one of its files is in the commit.

## On push instead of on commit

If commit-time checks are too slow for your suite, run them before pushing.
pytestmut then tests the lines in the commits being pushed:

```yaml
      - id: pytestmut
        name: Mutation Testing (pytestmut)
        entry: uv run pytestmut run --hook
        language: system
        pass_filenames: false
        require_serial: true
        types_or: [python]
        stages: [pre-push]
```

Install the push hook with `pre-commit install --hook-type pre-push`.

## Tuning

```toml
[tool.pytestmut.diff]
max_mutants = 20            # smaller cap for faster commits
budget_seconds = 60         # give up starting new mutants after a minute
on_budget_exceeded = "pass" # "fail" to block the commit when time runs out
```

## Using this repository's hook definitions

pytestmut also ships `.pre-commit-hooks.yaml` with two hooks, `pytestmut`
(changed lines) and `pytestmut-full` (everything, on push or on demand). They
use `language: system` too, so pytestmut must be installed in the environment
pre-commit runs in.
