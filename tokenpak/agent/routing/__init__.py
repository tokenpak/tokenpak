
import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.routing is deprecated, use tokenpak.routing instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ['fallback']
