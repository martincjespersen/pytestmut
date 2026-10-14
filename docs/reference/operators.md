# Mutation operators

An operator is one kind of small change. Operators are grouped in three
levels, and each level includes the one before it:

| Level | Use it for |
|---|---|
| `core` (default) | Changes that real bugs look like: wrong comparisons, wrong arithmetic, ignored return values. Few equivalent mutants. |
| `extended` | Adds changes to values, strings, defaults and control flow. Good for critical code. |
| `all` | Adds changes that are often noisy or cause endless loops. |

Choose per project or per [policy](../guides/policies.md):

```toml
[tool.pytestmut.operators]
level = "core"
enable = ["str.empty"]      # add single operators
disable = ["num.shift"]     # remove some; globs such as "cmp.*" work
```

`pytestmut operators` prints this list from the installed version.

## core

| Operator | Change | Example | A survivor usually means |
|---|---|---|---|
| `cmp.boundary` | `<` ↔ `<=`, `>` ↔ `>=` | `a < b` → `a <= b` | No test uses the boundary value itself |
| `cmp.negate` | Invert a comparison | `a == b` → `a != b` | One outcome of the comparison is never checked |
| `cmp.identity` | `is` ↔ `is not` | `x is None` → `x is not None` | Only one case (e.g. not-None) is tested |
| `cmp.membership` | `in` ↔ `not in` | `k in d` → `k not in d` | Only present, or only absent, items are tested |
| `bool.and_or` | `and` ↔ `or` | `a and b` → `a or b` | No case where exactly one condition holds |
| `bool.literal` | `True` ↔ `False` | `strict = True` → `strict = False` | The flag's effect is never asserted |
| `cond.negate` | Negate a condition | `if ready:` → `if not (ready):` | The tests can't tell the branches apart |
| `unary.not` | Remove `not` | `not x` → `x` | Only one truth value is tested |
| `unary.sign` | Remove `-` or `~` | `-x` → `x` | The sign of the result is never checked |
| `arith.add_sub` | `+` ↔ `-` | `a + b` → `a - b` | The exact result is not asserted |
| `arith.mul_div` | `*` ↔ `/`, `//` → `*` | `a * b` → `a / b` | The exact result is not asserted, or operands are 1 |
| `arith.mod_pow` | `%` or `**` → `*` | `a % b` → `a * b` | The exact result is not asserted |
| `bit.and_or` | `&` ↔ `\|`, `^` → `&` | `a & b` → `a \| b` | Bit patterns where they differ are not tested |
| `bit.shift` | `<<` ↔ `>>` | `a << 1` → `a >> 1` | The shifted value is not asserted |
| `aug.swap` | Swap an augmented operator | `n += 1` → `n -= 1` | An accumulated value is never checked |
| `num.shift` | Add one to a number | `x[0]` → `x[1]` | No test pins down this exact constant |
| `return.none` | Return `None` | `return total` → `return None` | The return value is never checked |
| `return.bool` | Return a constant boolean | `return a < b` → `return True` | Only True, or only False, results are tested |
| `call.minmax` | `min` ↔ `max` | `min(a, b)` → `max(a, b)` | Inputs are never different enough to matter |
| `call.anyall` | `any` ↔ `all` | `any(xs)` → `all(xs)` | No input with some, but not all, items true |
| `stmt.call_remove` | Remove a call statement | `audit(x)` → `pass` | The call's effect is never observed |

## extended

| Operator | Change | Example | A survivor usually means |
|---|---|---|---|
| `cond.const` | Force a condition | `if ok:` → `if True:` | One branch is never checked |
| `comp.filter_true` | Drop a comprehension filter | `[x for x in xs if p(x)]` → `... if True` | No input the filter should remove |
| `arith.floor_true` | `/` ↔ `//` | `a / b` → `a // b` | Operands always divide evenly |
| `aug.assign` | Augmented to plain assignment | `n += 1` → `n = 1` | Only one update is ever checked |
| `str.empty` | Empty a string, or fill an empty one | `"abc"` → `""` | The text is never asserted |
| `assign.none` | Assign `None` | `t = f(x)` → `t = None` | The variable does not affect asserted results |
| `return.empty` | Return an empty value of the annotated type | `return items` → `return []` | The contents of the result are not checked |
| `arg.default` | Change a parameter default | `def f(n=10)` → `n=11` | No test relies on the default |
| `stmt.super_remove` | Remove a `super()` call | `super().__init__()` → `pass` | State set up by the parent is not checked |
| `stmt.raise_remove` | Remove a `raise` | `raise ValueError()` → `pass` | No test expects the exception |
| `call.method_swap` | Swap paired methods | `s.lstrip()` → `s.rstrip()` | No input where they differ |
| `collection.empty` | Empty a list, tuple, set or dict | `[1, 2]` → `[]` | The contents are never asserted |
| `lambda.none` | A lambda returns `None` | `lambda x: x + 1` → `lambda x: None` | The lambda's result is never used in an assertion |

## all

| Operator | Change | Example | A survivor usually means |
|---|---|---|---|
| `fstring.expr` | Blank an f-string field (Python 3.12+) | `f"{name}"` → `f"{''}"` | The formatted text is never checked |
| `loop.break_continue` | `break` ↔ `continue` | `break` → `continue` | No input with items after the stop point |
| `decorator.remove` | Remove a decorator | `@cache` → *(nothing)* | The decorator's behaviour is not tested |

## What is never generated

These changes are left out on purpose, as noise or as a hazard:

- changing a string's case or adding characters to it;
- every possible comparison operator in place of every other;
- removing arguments from calls, which turns a guarded delete into an
  unguarded one;
- anything in code skipped by a [built-in rule](../guides/suppressing.md#what-is-skipped-automatically).

## How to skip one operator in one place

```python
retries = 3  # pytestmut: skip[num.shift] -- tuning value, not behaviour
```
