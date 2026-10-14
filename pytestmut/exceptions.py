"""Typed exceptions raised by pytestmut, each carrying its process exit code."""


class PytestmutError(Exception):
    """Base class for all errors raised by this package."""

    exit_code = 4


class ConfigError(PytestmutError):
    """Raised when the configuration or the command line is invalid."""

    exit_code = 2


class BaselineError(PytestmutError):
    """Raised when the unmutated test suite cannot serve as a baseline."""

    exit_code = 3


class ToolFailure(PytestmutError):
    """Raised when pytestmut cannot trust its own results."""

    exit_code = 4


class EmptyScopeError(PytestmutError):
    """Raised when the selected scope contains no runnable mutant."""

    exit_code = 5


class SourceError(PytestmutError):
    """Raised when a source file cannot be read, decoded or parsed."""

    exit_code = 4


class StateDirError(PytestmutError):
    """Raised when the state directory is unsafe to create, use or delete."""

    exit_code = 2


class GitError(PytestmutError):
    """Raised when a git query needed for the run fails."""

    exit_code = 2
