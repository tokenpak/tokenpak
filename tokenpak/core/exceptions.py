"""tokenpak.exceptions — backward-compat shim.

All exception classes have moved to tokenpak.core.error_handling.
"""

from tokenpak.core.error_handling import (
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
