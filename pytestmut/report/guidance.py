"""Explain what a surviving or unreached mutant asks of the tests.

Each message says what was changed, which tests ran the code without
noticing, and what kind of test would notice. It is written to be acted on by
whoever owns the tests, including a coding agent reviewing its own work.
"""

from __future__ import annotations

from collections.abc import Callable

from pytestmut.mutation.model import Mutant, MutantResult, Status

_LISTED_TESTS = 5
_EQUIVALENT = (
    "If no input can tell the two versions apart, the mutant is equivalent: "
    "mark the line `# pytestmut: skip[{operator}] -- <why>` instead of "
    "writing a test."
)

Advice = Callable[[Mutant], str]
# Operators whose advice already says when to skip instead.
_MENTIONS_SKIP = frozenset({"num.shift", "str.empty"})


def _calculation(mutant: Mutant) -> str:
    return (
        "The result of this calculation is not checked precisely. Assert the "
        f"exact value, using operands for which `{mutant.original}` and "
        f"`{mutant.replacement}` give different results (avoid 0 and 1)."
    )


def _forced(mutant: Mutant) -> str:
    other = "false" if mutant.replacement == "True" else "true"
    return (
        f"Forcing this condition to {mutant.replacement} went unnoticed, so the "
        f"case where it is {other} is never checked. Add a test where it is "
        f"{other} and assert what the other branch does."
    )


def _returned(mutant: Mutant) -> str:
    where = f"`{mutant.scope}`" if mutant.scope else "this function"
    return (
        f"What {where} returns here is never checked. Assert on its return "
        "value for the input that reaches this line."
    )


_ADVICE: dict[str, Advice] = {
    "cmp.boundary": lambda m: (
        f"No test sits on the boundary of `{m.original}`. Add a case where both "
        "sides are equal and assert the result; the current tests only use "
        "values clearly on one side."
    ),
    "cmp.negate": lambda m: (
        "The tests never tell the two outcomes of this comparison apart. Cover "
        "the case where it holds and the case where it does not, and assert "
        "what differs."
    ),
    "cmp.identity": lambda m: (
        "Test both cases: the value being the identical object and not being "
        "it, and assert what differs."
    ),
    "cmp.membership": lambda m: (
        "Test with an item that is in the collection and one that is not, and "
        "assert the difference."
    ),
    "bool.and_or": lambda m: (
        "Add a case where exactly one of the two conditions is true; when both "
        "are true or both are false, `and` and `or` agree."
    ),
    "bool.literal": lambda m: (
        f"Assert behaviour that depends on this value being `{m.original}`."
    ),
    "cond.negate": lambda m: (
        "The tests never tell the two branches of this condition apart. Cover "
        "the case where it is true and the case where it is false, and assert "
        "what differs."
    ),
    "cond.const": _forced,
    "comp.filter_true": lambda m: (
        "Include items this filter should drop, and assert that they are " "dropped."
    ),
    "unary.not": lambda m: (
        "Removing `not` went unnoticed. Test both truth values of the operand "
        "and assert the outcome of each."
    ),
    "unary.sign": lambda m: ("Use a non-zero value and assert the sign of the result."),
    "arith.add_sub": _calculation,
    "arith.mul_div": _calculation,
    "arith.mod_pow": _calculation,
    "arith.floor_true": lambda m: (
        "Use operands that do not divide evenly and assert the exact result."
    ),
    "bit.and_or": _calculation,
    "bit.shift": _calculation,
    "aug.swap": lambda m: (
        "The accumulated value is not checked. Assert it after more than one " "update."
    ),
    "aug.assign": lambda m: (
        "The accumulated value is not checked. Assert it after more than one "
        "update, starting from a non-zero value."
    ),
    "num.shift": lambda m: (
        f"No test pins down the exact value `{m.original}`. Add a case right "
        "at it, where an off-by-one would show. If any nearby value would do, "
        "say so with `# pytestmut: skip[num.shift] -- <why>`."
    ),
    "str.empty": lambda m: (
        "This text is never checked. Assert the exact string where it ends "
        "up. If it is cosmetic, say so with "
        "`# pytestmut: skip[str.empty] -- <why>`."
    ),
    "return.none": _returned,
    "return.empty": _returned,
    "return.bool": lambda m: (
        f"Assert that `{m.scope or 'this function'}` returns True for one input "
        "and False for another that reaches this line."
    ),
    "assign.none": lambda m: (
        "The value assigned here does not affect anything the tests assert. "
        "Assert something derived from it."
    ),
    "arg.default": lambda m: (
        f"Call `{m.scope or 'this function'}` without this argument and assert "
        "the behaviour the default gives."
    ),
    "call.minmax": lambda m: (
        "Use inputs with several different values so that the smallest and the "
        "largest differ, and assert which one is used."
    ),
    "call.anyall": lambda m: (
        "Use inputs where some but not all items are true, and assert the " "result."
    ),
    "call.method_swap": lambda m: (
        f"Use input on which `{m.original}` and `{m.replacement}` differ, e.g. "
        "text with whitespace or a match at only one end, and assert the "
        "result."
    ),
    "stmt.call_remove": lambda m: (
        f"Removing `{m.original.strip()}` changed nothing the tests observe. "
        "Assert its effect (state it changes, data it writes, a call it "
        "makes), or remove the call if it is not needed."
    ),
    "stmt.super_remove": lambda m: (
        "Assert state or behaviour the parent class sets up here."
    ),
    "stmt.raise_remove": lambda m: (
        "No test expects this exception. Add a `pytest.raises` test with the "
        "input that should trigger it."
    ),
    "collection.empty": lambda m: (
        "Assert the contents of this collection, not only that it exists."
    ),
    "lambda.none": lambda m: (
        "Assert the value this lambda produces where it is used."
    ),
    "fstring.expr": lambda m: "Assert the formatted text, including this field.",
    "loop.break_continue": lambda m: (
        "Add a case where the loop has more items after the point where it "
        "stops or skips, and assert which items were processed."
    ),
    "decorator.remove": lambda m: (
        f"Assert behaviour that `{m.original.strip()}` adds, such as caching, "
        "validation or registration."
    ),
}


def _where(mutant: Mutant) -> str:
    inside = f" in `{mutant.scope}`" if mutant.scope else " at module level"
    return f"{mutant.path}:{mutant.span.line}{inside}"


def _tests(result: MutantResult) -> str:
    if not result.tests:
        if result.tests_selected:
            return f"{result.tests_selected} test(s) ran this code and none failed."
        return ""
    shown = ", ".join(result.tests[:_LISTED_TESTS])
    more = len(result.tests) - _LISTED_TESTS
    extra = f" and {more} more" if more > 0 else ""
    return f"Tests that ran this code and still passed: {shown}{extra}."


def guidance(mutant: Mutant, result: MutantResult) -> str:
    """Explain a mutant the tests did not catch.

    Arguments:
        mutant: The mutant.
        result: Its verdict.

    Returns:
        A short paragraph, or an empty string for statuses that need no
        action from the tests.
    """
    change = f"`{mutant.original.strip()}` -> `{mutant.replacement.strip()}`"
    if result.status is Status.NO_COVERAGE:
        target = f"`{mutant.scope}`" if mutant.scope else "this code"
        reason = (
            "The tests never import this file."
            if "never imported" in result.reason
            else "No test executes this line."
        )
        return (
            f"{reason} Add a test that calls {target} so that line "
            f"{mutant.span.line} runs, and assert its result; {change} must "
            "make that test fail."
        )
    if result.status is not Status.SURVIVED:
        return ""
    advise = _ADVICE.get(mutant.operator)
    parts = [
        f"Changing {change} at {_where(mutant)} went unnoticed.",
        _tests(result),
        advise(mutant) if advise is not None else "",
        (
            ""
            if mutant.operator in _MENTIONS_SKIP
            else _EQUIVALENT.format(operator=mutant.operator)
        ),
    ]
    return " ".join(part for part in parts if part)
