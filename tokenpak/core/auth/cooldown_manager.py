"""tokenpak.core.auth.cooldown_manager — backward-compat shim.

CooldownManager and BackgroundCooldownClearer have moved to tokenpak.core.cooldown.
"""

from tokenpak.core.cooldown import CooldownManager, BackgroundCooldownClearer, HIGH_ERROR_THRESHOLD

__all__ = ["CooldownManager", "BackgroundCooldownClearer", "HIGH_ERROR_THRESHOLD"]
