"""tokenpak.agent.auth — backward-compat shim.

CooldownManager has moved to tokenpak.infrastructure.cooldown.
"""

from tokenpak.infrastructure.cooldown import CooldownManager

__all__ = ["CooldownManager"]
