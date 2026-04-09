"""tokenpak.exceptions — backward-compat shim.

All exception classes have moved to tokenpak.infrastructure.error_handling.
"""

from tokenpak.infrastructure.error_handling import (
    TokenPakError,
    ProxyError,
    UpstreamError,
    CircuitOpenError,
    CompressionError,
    ConfigError,
    AuthError,
    RateLimitError,
    CacheError,
    ValidationError,
    LicenseError,
)

__all__ = [
    "TokenPakError",
    "ProxyError",
    "UpstreamError",
    "CircuitOpenError",
    "CompressionError",
    "ConfigError",
    "AuthError",
    "RateLimitError",
    "CacheError",
    "ValidationError",
    "LicenseError",
]
