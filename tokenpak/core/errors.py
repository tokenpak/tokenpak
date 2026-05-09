"""tokenpak.errors — backward-compat shim.

All error classes have moved to tokenpak.core.error_handling.
"""

from tokenpak.core.error_handling import (
    AuthenticationError,
    AuthError,
    CacheCorruptedError,
    CacheError,
    CLIError,
    ConfigError,
    ConfigValidationError,
    InternalError,
    InvalidAPIKeyError,
    InvalidConfigFileError,
    LiteLLMError,
    MissingAPIKeyError,
    MissingConfigError,
    MissingDependencyError,
    NotImplementedError,  # noqa: A001
    PermissionDeniedError,
    PortInUseError,
    ProviderConnectionError,
    ProviderError,
    ProviderUnknownError,
    ProxyStartupError,
    RateLimitError,
    TokenPakError,
    TokenPakNotImplementedError,
    UnknownCommandError,
    ValidationError,
    format_error,
)
from tokenpak.core.error_handling import (
    NetworkConnectionError as ConnectionError,
)
from tokenpak.core.error_handling import (
    RequestTimeoutError as TimeoutError,
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
    "AuthError",
    "AuthenticationError",
    "InvalidAPIKeyError",
    "MissingAPIKeyError",
    "RateLimitError",
    "CacheError",
    "CacheCorruptedError",
    "ProviderError",
    "ProviderUnknownError",
    "InternalError",
    "TokenPakNotImplementedError",
    "NotImplementedError",
    "ProxyStartupError",
    "PortInUseError",
    "PermissionDeniedError",
    "MissingDependencyError",
    "LiteLLMError",
    "ValidationError",
    "CLIError",
    "UnknownCommandError",
    "format_error",
]
