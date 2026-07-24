# SPDX-License-Identifier: Apache-2.0
"""infrastructure.error_handling — Consolidated error/exception hierarchy.

Consolidates exceptions.py (clean hierarchy) and errors.py (structured error codes)
into a single module.

Primary hierarchy (from exceptions.py):
    TokenPakError
    ├── ProxyError          — HTTP proxy errors
    │   ├── UpstreamError   — Provider/upstream API errors
    │   └── CircuitOpenError — Circuit breaker is open
    ├── CompressionError    — Compression failures
    ├── ConfigError         — Config validation/loading errors
    ├── AuthError           — Authentication / API key errors
    ├── RateLimitError      — Rate limit exceeded
    ├── CacheError          — Cache read/write failures
    ├── ValidationError     — Input validation failures
    └── LicenseError        — License validation errors

Structured error codes (from errors.py):
    TP-E0xx: Config errors
    TP-E1xx: Connection/network errors
    TP-E2xx: Authentication errors
    TP-E3xx: Rate limiting errors
    TP-E4xx: Cache errors
    TP-E5xx: Provider (upstream) errors
    TP-E6xx: Internal/system errors
"""

from __future__ import annotations

from typing import Optional, cast

# ---------------------------------------------------------------------------
# Base exception (from exceptions.py — richer interface)
# ---------------------------------------------------------------------------


class TokenPakError(Exception):
    """Base class for all TokenPak exceptions.

    Attributes:
        message: Human-readable error description.
        detail: Optional machine-readable detail (dict or str).
        error_type: Short identifier for the error type (defaults to class name).
    """

    def __init__(
        self,
        message: str,
        detail: object = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.error_type = error_type or self.__class__.__name__

    @property
    def code(self) -> Optional[str]:
        """Structured error code (TP-Exxx), derived from class error_code."""
        return getattr(self.__class__, "error_code", None)

    @property
    def suggestion(self) -> str:
        """User-facing suggestion for resolving the error."""
        if isinstance(self.detail, dict):
            return cast(
                str,
                self.detail.get("suggestion", "Check TokenPak logs for details."),
            )
        return "Check TokenPak logs for details."

    @property
    def context(self) -> Optional[str]:
        """Optional context string for debugging."""
        if isinstance(self.detail, dict):
            return cast(Optional[str], self.detail.get("context"))
        return None

    def to_dict(self) -> dict[str, object]:
        """Return structured error response dict for API responses."""
        error: dict[str, object] = {
            "type": self.error_type,
            "message": self.message,
        }
        if self.detail is not None:
            error["detail"] = self.detail
        return {"error": error}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r})"


# Alias for backward compat with code/suggestion-style usage
TokenPakWarning = TokenPakError


# ---------------------------------------------------------------------------
# Proxy errors
# ---------------------------------------------------------------------------


class ProxyError(TokenPakError):
    """HTTP proxy operation failed."""


class UpstreamError(ProxyError):
    """Upstream provider returned an error response."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        provider: str | None = None,
        detail: object = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.status_code = status_code
        self.provider = provider

    def to_dict(self) -> dict[str, object]:
        d = super().to_dict()
        error = cast(dict[str, object], d["error"])
        if self.status_code is not None:
            error["status_code"] = self.status_code
        if self.provider is not None:
            error["provider"] = self.provider
        return d


class CircuitOpenError(ProxyError):
    """Circuit breaker is open — requests blocked until cooldown expires."""

    def __init__(
        self,
        provider: str,
        retry_after: float | None = None,
        detail: object = None,
    ) -> None:
        msg = f"Circuit breaker open for provider '{provider}'"
        if retry_after is not None:
            msg += f" (retry after {retry_after:.0f}s)"
        super().__init__(msg, detail=detail)
        self.provider = provider
        self.retry_after = retry_after


class SpendGuardBlocked(ProxyError):
    """Request held by TIP Spend Guard before provider send. (TP-ESG01)

    Recoverable — the caller can release with Yes/No or ``[TIP: allow=once]``.
    See standards/29-spend-guard-agent-contract.md for the structured-error
    contract agents must honor.
    """

    error_code = "TP-ESG01"

    def __init__(
        self,
        message: str = "TIP Spend Guard blocked this request before provider send.",
        *,
        pending_id: str | None = None,
        projected_cost_usd: float | None = None,
        projected_tokens: int | None = None,
        threshold_hit: str | None = None,
    ) -> None:
        detail = {
            "pending_id": pending_id,
            "projected_cost_usd": projected_cost_usd,
            "projected_tokens": projected_tokens,
            "threshold_hit": threshold_hit,
            "retryable": True,
            "recovery_status": "user_action_required",
        }
        super().__init__(message, detail=detail, error_type="tokenpak_spend_guard_blocked")


class SpendGuardHardBlocked(ProxyError):
    """Hard-block ceiling exceeded — cannot be released. (TP-ESG02)"""

    error_code = "TP-ESG02"

    def __init__(
        self,
        message: str = "TIP Spend Guard hard-blocked this request.",
        *,
        projected_cost_usd: float | None = None,
        projected_tokens: int | None = None,
        threshold_hit: str | None = None,
    ) -> None:
        detail = {
            "projected_cost_usd": projected_cost_usd,
            "projected_tokens": projected_tokens,
            "threshold_hit": threshold_hit,
            "retryable": False,
            "recovery_status": "terminally_blocked",
        }
        super().__init__(message, detail=detail, error_type="tokenpak_spend_guard_hard_blocked")


class ProxyStartupError(ProxyError):
    """Error during proxy server startup. (TP-E100)"""

    error_code = "TP-E100"

    def __init__(
        self,
        message: str,
        suggestion: Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        detail = {}
        if suggestion:
            detail["suggestion"] = suggestion
        if context:
            detail["context"] = context
        super().__init__(message, detail=detail or None)


class PortInUseError(ProxyStartupError):
    """Proxy port is already in use. (TP-E100)"""

    def __init__(self, port: int) -> None:
        super().__init__(
            message=f"Port {port} is already in use",
            suggestion=f"Use a different port or stop the process using port {port}.",
            context=f"port={port}",
        )


class PermissionDeniedError(ProxyStartupError):
    """Insufficient permissions for proxy operation."""

    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(
            message=message,
            suggestion="Run with appropriate permissions or check file ownership.",
        )


class MissingDependencyError(ProxyStartupError):
    """Required dependency not installed."""

    def __init__(self, dependency: str) -> None:
        super().__init__(
            message=f"Missing required dependency: {dependency}",
            suggestion=f"Install it: pip install {dependency}",
            context=f"dependency={dependency}",
        )


# ---------------------------------------------------------------------------
# Compression errors
# ---------------------------------------------------------------------------


class CompressionError(TokenPakError):
    """Context compression or decompression failed."""


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


class ConfigError(TokenPakError):
    """Configuration is invalid or could not be loaded. (TP-E001)"""

    error_code = "TP-E001"

    def __init__(
        self,
        message: str,
        config_path: str | None = None,
        detail: object = None,
        suggestion: Optional[str] = None,
    ) -> None:
        _detail = detail
        if config_path or suggestion:
            if isinstance(detail, dict):
                _detail = dict(detail)
            else:
                _detail = {}
            if config_path:
                _detail["config_path"] = config_path
            if suggestion:
                _detail["suggestion"] = suggestion
        super().__init__(message, detail=_detail)
        self.config_path = config_path


class ConfigValidationError(ConfigError):
    """Config validation failed. (TP-E002)"""

    error_code = "TP-E002"

    def __init__(self, field: str, reason: str, suggestion: Optional[str] = None) -> None:
        msg = f"Invalid config field '{field}': {reason}"
        sug = suggestion or f"Check the value of '{field}' in your config file."
        super().__init__(msg, suggestion=sug)
        self.field = field


class MissingConfigError(ConfigError):
    """Required config field is missing. (TP-E003)"""

    error_code = "TP-E003"

    def __init__(self, field: str) -> None:
        msg = f"Required config field missing: '{field}'"
        sug = f'Add "{field}" to your TokenPak config file.'
        super().__init__(msg, suggestion=sug)


class InvalidConfigFileError(ConfigError):
    """Config file is invalid JSON or doesn't exist. (TP-E004)"""

    error_code = "TP-E004"

    def __init__(self, filepath: str, reason: str) -> None:
        msg = f"Invalid config file '{filepath}': {reason}"
        sug = f"Check that {filepath} is valid JSON."
        super().__init__(msg, config_path=filepath, suggestion=sug)


# ---------------------------------------------------------------------------
# Auth errors
# ---------------------------------------------------------------------------


class AuthError(TokenPakError):
    """Authentication failed — missing or invalid API key / token. (TP-E201)"""

    error_code = "TP-E201"


class AuthenticationError(AuthError):
    """Alias for AuthError (structured version)."""

    pass


class InvalidAPIKeyError(AuthError):
    """API key is invalid or expired. (TP-E202)"""

    error_code = "TP-E202"

    def __init__(self, provider: str) -> None:
        msg = f"Invalid or expired API key for {provider}"
        super().__init__(
            msg, detail={"suggestion": f"Check your {provider} API key in the TokenPak config."}
        )


class MissingAPIKeyError(AuthError):
    """API key is missing. (TP-E203)"""

    error_code = "TP-E203"

    def __init__(self, provider: str) -> None:
        msg = f"Missing API key for {provider}"
        sug = f'Add your {provider} API key to the "api_keys" section of TokenPak config.'
        super().__init__(msg, detail={"suggestion": sug})


# ---------------------------------------------------------------------------
# Rate limit errors
# ---------------------------------------------------------------------------


class RateLimitError(TokenPakError):
    """Rate limit exceeded. (TP-E301)"""

    error_code = "TP-E301"

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        provider: str | None = None,
        detail: object = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.retry_after = retry_after
        self.provider = provider


# ---------------------------------------------------------------------------
# Cache errors
# ---------------------------------------------------------------------------


class CacheError(TokenPakError):
    """Cache read or write operation failed. (TP-E401)"""

    error_code = "TP-E401"


class CacheCorruptedError(CacheError):
    """Cache data is corrupted. (TP-E402)"""

    error_code = "TP-E402"

    def __init__(self) -> None:
        super().__init__(
            "Cache data is corrupted",
            detail={"suggestion": "Clear your cache and retry: `tokenpak cache clear`"},
        )


# ---------------------------------------------------------------------------
# Connection errors
# ---------------------------------------------------------------------------


class NetworkConnectionError(TokenPakError):
    """Network connection failed. (TP-E101)"""

    error_code = "TP-E101"


class ProviderConnectionError(NetworkConnectionError):
    """Failed to connect to provider. (TP-E102)"""

    error_code = "TP-E102"

    def __init__(self, provider: str, reason: str) -> None:
        msg = f"Failed to connect to {provider}: {reason}"
        sug = f"Check that {provider} is reachable and your config is correct."
        super().__init__(msg, detail={"suggestion": sug, "provider": provider})


class RequestTimeoutError(NetworkConnectionError):
    """Request or connection timed out. (TP-E103)"""

    error_code = "TP-E103"

    def __init__(self, service: str, timeout_seconds: int) -> None:
        msg = f"Request to {service} timed out after {timeout_seconds}s"
        sug = f"Increase timeout or check {service} availability."
        super().__init__(msg, detail={"suggestion": sug})


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class ValidationError(TokenPakError):
    """Input validation failed. (TP-E601)"""

    error_code = "TP-E601"

    def __init__(
        self,
        message: str,
        field: str | None = None,
        detail: object = None,
        suggestion: Optional[str] = None,
    ) -> None:
        _detail = detail
        if field or suggestion:
            if isinstance(detail, dict):
                _detail = dict(detail)
            else:
                _detail = {}
            if field:
                _detail["field"] = field
            if suggestion:
                _detail["suggestion"] = suggestion
        super().__init__(message, detail=_detail)
        self.field = field


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------


class ProviderError(TokenPakError):
    """Upstream provider error. (TP-E501)"""

    error_code = "TP-E501"

    def __init__(self, provider: str, status_code: int, reason: str) -> None:
        msg = f"{provider} returned error {status_code}: {reason}"
        super().__init__(msg, detail={"provider": provider, "status_code": status_code})


class ProviderUnknownError(TokenPakError):
    """Provider name is not recognized by TokenPak. (TP-E502)"""

    error_code = "TP-E502"

    def __init__(self, provider: str) -> None:
        super().__init__(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# License errors
# ---------------------------------------------------------------------------


class LicenseError(TokenPakError):
    """License is invalid, expired, or insufficient. (TP-E700)"""

    error_code = "TP-E700"

    def __init__(
        self,
        message: str,
        required_tier: str | None = None,
        current_tier: str | None = None,
        detail: object = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.required_tier = required_tier
        self.current_tier = current_tier


# ---------------------------------------------------------------------------
# Integration errors
# ---------------------------------------------------------------------------


class LiteLLMError(TokenPakError):
    """Error from LiteLLM integration. (TP-E501)"""

    error_code = "TP-E501"


# ---------------------------------------------------------------------------
# CLI errors
# ---------------------------------------------------------------------------


class CLIError(TokenPakError):
    """CLI-specific error. (TP-E602)"""

    error_code = "TP-E602"

    def __init__(
        self,
        message: str,
        suggestion: Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        detail: dict[str, str] = {}
        if suggestion:
            detail["suggestion"] = suggestion
        if context:
            detail["context"] = context
        super().__init__(message, detail=detail or None)


class UnknownCommandError(CLIError):
    """Unknown CLI command."""

    def __init__(self, command: str, suggestion: Optional[str] = None) -> None:
        super().__init__(
            message=f"Unknown command: '{command}'",
            suggestion=suggestion or "Run 'tokenpak help' for available commands.",
            context=f"command={command}",
        )


# ---------------------------------------------------------------------------
# Internal errors
# ---------------------------------------------------------------------------


class InternalError(TokenPakError):
    """Internal TokenPak error. (TP-E601)"""

    error_code = "TP-E601"


class TokenPakNotImplementedError(InternalError):
    """Feature is not yet implemented. (TP-E602)"""

    error_code = "TP-E602"

    def __init__(self, feature: str) -> None:
        sug = f"Enable {feature} support or use a supported alternative."
        super().__init__(f"{feature} is not yet implemented", detail={"suggestion": sug})


NotImplementedError = TokenPakNotImplementedError  # noqa: A001


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def format_error(exc: Exception) -> str:
    """Format an exception as a user-friendly error message (never raw traceback)."""
    if isinstance(exc, TokenPakError):
        return str(exc)
    return str(InternalError(f"Unexpected error: {type(exc).__name__}"))


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------

__all__ = [
    # Base
    "TokenPakError",
    "TokenPakWarning",
    # Proxy
    "ProxyError",
    "UpstreamError",
    "CircuitOpenError",
    "ProxyStartupError",
    "PortInUseError",
    "PermissionDeniedError",
    "MissingDependencyError",
    # Compression
    "CompressionError",
    # Config
    "ConfigError",
    "ConfigValidationError",
    "MissingConfigError",
    "InvalidConfigFileError",
    # Auth
    "AuthError",
    "AuthenticationError",
    "InvalidAPIKeyError",
    "MissingAPIKeyError",
    # Rate limit
    "RateLimitError",
    # Cache
    "CacheError",
    "CacheCorruptedError",
    # Network
    "NetworkConnectionError",
    "ProviderConnectionError",
    "RequestTimeoutError",
    # Validation
    "ValidationError",
    # Provider
    "ProviderError",
    "ProviderUnknownError",
    # License
    "LicenseError",
    # Integration
    "LiteLLMError",
    # CLI
    "CLIError",
    "UnknownCommandError",
    # Internal
    "InternalError",
    "TokenPakNotImplementedError",
    # Utility
    "format_error",
    # Aliases
    "TimeoutError",
    "NotImplementedError",
]

# TimeoutError alias — tests import this name from this module
TimeoutError = RequestTimeoutError  # noqa: A001
