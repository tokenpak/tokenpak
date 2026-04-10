"""tokenpak._internal.auth.cooldown_manager — backward-compat shim.

CooldownManager and BackgroundCooldownClearer have moved to tokenpak.infrastructure.cooldown.
"""

from tokenpak.infrastructure.cooldown import CooldownManager, BackgroundCooldownClearer, HIGH_ERROR_THRESHOLD

__all__ = ["CooldownManager", "BackgroundCooldownClearer", "HIGH_ERROR_THRESHOLD"]
