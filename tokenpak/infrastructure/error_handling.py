"""
TokenPak error handling infrastructure.

Provides a unified exception hierarchy for all TokenPak errors.
All errors inherit from TokenPakError (base), which provides:
  - .message attribute
  - .error_type attribute (defaults to class name)
  - .to_dict() -> {"error": {"type": ..., "message": ..., "detail": ...}}
  - str(e) -> human-readable message
"""

from typing import Any, Dict, Optional


class TokenPakError(Exception):
    """Base class for all TokenPak errors."""

    def __init__(
        self,
        message: str,
        *,
        detail: Optional[Dict[str, Any]] = None,
        error_type: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        self.error_type = error_type or type(self).__name__

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "error": {
                "type": self.error_type,
                "message": self.message,
            }
        }
        if self.detail:
            d["error"]["detail"] = self.detail
        for k, v in self.__dict__.items():
            if k not in ("message", "detail", "error_type", "args") and not k.startswith("_"):
                d["error"][k] = v
        return d


class ProxyError(TokenPakError):
    """Errors originating from the proxy layer."""
    pass


class UpstreamError(ProxyError):
    """Error from upstream provider."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        provider: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.status_code = status_code
        self.provider = provider

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        if self.status_code is not None:
            d["error"]["status_code"] = self.status_code
        if self.provider is not None:
            d["error"]["provider"] = self.provider
        return d


class CircuitOpenError(ProxyError):
    """Circuit breaker is open for a provider."""

    def __init__(
        self,
        provider: str,
        *,
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        msg = f"Circuit open for {provider}"
        if retry_after is not None:
            msg += f"; retry after {retry_after:.0f}s"
        super().__init__(msg, **kwargs)
        self.provider = provider
        self.retry_after = retry_after


class CompressionError(TokenPakError):
    """Error during compression/decompression."""
    pass


class ConfigError(TokenPakError):
    """Configuration error."""

    def __init__(self, message: str, *, config_path: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        self.config_path = config_path


class AuthError(TokenPakError):
    """Authentication/authorization error."""
    pass


# Alias used in some places
AuthenticationError = AuthError


class RateLimitError(TokenPakError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: Optional[float] = None,
        provider: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after
        self.provider = provider


class CacheError(TokenPakError):
    """Cache operation error."""
    pass


class ValidationError(TokenPakError):
    """Input validation error."""

    def __init__(self, message: str, *, field: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        self.field = field


class LicenseError(TokenPakError):
    """License validation error."""

    def __init__(
        self,
        message: str,
        *,
        required_tier: Optional[str] = None,
        current_tier: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.required_tier = required_tier
        self.current_tier = current_tier


class InternalError(TokenPakError):
    """Internal/unexpected error."""
    pass


def format_error(exc: TokenPakError) -> str:
    """Format a TokenPakError as a human-readable string."""
    return str(exc)
