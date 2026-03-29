"""tokenpak.infrastructure — infrastructure layer.

Consolidates config, debug, state management, version checking,
license management, auth helpers, and error handling.
"""

from tokenpak.infrastructure.error_handling import (
    TokenPakError,
    ConfigError,
    AuthError,
    AuthenticationError,
    RateLimitError,
    CacheError,
    ValidationError,
    LicenseError,
    CompressionError,
    UpstreamError,
    CircuitOpenError,
    InternalError,
    format_error,
)
from tokenpak.infrastructure.state_manager import *  # noqa: F401,F403
from tokenpak.infrastructure.version_check import *  # noqa: F401,F403
from tokenpak.infrastructure.debug import DebugLogger, DebugState

__all__ = [
    "TokenPakError",
    "ConfigError",
    "AuthError",
    "AuthenticationError",
    "RateLimitError",
    "CacheError",
    "ValidationError",
    "LicenseError",
    "CompressionError",
    "UpstreamError",
    "CircuitOpenError",
    "InternalError",
    "format_error",
    "DebugLogger",
    "DebugState",
]
