"""Turn one source file into its list of mutants.

Every candidate an operator finds ends up in the result: as a runnable mutant,
or with a status and reason explaining why it will not run. Nothing is dropped
silently, because a tool that quietly generates no mutants reports a perfect
score.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import re
from dataclasses import dataclass

from pytestmut.config.model import Config, PolicyConfig
from pytestmut.exceptions import SourceError
from pytestmut.mutation.model import Mutant, Site, SourceSpan, Splice, Status
from pytestmut.mutation.operators import families_by_node_type
from pytestmut.mutation.registry import OPERATORS
from pytestmut.mutation.rules import BuiltinRules
from pytestmut.mutation.validate import CodeKey, Validator
from pytestmut.policy.matcher import PolicyMatcher, symbol_matches
from pytestmut.source.comments import Directive, DirectiveIndex, parse_directives
from pytestmut.source.discovery import TargetFile, path_matches
from pytestmut.source.scopes import ModuleContext
from pytestmut.source.text import SourceText


@dataclass(frozen=True)
class Issue:
    """Something about a file the user should know.

    Attributes:
        path: POSIX path relative to the project root.
        line: Line the issue is about, or 0.
        message: What is wrong.
        policy: Policy the issue counts against, or empty.
        fatal: True when the run must stop with a configuration error.
    """

    path: str
    line: int
    message: str
    policy: str = ""
    fatal: bool = False


@dataclass(frozen=True)
class FileMutants:
    """Everything generated from one file.

    Attributes:
        relpath: POSIX path relative to the project root.
        sha256: Digest of the file bytes the mutants were built from.
        mutants: All candidates, in source order.
        empty_scopes: Functions that produced no runnable mutant.
        issues: Warnings and errors found while generating.
        error: Why the file could not be processed, if it could not.
    """

    relpath: str
    sha256: str = ""
    mutants: tuple[Mutant, ...] = ()
    empty_scopes: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = ()
    error: str = ""


class Generator:
    """Generates mutants for target files.

    Arguments:
        config: The run configuration.
        matcher: Resolves the policy of each location.
    """

    def __init__(self, config: Config, matcher: PolicyMatcher) -> None:
        self._config = config
        self._matcher = matcher
        self._families = families_by_node_type()
        self._line_patterns = tuple(re.compile(p) for p in config.suppress.lines)
        self._symbols = tuple(
            (item.rpartition("::")[0], item.rpartition("::")[2])
            for item in config.suppress.symbols
        )
        self._enabled = frozenset().union(
            *(policy.operators for policy in config.all_policies)
        )

    def generate(self, target: TargetFile) -> FileMutants:
        """Generate the mutants of one file.

        Arguments:
            target: The file to mutate.

        Returns:
            The candidates with their pre-run statuses; ``error`` is set when
            the file cannot be read or parsed.
        """
        try:
            source = SourceText.from_file(target.path)
            ctx = ModuleContext.parse(source)
        except SourceError as exc:
            return FileMutants(relpath=target.relpath, error=str(exc))
        return _FileRun(self, target.relpath, ctx).run()

    def sites(self, ctx: ModuleContext) -> tuple[list[Site], list[str]]:
        """Collect every candidate the enabled operators find in a module.

        Arguments:
            ctx: The parsed module.

        Returns:
            The candidates in source order, and messages for operators that
            failed on a node.
        """
        found: list[Site] = []
        failures: list[str] = []
        for node in ast.walk(ctx.tree):
            for family in self._families.get(type(node), ()):
                try:
                    found.extend(
                        site
                        for site in family(ctx, node)
                        if site.operator in self._enabled
                    )
                except Exception as exc:  # noqa: BLE001 - one node must not end the run
                    line = getattr(node, "lineno", 0)
                    failures.append(
                        f"line {line}: {family.__name__} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
        found.sort(key=lambda s: (s.start, s.end, s.operator, s.replacement))
        return found, failures

    def config_reason(
        self, relpath: str, scope: str, ctx: ModuleContext, lines: range
    ) -> str | None:
        """Reason code when the configuration suppresses a location.

        Arguments:
            relpath: POSIX path of the file.
            scope: Qualified name of the enclosing definition.
            ctx: The parsed module.
            lines: Lines the mutation touches.

        Returns:
            ``config-line``, ``config-symbol`` or None.
        """
        for line in lines:
            text = ctx.source.line_text(line)
            if any(pattern.search(text) for pattern in self._line_patterns):
                return "config-line"
        for path_glob, symbol_glob in self._symbols:
            if path_glob and not path_matches(relpath, (path_glob,)):
                continue
            if symbol_matches(scope, (symbol_glob,)):
                return "config-symbol"
        return None

    @property
    def config(self) -> Config:
        """The run configuration."""
        return self._config

    @property
    def matcher(self) -> PolicyMatcher:
        """The policy matcher."""
        return self._matcher


class _FileRun:
    """State for generating the mutants of one file."""

    def __init__(self, generator: Generator, relpath: str, ctx: ModuleContext) -> None:
        self._generator = generator
        self._config = generator.config
        self._relpath = relpath
        self._ctx = ctx
        self._directives: DirectiveIndex = parse_directives(
            ctx, self._config.suppress.foreign_pragmas
        )
        self._rules = BuiltinRules(ctx, self._config.suppress)
        self._validator = Validator(ctx)
        self._issues: list[Issue] = []
        self._occurrences: dict[str, int] = {}
        self._seen: dict[tuple[int, CodeKey], str] = {}
        self._refused: set[tuple[int, str]] = set()

    def run(self) -> FileMutants:
        self._check_directives()
        sites, failures = self._generator.sites(self._ctx)
        self._issues.extend(Issue(self._relpath, 0, message) for message in failures)
        mutants = [mutant for mutant in map(self._build, sites) if mutant is not None]
        self._report_unused()
        runnable = [mutant.scope for mutant in mutants if mutant.runnable]
        empty = tuple(
            scope.qualname
            for scope in self._ctx.scopes
            if scope.kind == "function"
            and not any(
                name == scope.qualname or name.startswith(scope.qualname + ".")
                for name in runnable
            )
        )
        return FileMutants(
            relpath=self._relpath,
            sha256=self._ctx.source.sha256,
            mutants=tuple(mutants),
            empty_scopes=empty,
            issues=tuple(self._issues),
        )

    def _check_directives(self) -> None:
        for line, message in self._directives.problems:
            self._issues.append(Issue(self._relpath, line, message, fatal=True))
        for directive in self._directives.directives:
            for glob in directive.operators:
                if not any(fnmatch.fnmatchcase(name, glob) for name in OPERATORS):
                    self._issues.append(
                        Issue(
                            self._relpath,
                            directive.line,
                            f"unknown mutation operator {glob!r} in skip comment",
                            fatal=True,
                        )
                    )
        for qualname, name in self._directives.policies.items():
            if not self._generator.matcher.known(name):
                self._issues.append(
                    Issue(
                        self._relpath,
                        0,
                        f"{qualname}: policy={name} names no configured policy",
                        fatal=True,
                    )
                )

    def _report_unused(self) -> None:
        mode = self._config.suppress.report_unused
        if mode == "ignore":
            return
        for directive in self._directives.directives:
            if directive.kind == "inline" and directive.hits == 0:
                if (directive.line, "refused") in self._refused:
                    continue
                self._issues.append(
                    Issue(
                        self._relpath,
                        directive.line,
                        "skip comment suppresses no mutant",
                        fatal=mode == "error",
                    )
                )

    def _build(self, site: Site) -> Mutant | None:
        ctx, source = self._ctx, self._ctx.source
        original = source.text[site.start : site.end]
        if original == site.replacement:
            return None
        scope = ctx.scope_of(site.node)
        policy = self._generator.matcher.resolve(
            self._relpath, scope, self._directives.policy_for(scope)
        )
        if site.operator not in policy.operators:
            return None
        line, col = source.position(site.start)
        end_line, end_col = source.position(max(site.start, site.end))
        static = ctx.is_static(site.node)
        status, reason, duplicate_of, seen_key = self._classify(
            site, policy, scope, range(line, end_line + 1), static
        )
        try:
            splice = Splice(
                start=source.byte_offset(site.start),
                end=source.byte_offset(site.end),
                replacement=source.encode(site.replacement),
            )
        except SourceError:
            splice = Splice(start=0, end=0, replacement=b"")
            status, reason = Status.INVALID, "replacement not encodable"
        base = "\0".join(
            (self._relpath, scope, site.operator, original, site.replacement)
        )
        occurrence = self._occurrences.get(base, 0)
        self._occurrences[base] = occurrence + 1
        identifier = hashlib.sha256(f"{base}\0{occurrence}".encode()).hexdigest()[:12]
        if status is None and seen_key is not None:
            self._seen[seen_key] = identifier
        return Mutant(
            id=identifier,
            path=self._relpath,
            scope=scope,
            operator=site.operator,
            span=SourceSpan(line=line, col=col, end_line=end_line, end_col=end_col),
            splice=splice,
            original=original,
            replacement=site.replacement,
            static=static,
            memoised=ctx.is_memoised(site.node),
            policy=policy.name,
            cover_lines=ctx.cover_lines(site.node),
            pre_status=status,
            reason=reason,
            duplicate_of=duplicate_of,
        )

    def _classify(
        self, site: Site, policy: PolicyConfig, scope: str, lines: range, static: bool
    ) -> tuple[Status | None, str, str | None, tuple[int, CodeKey] | None]:
        reason = self._suppression(site, policy, scope, lines, static)
        if reason is not None:
            return Status.SUPPRESSED, reason, None, None
        verdict, key, statement = self._validator.check(site)
        if verdict == "invalid" or key is None:
            return Status.INVALID, "does not compile", None, None
        if verdict == "equivalent":
            return Status.EQUIVALENT, "compiles to the same bytecode", None, None
        seen_key = (id(statement), key)
        if seen_key in self._seen:
            first = self._seen[seen_key]
            return Status.DUPLICATE, "same bytecode as another mutant", first, None
        return None, "", None, seen_key

    def _suppression(
        self, site: Site, policy: PolicyConfig, scope: str, lines: range, static: bool
    ) -> str | None:
        suppress = self._config.suppress
        builtin = self._rules.reason(site)
        if builtin is not None:
            return builtin
        first, last = lines[0], lines[-1]
        if suppress.honor_no_cover and "no-cover" not in suppress.builtin_disable:
            if self._directives.match(first, last, site.operator, "no-cover"):
                return "no-cover"
        configured = self._generator.config_reason(
            self._relpath, scope, self._ctx, lines
        )
        if configured is not None:
            return configured
        directive = self._directives.match(first, last, site.operator, "inline")
        if directive is not None and self._honoured(directive, policy):
            directive.hits += 1
            return "inline"
        mode = policy.static_mutants or self._config.run.static_mutants
        if static and mode == "skip":
            return "static-skip"
        return None

    def _honoured(self, directive: Directive, policy: PolicyConfig) -> bool:
        thresholds = policy.thresholds
        problem = ""
        if not thresholds.allow_inline_suppressions:
            problem = f"inline skip is not allowed in policy {policy.name!r}"
        elif (
            thresholds.require_reason or self._config.suppress.require_reason
        ) and not directive.reason:
            problem = "inline skip needs a reason: '# pytestmut: skip -- why'"
        if not problem:
            return True
        if (directive.line, "refused") not in self._refused:
            self._refused.add((directive.line, "refused"))
            self._issues.append(
                Issue(self._relpath, directive.line, problem, policy=policy.name)
            )
        return False
