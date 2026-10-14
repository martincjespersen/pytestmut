"""Mutation testing for pytest.

PYTEST_DONT_REWRITE: this package never needs assertion rewriting, and opting
out keeps pytest from warning when it is imported before the plugin loads.
"""

from pytestmut._meta import __version__

__all__ = ["__version__"]
