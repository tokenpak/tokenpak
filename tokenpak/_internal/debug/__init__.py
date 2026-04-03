"""tokenpak._internal.debug — internal implementation module."""

try:
    from ._impl import *  # noqa: F401,F403
except ImportError:
    pass

__all__ = []
