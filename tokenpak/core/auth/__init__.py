"""tokenpak.agent.auth — backward-compat shim.

CooldownManager has moved to tokenpak.core.cooldown.
"""

from tokenpak.core.cooldown import CooldownManager

__all__ = ["CooldownManager", "cooldown_manager", "oauth_manager"]
