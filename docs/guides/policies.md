# Strict rules for critical code

Not all code deserves the same scrutiny. A rounding error in billing costs
money; a typo in a log line does not. **Policies** let you hold each part of
your code to its own standard.

## A first policy

```toml
[tool.pytestmut]
paths = ["src/app"]

[tool.pytestmut.thresholds]        # everything else
min_score = 70.0

[[tool.pytestmut.policy]]
name = "billing"
paths = ["src/app/billing/**"]
min_score = 95.0
max_survivors = 0
max_no_coverage = 0
```

The report now has a row per policy, and the run fails if any of them misses
its thresholds:

```text
  Policy    Mutants  Score   Covered  Survived  No cov  Result
  billing        42   97.6%    97.6%      1/0     0/0    FAIL
  default       180   74.4%    83.1%     31/-    10/-    pass

Failures
  billing: 1 survivor(s), at most 0 allowed
```

## Several policies

Add one `[[tool.pytestmut.policy]]` table per area. Here are three common
shapes:

=== "Critical: everything must be caught"

    ```toml
    [[tool.pytestmut.policy]]
    name = "billing"
    paths = ["src/app/billing/**"]
    level = "extended"          # try more kinds of mistakes here
    min_score = 100.0
    max_survivors = 0
    max_no_coverage = 0
    require_reason = true       # any skip must say why
    max_suppressions = 3
    always_full = true          # in pre-commit/diff runs, test the whole area
    ```

=== "Security: specific functions"

    ```toml
    [[tool.pytestmut.policy]]
    name = "auth"
    paths = ["src/app/auth/**"]
    symbols = ["verify_*", "Session"]   # only these definitions
    min_score = 95.0
    allow_inline_suppressions = false   # no skipping at all
    ```

=== "Legacy: be lenient"

    ```toml
    [[tool.pytestmut.policy]]
    name = "legacy"
    paths = ["src/app/legacy/**"]
    disable = ["str.*", "num.shift"]
    tolerate_survivors = ["cmp.boundary"]
    min_score = 50.0
    ```

## Which policy applies

Each mutant belongs to exactly one policy, chosen in this order:

1. **An inline tag** on the enclosing function or class wins:
   ```python
   def charge(self, amount):  # pytestmut: policy=billing
       ...
   ```
2. **The first policy in file order** whose `paths` and `symbols` both match.
   When policies overlap, put the more specific one first.
3. **The default**, `[tool.pytestmut.thresholds]`.

`symbols` are qualified-name globs such as `Invoice.total` or `compute_*`. A
name covers everything nested in it, so `Invoice` includes all of its methods.

## What a policy can set

| Setting | Meaning |
|---|---|
| `paths`, `symbols` | Which code the policy covers (at least one is required) |
| `level`, `enable`, `disable` | Which kinds of mutation are tried in this code |
| `tolerate_survivors` | Kinds of mutation allowed to survive here; they count neither as survivors nor in the score |
| `min_score`, `min_covered_score` | Minimum scores, in percent |
| `max_survivors`, `max_no_coverage`, `max_timeouts` | Maximum counts; `-1` is unlimited |
| `count_timeouts_as_killed` | Whether hangs count as caught (default yes) |
| `allow_inline_suppressions`, `require_reason`, `max_suppressions` | How much may be skipped |
| `static_mutants` | `"skip"` leaves import-time code alone in this area |
| `always_full` | With `--diff`, `--staged` or `--hook`, test the whole policy whenever one of its files changed |
| `allow_empty` | Accept a policy that matches no code (otherwise that is an error) |

Anything a policy leaves unset comes from `[tool.pytestmut.thresholds]`.

## Two kinds of "mutation types allowed"

These are easy to mix up:

- **`level` / `enable` / `disable`** decide which mutations are **tried**.
  Disabling `str.*` means string literals in this code are never changed.
- **`tolerate_survivors`** tries them but **forgives** survivors of those
  kinds. They show up in the report, but don't count against the policy.

Use the first for noise you never want to see, and the second for gaps you
accept but still want visible.

## Safety net

A policy whose `paths` and `symbols` match no code stops the run with a
configuration error. That way, renaming a folder cannot silently drop your
strictest rules. Set `allow_empty = true` for a policy that is meant to be
empty for now.
