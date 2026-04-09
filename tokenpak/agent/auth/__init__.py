"""tokenpak.agent.auth — backward-compat shim.

CooldownManager has moved to tokenpak.infrastructure.cooldown.
"""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.auth is deprecated, use tokenpak.infrastructure.cooldown instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.infrastructure.cooldown import CooldownManager

__all__ = ["CooldownManager"]
