"""tokenpak.state_manager — backward-compat shim.

StateManager has moved to tokenpak.infrastructure.state_manager.
"""

from tokenpak.infrastructure.state_manager import *  # noqa: F401, F403
from tokenpak.infrastructure.state_manager import StateManager  # explicit for IDE support

__all__ = ["StateManager"]
