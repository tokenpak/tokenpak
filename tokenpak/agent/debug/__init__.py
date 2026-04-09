"""tokenpak.agent.debug — backward-compat shim.

DebugLogger and DebugState have moved to tokenpak.infrastructure.debug.
This module re-exports them for backward compatibility.
"""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.debug is deprecated, use tokenpak.infrastructure.debug instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.infrastructure.debug import DebugLogger, DebugState

__all__ = ["DebugLogger", "DebugState"]
