"""tokenpak.agent.debug — backward-compat shim.

DebugLogger and DebugState have moved to tokenpak.infrastructure.debug.
This module re-exports them for backward compatibility.
"""

from tokenpak.infrastructure.debug import DebugLogger, DebugState

__all__ = ["DebugLogger", "DebugState"]
