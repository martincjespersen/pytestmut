"""The catalogue of mutation operators and how sets of them are selected."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from pytestmut.exceptions import ConfigError


class Level(str, Enum):
    """Operator tiers, each including the ones before it."""

    CORE = "core"
    EXTENDED = "extended"
    ALL = "all"


_LEVEL_RANK = {Level.CORE: 0, Level.EXTENDED: 1, Level.ALL: 2}


@dataclass(frozen=True)
class OperatorInfo:
    """Description of one operator.

    Attributes:
        id: Stable identifier used in config, comments and reports.
        level: Lowest tier that includes the operator.
        summary: What it changes.
        example: A before and after.
    """

    id: str
    level: Level
    summary: str
    example: str


def _op(identifier: str, level: Level, summary: str, example: str) -> OperatorInfo:
    return OperatorInfo(id=identifier, level=level, summary=summary, example=example)


_CORE, _EXT, _ALL = Level.CORE, Level.EXTENDED, Level.ALL

OPERATORS: dict[str, OperatorInfo] = {
    info.id: info
    for info in (
        _op("cmp.boundary", _CORE, "shift a comparison boundary", "a < b -> a <= b"),
        _op("cmp.negate", _CORE, "negate a comparison", "a == b -> a != b"),
        _op("cmp.identity", _CORE, "swap is / is not", "x is None -> x is not None"),
        _op("cmp.membership", _CORE, "swap in / not in", "k in d -> k not in d"),
        _op("bool.and_or", _CORE, "swap and / or", "a and b -> a or b"),
        _op("bool.literal", _CORE, "flip a boolean literal", "True -> False"),
        _op("cond.negate", _CORE, "negate a condition", "if ready: -> if not (ready):"),
        _op("unary.not", _CORE, "remove not", "not x -> x"),
        _op("unary.sign", _CORE, "remove unary minus or invert", "-x -> x"),
        _op("arith.add_sub", _CORE, "swap + and -", "a + b -> a - b"),
        _op("arith.mul_div", _CORE, "swap * and /", "a * b -> a / b"),
        _op("arith.mod_pow", _CORE, "replace % or ** by *", "a % b -> a * b"),
        _op("bit.and_or", _CORE, "swap bitwise and / or", "a & b -> a | b"),
        _op("bit.shift", _CORE, "swap shift direction", "a << 1 -> a >> 1"),
        _op("aug.swap", _CORE, "swap an augmented operator", "n += 1 -> n -= 1"),
        _op("num.shift", _CORE, "add one to a number", "x[0] -> x[1]"),
        _op("return.none", _CORE, "return None instead", "return total -> return None"),
        _op("return.bool", _CORE, "return a constant boolean", "return a < b -> True"),
        _op("call.minmax", _CORE, "swap min / max", "min(a, b) -> max(a, b)"),
        _op("call.anyall", _CORE, "swap any / all", "any(xs) -> all(xs)"),
        _op("stmt.call_remove", _CORE, "remove a call statement", "audit(x) -> pass"),
        _op("cond.const", _EXT, "force a branch", "if ok: -> if True:"),
        _op("comp.filter_true", _EXT, "drop a comprehension filter", "if p(x) -> True"),
        _op("arith.floor_true", _EXT, "swap / and //", "a / b -> a // b"),
        _op("aug.assign", _EXT, "turn augmented into plain", "n += 1 -> n = 1"),
        _op("str.empty", _EXT, "empty a string literal", '"abc" -> ""'),
        _op("assign.none", _EXT, "assign None instead", "t = f(x) -> t = None"),
        _op("return.empty", _EXT, "return an empty value", "return items -> []"),
        _op("arg.default", _EXT, "change a default value", "def f(n=10) -> n=11"),
        _op("stmt.super_remove", _EXT, "remove a super() call", "super().m() -> pass"),
        _op("stmt.raise_remove", _EXT, "remove a raise", "raise E() -> pass"),
        _op("call.method_swap", _EXT, "swap a paired method", "lstrip() -> rstrip()"),
        _op("collection.empty", _EXT, "empty a collection", "[1, 2] -> []"),
        _op("lambda.none", _EXT, "make a lambda return None", "lambda x: x -> None"),
        _op("fstring.expr", _ALL, "blank an f-string field", "f'{name}' -> f'{''}'"),
        _op("loop.break_continue", _ALL, "swap break / continue", "break -> continue"),
        _op("decorator.remove", _ALL, "remove a decorator", "@cache -> (nothing)"),
    )
}


def expand_operators(patterns: Iterable[str], where: str) -> frozenset[str]:
    """Expand operator ids and globs into concrete ids.

    Arguments:
        patterns: Ids such as ``cmp.negate`` or globs such as ``cmp.*``.
        where: Description of the setting, used in error messages.

    Returns:
        The matching operator ids.

    Raises:
        ConfigError: If a pattern matches no operator.
    """
    found: set[str] = set()
    for pattern in patterns:
        matched = [
            identifier
            for identifier in OPERATORS
            if fnmatch.fnmatchcase(identifier, pattern)
        ]
        if not matched:
            raise ConfigError(
                f"{where}: unknown mutation operator {pattern!r}; "
                f"known operators: {', '.join(sorted(OPERATORS))}"
            )
        found.update(matched)
    return frozenset(found)


def operators_at(level: Level) -> frozenset[str]:
    """Operators included in a tier.

    Arguments:
        level: The tier.

    Returns:
        Every operator whose own tier is at or below it.
    """
    rank = _LEVEL_RANK[level]
    return frozenset(
        info.id for info in OPERATORS.values() if _LEVEL_RANK[info.level] <= rank
    )


def resolve_operators(
    level: Level, enable: Iterable[str], disable: Iterable[str], where: str
) -> frozenset[str]:
    """Compute an enabled operator set.

    Arguments:
        level: Base tier.
        enable: Ids or globs added to the tier.
        disable: Ids or globs removed afterwards.
        where: Description of the setting, used in error messages.

    Returns:
        The enabled operator ids.

    Raises:
        ConfigError: If an id or glob matches no operator.
    """
    enabled = set(operators_at(level))
    enabled |= expand_operators(enable, f"{where}.enable")
    enabled -= expand_operators(disable, f"{where}.disable")
    return frozenset(enabled)


def parse_level(value: str, where: str) -> Level:
    """Parse a tier name.

    Arguments:
        value: ``core``, ``extended`` or ``all``.
        where: Description of the setting, used in error messages.

    Returns:
        The tier.

    Raises:
        ConfigError: If the name is not a tier.
    """
    try:
        return Level(value)
    except ValueError:
        choices = ", ".join(level.value for level in Level)
        raise ConfigError(f"{where}: {value!r} is not one of {choices}") from None
