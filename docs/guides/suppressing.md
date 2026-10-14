# Leaving code alone

Some mutants are not worth a test: a log message, a timeout of 30 versus 31
seconds, a change that cannot alter behaviour. pytestmut gives you several
ways to say so. Every one of them is recorded with a reason and counted in the
reports, so nothing disappears silently.

## What is skipped automatically

These are never mutated. Each is reported as `suppressed` with the reason code
shown in brackets.

| Code | What is left alone |
|---|---|
| `docstring` | Docstrings and other bare string statements |
| `annotation` | Type annotations, `TypeVar`, `NewType`, `cast` arguments |
| `type-checking` | `if TYPE_CHECKING:` blocks and `@overload` stubs |
| `module-metadata` | `__all__`, `__slots__`, `__version__` and other dunder assignments |
| `main-guard` | `if __name__ == "__main__":` |
| `platform-check` | Checks on `sys.version_info`, `sys.platform`, `os.name` |
| `logging` | Logging calls, `print`, `warnings.warn` and their arguments |
| `exception-message` | Text inside `raise SomeError("...")` |
| `assert` | `assert` statements inside your source code |
| `tunable` | Values like `timeout=30`, `retries=3`, `max_size=100`, `time.sleep(5)` |
| `hazard` | Arguments of calls that delete, execute or send: `shutil.rmtree`, `os.remove`, `subprocess.*`, `requests.*`, `cursor.execute` and similar |
| `hazard-guard` | Conditions that guard such a call, e.g. `if not dry_run:` |
| `cleanup-call` | Removing `close()`, `join()`, `shutdown()`, `release()` and similar |
| `decorator` | Expressions inside decorators |
| `repr` | The body of `__repr__` |
| `no-cover` | Code marked `# pragma: no cover` |

Turn any of them back on:

```toml
[tool.pytestmut.suppress]
builtin_disable = ["logging", "tunable"]
```

Add your own dangerous calls to the `hazard` list:

```toml
[tool.pytestmut.suppress]
hazardous_calls = ["*.purge", "billing.charge_card"]
```

## In the code

=== "One line"

    ```python
    TIMEOUT = 30  # pytestmut: skip -- operational setting, not behaviour
    ```

=== "Some operators on a line"

    ```python
    if attempts >= 3:  # pytestmut: skip[cmp.boundary, num.*] -- 3 vs 4 retries is a tuning choice
        give_up()
    ```

=== "A whole function"

    ```python
    def render_banner(self):  # pytestmut: skip -- cosmetic output
        ...
    ```

=== "A block"

    ```python
    # pytestmut: off -- generated lookup table
    TABLE = {...}
    # pytestmut: on
    ```

The text after `--` is the reason. It is optional unless a policy sets
`require_reason = true`, and it is always a good idea.

`# pragma: no mutate`, as used by other tools, works as a plain skip.

!!! warning "Skips that no longer do anything"
    A skip comment that suppresses no mutant is reported as a warning, so stale
    comments don't pile up. Make it an error with
    `suppress.report_unused = "error"`.

## In the config

```toml
[tool.pytestmut]
exclude = ["**/migrations/**", "src/app/generated/**"]   # whole files

[tool.pytestmut.suppress]
lines = ['^\s*logger\.debug\(', 'assert_never\(']        # regex on source lines
symbols = ["*.to_dict", "src/app/cli.py::main"]         # functions and classes
```

`symbols` matches qualified names such as `Invoice.total`. A name covers
everything nested in it, and `path::name` limits it to one file.

## Turning operators off

When a whole kind of mutation is noise for your code, stop generating it
instead of skipping it line by line:

```toml
[tool.pytestmut.operators]
level = "core"
disable = ["num.shift", "str.*"]
```

See [Mutation operators](../reference/operators.md) for the full list.

## Keeping skips honest

In code that matters, limit how much may be skipped. All of these work per
[policy](policies.md):

```toml
[[tool.pytestmut.policy]]
name = "billing"
paths = ["src/app/billing/**"]
require_reason = true               # a skip without "-- reason" is refused
max_suppressions = 2                # at most two skips in this code
allow_inline_suppressions = false   # or: no inline skips at all
```

A refused skip is not applied, and it is listed under the policy's failures.
