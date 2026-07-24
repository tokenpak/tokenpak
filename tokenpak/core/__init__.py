"""infrastructure — Cross-cutting concerns: debug, licensing, error handling, state."""

import os as _os

# Directory containing this package's source files.
# Transferred from monolith (TPK-CONSOLIDATION-A2a, line 61).
# Useful for path resolution relative to the installed package location.
_SCRIPT_DIR: str = _os.path.dirname(_os.path.abspath(__file__))

from .debug import DebugLogger, DebugState
from .error_handling import TokenPakError, TokenPakWarning
from .state_manager import StateManager
from .version_check import run_startup_check

__all__ = [
    "DebugLogger",
    "DebugState",
    "StateManager",
    "run_startup_check",
    "TokenPakError",
    "TokenPakWarning",
    "cooldown",
    "debug",
    "error_handling",
    "startup_validator",
    "state_manager",
    "version_check",
]
