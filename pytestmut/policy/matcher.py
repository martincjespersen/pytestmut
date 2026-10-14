"""Assign every piece of code to exactly one policy.

Resolution order: an inline ``policy=`` tag on the nearest enclosing
definition, then the first ``[[policy]]`` in file order whose path and symbol
selectors match, then the default policy.
"""

from __future__ import annotations

import fnmatch

from pytestmut.config.model import Config, PolicyConfig
from pytestmut.source.discovery import path_matches


def symbol_matches(qualname: str, patterns: tuple[str, ...]) -> bool:
    """Tell whether a definition is selected by symbol globs.

    A glob selects a definition and everything nested in it, so ``Invoice``
    covers ``Invoice.total``.

    Arguments:
        qualname: Dotted name of the innermost enclosing definition.
        patterns: Qualified-name globs.

    Returns:
        True when the name or any enclosing name matches a glob.
    """
    parts = qualname.split(".") if qualname else []
    prefixes = [".".join(parts[: count + 1]) for count in range(len(parts))]
    return any(
        fnmatch.fnmatchcase(prefix, pattern)
        for pattern in patterns
        for prefix in prefixes
    )


class PolicyMatcher:
    """Resolves the policy for a location in the code.

    Arguments:
        config: The run configuration.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    def known(self, name: str) -> bool:
        """Tell whether a policy name exists."""
        return any(policy.name == name for policy in self._config.all_policies)

    def resolve(self, relpath: str, scope: str, tagged: str | None) -> PolicyConfig:
        """Find the policy for a location.

        Arguments:
            relpath: POSIX path of the file relative to the project root.
            scope: Qualified name of the enclosing definition.
            tagged: Policy named by an inline tag, if any.

        Returns:
            The policy that applies.
        """
        if tagged is not None and self.known(tagged):
            return self._config.policy(tagged)
        for policy in self._config.policies:
            if policy.paths and not path_matches(relpath, policy.paths):
                continue
            if policy.symbols and not symbol_matches(scope, policy.symbols):
                continue
            return policy
        return self._config.default_policy
