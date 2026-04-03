"""tokenpak._internal.auth — authentication and API key management.

Note: Full implementation requires tokenpak.infrastructure.cooldown.
"""

try:
    from .cooldown_manager import CooldownManager
    from .oauth_manager import OAuthManager
    __all__ = ["CooldownManager", "OAuthManager"]
except ImportError:
    __all__ = []
