"""tokenpak.errors — backward-compat shim.

All error classes have moved to tokenpak.infrastructure.error_handling.
"""

from tokenpak.infrastructure.error_handling import (
    TokenPakError,
    ConfigError,
    ConfigValidationError,
    MissingConfigError,
    InvalidConfigFileError,
    NetworkConnectionError as ConnectionError,
    ProviderConnectionError,
    RequestTimeoutError as TimeoutError,
    AuthError,
    AuthenticationError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    RateLimitError,
    CacheError,
    CacheCorruptedError,
    ProviderError,
    InternalError,
    ValidationError,
    CLIError,
    UnknownCommandError,
    format_error,
)

__all__ = [
    "TokenPakError",
    "ConfigError",
    "ConfigValidationError",
    "MissingConfigError",
    "InvalidConfigFileError",
    "ConnectionError",
    "ProviderConnectionError",
    "TimeoutError",
    "AuthenticationError",
    "InvalidAPIKeyError",
    "MissingAPIKeyError",
    "RateLimitError",
    "CacheError",
    "CacheCorruptedError",
    "ProviderError",
    "InternalError",
    "ValidationError",
    "CLIError",
    "UnknownCommandError",
    "format_error",
]
