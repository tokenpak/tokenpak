"""
TokenPak error handling infrastructure.

All user-facing errors should:
1. Have an error code (TP-Exxx format)
2. Include human-readable message
3. Provide actionable fix suggestion
4. Never show raw Python tracebacks

Error codes:
- TP-E0xx: Config errors
- TP-E1xx: Connection/network errors
- TP-E2xx: Authentication errors
- TP-E3xx: Rate limiting errors
- TP-E4xx: Cache errors
- TP-E5xx: Provider (upstream) errors
- TP-E6xx: Internal/system errors
"""

from typing import Optional


class TokenPakError(Exception):
    """Base class for all TokenPak errors."""

    def __init__(
        self,
        code: str,
        message: str,
        suggestion: Optional[str] = None,
        context: Optional[str] = None,
    ):
        self.code = code
        self.message = message
        self.suggestion = suggestion or "Check TokenPak logs for details."
        self.context = context

    def __str__(self) -> str:
        """Return human-readable error message."""
        lines = [f"{self.code}: {self.message}"]
        if self.context:
            lines.append(f"  Context: {self.context}")
        lines.append(f"  Fix: {self.suggestion}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return error as dict (for JSON responses)."""
        return {
            "error_code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "context": self.context,
        }


# Config Errors (TP-E0xx)
class ConfigError(TokenPakError):
    """Base class for config errors."""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__("TP-E001", message, suggestion)


class ConfigValidationError(ConfigError):
    """Config validation failed."""

    def __init__(self, field: str, reason: str, suggestion: Optional[str] = None):
        code = "TP-E002"
        msg = f"Invalid config field '{field}': {reason}"
        sug = suggestion or f"Check the value of '{field}' in your config file."
        super().__init__(msg, sug)
        self.code = code
        self.field = field


class MissingConfigError(ConfigError):
    """Required config field is missing."""

    def __init__(self, field: str):
        code = "TP-E003"
        msg = f"Required config field missing: '{field}'"
        sug = f'Add "{field}" to your TokenPak config file.'
        super().__init__(msg, sug)
        self.code = code


class InvalidConfigFileError(ConfigError):
    """Config file is invalid JSON or doesn't exist."""

    def __init__(self, filepath: str, reason: str):
        code = "TP-E004"
        msg = f"Invalid config file '{filepath}': {reason}"
        sug = f"Check that {filepath} is valid JSON."
        super().__init__(msg, sug)
        self.code = code


# Connection/Network Errors (TP-E1xx)
class ConnectionError(TokenPakError):
    """Base class for connection errors."""

    def __init__(
        self, message: str, suggestion: Optional[str] = None, context: Optional[str] = None
    ):
        super().__init__("TP-E101", message, suggestion, context)


class ProviderConnectionError(ConnectionError):
    """Failed to connect to provider."""

    def __init__(self, provider: str, reason: str):
        code = "TP-E102"
        msg = f"Failed to connect to {provider}: {reason}"
        sug = f"Check that {provider} is reachable and your config is correct."
        error = ConnectionError(msg, sug)
        error.code = code
        return None  # Don't return, just set attrs
        # Actually, need to restructure...


class TimeoutError(ConnectionError):
    """Request or connection timed out."""

    def __init__(self, service: str, timeout_seconds: int):
        code = "TP-E103"
        msg = f"Request to {service} timed out after {timeout_seconds}s"
        sug = f"Increase timeout or check {service} availability."
        super().__init__(msg, sug)
        self.code = code


# Authentication Errors (TP-E2xx)
class AuthenticationError(TokenPakError):
    """Base class for authentication errors."""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__("TP-E201", message, suggestion)


class InvalidAPIKeyError(AuthenticationError):
    """API key is invalid or expired."""

    def __init__(self, provider: str):
        code = "TP-E202"
        msg = f"Invalid or expired API key for {provider}"
        sug = f"Check your {provider} API key in the TokenPak config."
        super().__init__(msg, sug)
        self.code = code


class MissingAPIKeyError(AuthenticationError):
    """API key is missing."""

    def __init__(self, provider: str):
        code = "TP-E203"
        msg = f"Missing API key for {provider}"
        sug = f'Add your {provider} API key to the "api_keys" section of TokenPak config.'
        super().__init__(msg, sug)
        self.code = code


# Rate Limiting Errors (TP-E3xx)
class RateLimitError(TokenPakError):
    """Request rate limit exceeded."""

    def __init__(self, provider: str, retry_after_seconds: Optional[int] = None):
        code = "TP-E301"
        msg = f"Rate limit exceeded for {provider}"
        if retry_after_seconds:
            msg += f" (retry in {retry_after_seconds}s)"
        sug = f"Wait before retrying, or increase your {provider} rate limit quota."
        super().__init__(code, msg, sug)


# Cache Errors (TP-E4xx)
class CacheError(TokenPakError):
    """Cache operation failed."""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__("TP-E401", message, suggestion or "Check cache configuration.")


class CacheCorruptedError(CacheError):
    """Cache data is corrupted."""

    def __init__(self):
        code = "TP-E402"
        msg = "Cache data is corrupted"
        sug = "Clear your cache and retry: `tokenpak cache clear`"
        super().__init__(msg, sug)
        self.code = code


# Provider Errors (TP-E5xx)
class ProviderError(TokenPakError):
    """Upstream provider error."""

    def __init__(self, provider: str, status_code: int, reason: str):
        code = "TP-E501"
        msg = f"{provider} returned error {status_code}: {reason}"
        sug = "Check provider status page and retry."
        super().__init__(code, msg, sug, context=f"{provider} {status_code}")


class ProviderUnknownError(ProviderError):
    """Unknown provider error."""

    def __init__(self, provider: str):
        code = "TP-E502"
        msg = f"Unknown error from {provider}"
        sug = f"Check {provider} status page or TokenPak logs."
        TokenPakError.__init__(self, code, msg, sug)


# Internal Errors (TP-E6xx)
class InternalError(TokenPakError):
    """Internal TokenPak error."""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__("TP-E601", message, suggestion or "Check TokenPak logs.")


class NotImplementedError(InternalError):
    """Feature is not implemented."""

    def __init__(self, feature: str):
        code = "TP-E602"
        msg = f"Feature not yet implemented: {feature}"
        sug = f"Check TokenPak documentation or GitHub issues for {feature}."
        super().__init__(msg, sug)
        self.code = code


# Error handler utility
def format_error(exc: Exception) -> str:
    """
    Format an exception as a user-friendly error message.
    Returns the formatted string, never raw traceback.
    """
    if isinstance(exc, TokenPakError):
        return str(exc)
    else:
        # Wrap unknown exceptions
        error = InternalError(
            f"Unexpected error: {type(exc).__name__}",
            "Check TokenPak logs for details.",
        )
        return str(error)


# ---------------------------------------------------------------------------
# Proxy errors (from Sue's implementation)
# ---------------------------------------------------------------------------

class ProxyStartupError(TokenPakError):
    """Error during proxy server startup."""
    def __init__(self, message: str, suggestion: Optional[str] = None, context: Optional[str] = None):
        super().__init__(code="TP-E100", message=message, suggestion=suggestion or "Check proxy configuration.", context=context)


class PortInUseError(ProxyStartupError):
    """Proxy port is already in use."""
    def __init__(self, port: int):
        super().__init__(
            message=f"Port {port} is already in use",
            suggestion=f"Use a different port: tokenpak start --port <PORT>, or stop the process using port {port}.",
            context=f"port={port}",
        )


class PermissionDeniedError(ProxyStartupError):
    """Insufficient permissions for proxy operation."""
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message=message, suggestion="Run with appropriate permissions or check file ownership.")


class MissingDependencyError(ProxyStartupError):
    """Required dependency not installed."""
    def __init__(self, dependency: str):
        super().__init__(
            message=f"Missing required dependency: {dependency}",
            suggestion=f"Install it: pip install {dependency}",
            context=f"dependency={dependency}",
        )


# ---------------------------------------------------------------------------
# Integration errors
# ---------------------------------------------------------------------------

class LiteLLMError(TokenPakError):
    """Error from LiteLLM integration."""
    def __init__(self, message: str, suggestion: Optional[str] = None, context: Optional[str] = None):
        super().__init__(code="TP-E501", message=message, suggestion=suggestion or "Check LiteLLM configuration.", context=context)


# ---------------------------------------------------------------------------
# Validation and CLI errors
# ---------------------------------------------------------------------------

class ValidationError(TokenPakError):
    """General validation error (non-config)."""
    def __init__(self, message: str, suggestion: Optional[str] = None, context: Optional[str] = None):
        super().__init__(code="TP-E601", message=message, suggestion=suggestion or "Check input values.", context=context)


class CLIError(TokenPakError):
    """CLI-specific error."""
    def __init__(self, message: str, suggestion: Optional[str] = None, context: Optional[str] = None):
        super().__init__(code="TP-E602", message=message, suggestion=suggestion or "Run 'tokenpak help' for usage.", context=context)


class UnknownCommandError(CLIError):
    """Unknown CLI command."""
    def __init__(self, command: str, suggestion: Optional[str] = None):
        super().__init__(
            message=f"Unknown command: '{command}'",
            suggestion=suggestion or "Run 'tokenpak help' for available commands.",
            context=f"command={command}",
        )
