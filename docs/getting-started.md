# Getting started

This page takes a small project from "no mutation testing" to "every mutant
caught" in about ten minutes. Every command and output below is real.

## 1. Install

pytestmut runs your tests in the Python environment it is installed in, so add
it next to pytest in your development dependencies.

=== "uv"

    ```bash
    uv add --dev /path/to/pytestmut
    ```

=== "pip"

    ```bash
    pip install /path/to/pytestmut
    ```

!!! note "Not on PyPI yet"
    Until the first release is published, install from a checkout of the
    repository as shown above.

Check that it works:

```bash
pytestmut --version
```

## 2. The example project

```text
shop/
├── pyproject.toml
├── src/shop/__init__.py
├── src/shop/pricing.py
└── tests/test_pricing.py
```

```python title="src/shop/pricing.py"
def total(price: float, quantity: int) -> float:
    if quantity < 0:
        raise ValueError("quantity must not be negative")
    return price * quantity


def discount(amount: float, loyal: bool) -> float:
    if amount > 100 and loyal:
        return amount * 0.1
    return 0.0
```

```python title="tests/test_pricing.py"
import pytest

from shop.pricing import discount, total


def test_total():
    assert total(2.0, 3) == 6.0


def test_negative_quantity():
    with pytest.raises(ValueError):
        total(1.0, -1)


def test_discount():
    assert discount(200.0, True) == 20.0
```

These tests pass, and line coverage is high. Let's see what they actually
check.

## 3. Configure

Tell pytestmut what to mutate. `pytestmut init --write` adds a starter
configuration to `pyproject.toml`; for this project it boils down to:

```toml title="pyproject.toml"
[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.pytestmut]
paths = ["src/shop"]               # the code to mutate, never the tests

[tool.pytestmut.thresholds]
min_score = 80.0                   # fail the run below 80%
```

## 4. Run

```bash
pytestmut run
```

Progress goes to stderr while it works. The checks before the first mutant
(measuring the suite, running it in parallel, a self-check) are what make the
numbers trustworthy:

```text
pytestmut: checking the pytest setup
pytestmut: generating mutants for 2 file(s)
pytestmut: running the test suite once to measure it
pytestmut: memory: 54 MiB per run measured, budget 9.0 GiB, limit 512 MiB per worker, 12 worker(s)
pytestmut: running the baseline 12 times at once
pytestmut: mapping tests to the lines they execute
pytestmut: self-check: a mutant that must be killed
pytestmut: running 15 mutant(s) on 12 worker(s)
```

Then the report:

```text
pytestmut 0.1.0: 15 mutants in 2 file(s), 3 tests, 12 worker(s), 2.3s

  killed            8
  survived          5
  no coverage       2

  score           53.3%  (8 detected of 15 scored: 5 survived, 2 without coverage)
  covered score   61.5%  (8 detected of 13 that a test reaches)

  Policy   Mutants  Score  Covered  Survived  No cov  Result
  default       15  53.3%    61.5%       5/-     2/-    FAIL

Failures
  default: score 53.3% is below the minimum 80%
```

The run failed: the tests catch only 8 of the 15 mistakes.

## 5. Read what was missed

Below the summary, every missed mutant is listed with what to do about it:

```text
Survivors (5)
  src/shop/pricing.py:2  cmp.boundary  [b40f41600dd0]  in total
      - if quantity < 0:
      + if quantity <= 0:
      Changing `<` -> `<=` at src/shop/pricing.py:2 in `total` went unnoticed. Tests
      that ran this code and still passed: tests/test_pricing.py::test_total,
      tests/test_pricing.py::test_negative_quantity. No test sits on the boundary of
      `<`. Add a case where both sides are equal and assert the result; ...
  src/shop/pricing.py:8  bool.and_or  [76a067fea1ac]  in discount
      - if amount > 100 and loyal:
      + if amount > 100 or loyal:
      Changing `and` -> `or` at src/shop/pricing.py:8 in `discount` went unnoticed.
      Tests that ran this code and still passed: tests/test_pricing.py::test_discount.
      Add a case where exactly one of the two conditions is true; when both are true or
      both are false, `and` and `or` agree. ...

No coverage (2)
  src/shop/pricing.py:10  return.none  [0ac715072dba]  in discount
      - return 0.0
      + return None
      No test executes this line. Add a test that calls `discount` so that line 10 runs,
      and assert its result; `0.0` -> `None` must make that test fail.
```

Each finding is a concrete gap:

- nobody checks that a quantity of **exactly 0** is allowed;
- nobody checks an amount of **exactly 100**;
- nobody checks a **non-loyal** customer;
- the "no discount" path (`return 0.0`) is **never run** at all.

To look at one mutant on its own, use its id (or the start of it):

```bash
pytestmut show b40f4160
```

## 6. Fix the tests

Add the cases the guidance asks for:

```python title="tests/test_pricing.py (added)"
def test_zero_quantity_is_allowed():
    assert total(2.0, 0) == 0.0


def test_discount_rules():
    assert discount(100.0, True) == 0.0                  # exactly 100: no discount
    assert discount(101.0, True) == pytest.approx(10.1)  # just above: discount
    assert discount(200.0, False) == 0.0                 # not loyal: no discount
```

!!! tip "Check one mutant before running everything"
    `pytestmut exec b40f4160 -- tests/test_pricing.py` runs pytest with that
    single mutant active. If your new test fails there, it catches the mutant.

## 7. Run again

```bash
pytestmut run
```

```text
pytestmut 0.1.0: 15 mutants in 2 file(s), 5 tests, 12 worker(s), 1.9s

  killed           15

  score          100.0%  (15 detected of 15 scored: 0 survived, 0 without coverage)
  covered score  100.0%  (15 detected of 15 that a test reaches)

  Policy   Mutants   Score  Covered  Survived  No cov  Result
  default       15  100.0%   100.0%       0/-     0/-    pass

PASSED: every policy met its thresholds
```

Every mistake is now caught. The second run is faster because verdicts are
cached: a mutant that was killed before is checked again with just the one
test that killed it.

## Where next

- [Reading the results](guides/results.md): every status, both scores, the
  HTML report.
- [Pre-commit](guides/pre-commit.md) and [CI/CD](guides/ci.md): keep it that
  way.
- [Strict rules for critical code](guides/policies.md): demand 100% where it
  matters most.
