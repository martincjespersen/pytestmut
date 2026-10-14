"""Built-in suppression rules.

Each rule names a kind of code where mutants are almost always noise
(docstrings, annotations, logging) or dangerous (arguments of calls that
delete files or talk to the network). A suppressed candidate is still
recorded, with the rule's reason code, so nothing disappears silently.
"""

from __future__ import annotations

import ast
import fnmatch
import sys

from pytestmut.config.model import SuppressConfig
from pytestmut.mutation.model import Site
from pytestmut.source.scopes import ModuleContext

HAZARDOUS_CALLS = (
    "shutil.rmtree",
    "shutil.move",
    "shutil.copy*",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "os.rename",
    "os.renames",
    "os.replace",
    "os.truncate",
    "os.chmod",
    "os.chown",
    "os.kill",
    "os.killpg",
    "os.system",
    "os.popen",
    "os.exec*",
    "os.spawn*",
    "os.startfile",
    "subprocess.*",
    "*.unlink",
    "*.rmdir",
    "*.rmtree",
    "requests.*",
    "httpx.*",
    "aiohttp.*",
    "urllib.request.*",
    "urllib3.*",
    "socket.*",
    "smtplib.*",
    "*.sendmail",
    "*.send_message",
    "boto3.*",
    "*.execute",
    "*.executemany",
    "*.executescript",
)
TUNABLE_KEYWORDS = (
    "timeout",
    "*_timeout",
    "timeout_*",
    "retries",
    "*_retries",
    "max_*",
    "*_size",
    "maxsize",
    "delay",
    "*_delay",
    "interval",
    "*_interval",
    "ttl",
    "*_ttl",
    "workers",
    "*_workers",
    "backoff",
    "*_backoff",
)
_SLEEPS = frozenset({"time.sleep", "asyncio.sleep"})
_CLEANUP_METHODS = frozenset(
    {
        "close",
        "flush",
        "release",
        "join",
        "shutdown",
        "terminate",
        "kill",
        "wait",
        "stop",
        "disconnect",
    }
)
_LOG_METHODS = frozenset(
    {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
        "fatal",
        "log",
    }
)
_LOG_OBJECTS = frozenset({"logging", "logger", "log"})
_OUTPUT_CALLS = frozenset({"print", "warnings.warn", "pprint.pprint", "pprint"})
_TYPING_CALLS = frozenset(
    {"TypeVar", "NewType", "cast", "TypedDict", "NamedTuple", "ParamSpec"}
)
_PLATFORM_NAMES = frozenset(
    {
        "sys.version_info",
        "sys.platform",
        "os.name",
        "platform.system",
        "sys.implementation",
    }
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_TEXT_OPERATORS = ("str.", "fstring.")


def _matches(name: str, globs: tuple[str, ...]) -> bool:
    tail = "?." + name.rpartition(".")[2]
    return any(
        fnmatch.fnmatchcase(name, glob) or fnmatch.fnmatchcase(tail, glob)
        for glob in globs
    )


class BuiltinRules:
    """Decides whether a candidate mutation falls under a built-in rule.

    Arguments:
        ctx: The parsed module.
        config: Suppression settings.
    """

    def __init__(self, ctx: ModuleContext, config: SuppressConfig) -> None:
        self._ctx = ctx
        self._off = frozenset(config.builtin_disable)
        self._hazards = HAZARDOUS_CALLS + config.hazardous_calls
        self._tunables = TUNABLE_KEYWORDS + config.tunable_keywords

    def reason(self, site: Site) -> str | None:
        """Find the rule that suppresses a candidate.

        Arguments:
            site: The candidate mutation.

        Returns:
            The reason code of the first matching rule that is switched on,
            or None when the candidate should become a mutant.
        """
        for code in self._reasons(site):
            if code not in self._off:
                return code
        return None

    def _reasons(self, site: Site) -> list[str]:
        ctx, node = self._ctx, site.node
        found: list[str] = []
        if sys.version_info < (3, 12) and self._inside(node, ast.JoinedStr):
            return ["fstring-pre312"]
        if self._is_docstring(node):
            found.append("docstring")
        if site.operator == "stmt.call_remove" and isinstance(node, ast.Expr):
            call = node.value
            if isinstance(call, ast.Call):
                if self._is_logging(call):
                    found.append("logging")
                if (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr in _CLEANUP_METHODS
                ):
                    found.append("cleanup-call")
        if self._is_tunable(node):
            found.append("tunable")
        if isinstance(node, ast.Compare) and self._is_platform_check(node):
            found.append("platform-check")
        scope_name = ctx.scope_of(node).rpartition(".")[2]
        if scope_name == "__repr__":
            found.append("repr")
        child: ast.AST = node
        for ancestor, via in ctx.ancestors(node):
            found.extend(self._ancestor_reasons(site, child, ancestor, via))
            child = ancestor
        return found

    def _ancestor_reasons(
        self, site: Site, child: ast.AST, ancestor: ast.AST, via: str
    ) -> list[str]:
        ctx = self._ctx
        found: list[str] = []
        if via in {"annotation", "returns"} or type(ancestor).__name__ == "TypeAlias":
            found.append("annotation")
        if isinstance(ancestor, ast.Call):
            name = ctx.dotted_name(ancestor.func)
            if via in {"args", "keywords"}:
                if name.rpartition(".")[2] in _TYPING_CALLS:
                    found.append("annotation")
                if self._is_logging(ancestor):
                    found.append("logging")
                if _matches(name, self._hazards):
                    found.append("hazard")
        if isinstance(ancestor, (ast.If, ast.While)) and via == "test":
            if self._guards_hazard(ancestor):
                found.append("hazard-guard")
        if isinstance(ancestor, ast.If):
            if self._is_type_checking(ancestor.test):
                found.append("type-checking")
            if self._is_main_guard(ancestor.test):
                found.append("main-guard")
        if isinstance(ancestor, ast.Compare) and self._is_platform_check(ancestor):
            found.append("platform-check")
        if isinstance(ancestor, _FUNCTIONS):
            if via == "decorator_list" and site.operator != "decorator.remove":
                found.append("decorator")
            if any(
                ctx.dotted_name(item).rpartition(".")[2] == "overload"
                for item in ancestor.decorator_list
            ):
                found.append("type-checking")
        if isinstance(ancestor, ast.ClassDef) and via == "decorator_list":
            if site.operator != "decorator.remove":
                found.append("decorator")
        if isinstance(ancestor, (ast.Assign, ast.AnnAssign)):
            targets = (
                ancestor.targets
                if isinstance(ancestor, ast.Assign)
                else [ancestor.target]
            )
            if any(
                isinstance(target, ast.Name)
                and target.id.startswith("__")
                and target.id.endswith("__")
                for target in targets
            ):
                found.append("module-metadata")
        if isinstance(ancestor, ast.Assert):
            found.append("assert")
        if isinstance(ancestor, ast.Raise) and site.operator.startswith(
            _TEXT_OPERATORS
        ):
            found.append("exception-message")
        return found

    def _inside(self, node: ast.AST, kind: type[ast.AST]) -> bool:
        if isinstance(node, kind):
            return True
        return any(isinstance(anc, kind) for anc, _ in self._ctx.ancestors(node))

    def _is_docstring(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (str, bytes))
            and isinstance(self._ctx.parent(node), ast.Expr)
        )

    def _is_logging(self, call: ast.Call) -> bool:
        name = self._ctx.dotted_name(call.func)
        if name in _OUTPUT_CALLS:
            return True
        base, _, method = name.rpartition(".")
        owner = base.rpartition(".")[2].strip("_").lower()
        return method in _LOG_METHODS and owner in _LOG_OBJECTS

    def _is_tunable(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Constant):
            return False
        ctx = self._ctx
        parent = ctx.parent(node)
        if isinstance(parent, ast.UnaryOp):
            node, parent = parent, ctx.parent(parent)
        if isinstance(parent, ast.keyword) and parent.arg:
            return _matches(parent.arg, self._tunables)
        if isinstance(parent, ast.Call) and ctx.field_in_parent(node) == "args":
            return ctx.dotted_name(parent.func) in _SLEEPS
        if isinstance(parent, ast.arguments):
            return _matches(self._default_name(parent, node), self._tunables)
        return False

    @staticmethod
    def _default_name(arguments: ast.arguments, default: ast.AST) -> str:
        positional = [*arguments.posonlyargs, *arguments.args]
        offset = len(positional) - len(arguments.defaults)
        for index, value in enumerate(arguments.defaults):
            if value is default:
                return positional[offset + index].arg
        for argument, value in zip(arguments.kwonlyargs, arguments.kw_defaults):
            if value is default:
                return argument.arg
        return ""

    def _guards_hazard(self, statement: ast.If | ast.While) -> bool:
        for child in [*statement.body, *statement.orelse]:
            if hasattr(child, "body"):
                continue
            for item in ast.walk(child):
                if isinstance(item, ast.Call) and _matches(
                    self._ctx.dotted_name(item.func), self._hazards
                ):
                    return True
        return False

    @staticmethod
    def _is_type_checking(test: ast.expr) -> bool:
        if isinstance(test, ast.Name):
            return test.id == "TYPE_CHECKING"
        return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"

    @staticmethod
    def _is_main_guard(test: ast.expr) -> bool:
        if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
            return False
        sides = [test.left, test.comparators[0]]
        names = [side.id for side in sides if isinstance(side, ast.Name)]
        values = [side.value for side in sides if isinstance(side, ast.Constant)]
        return names == ["__name__"] and values == ["__main__"]

    def _is_platform_check(self, compare: ast.Compare) -> bool:
        for item in ast.walk(compare):
            if isinstance(item, ast.Attribute):
                if self._ctx.dotted_name(item) in _PLATFORM_NAMES:
                    return True
        return False
