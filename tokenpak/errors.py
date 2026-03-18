"""
User-facing error messages for TokenPak.

Provides clear, actionable error messages for all user-facing error paths:
- Config validation errors (field-specific, with valid values)
- Proxy startup errors (port conflicts, permissions, dependencies)
- LiteLLM integration errors (with retry/fallback guidance)
- Network/API errors (with retry suggestions)
- CLI errors (with command suggestions)

All errors wrap internal exceptions to prevent raw stack traces.
Each error includes: what went wrong, why, and what to do.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional


class TokenPakError(Exception):
    """Base exception for all TokenPak user-facing errors."""

    def __init__(self, message: str, suggestion: str = "", code: int = 1):
        self.message = message
        self.suggestion = suggestion
        self.code = code
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"❌ {self.message}"
        if self.suggestion:
            msg += f"\n   → {self.suggestion}"
        return msg

    def exit(self) -> None:
        """Print error and exit with code."""
        print(self._format_message(), file=sys.stderr)
        sys.exit(self.code)


class ConfigError(TokenPakError):
    """Configuration validation error."""

    def __init__(
        self,
        field: str,
        expected: str,
        actual: Any,
        reason: str,
        suggestion: str,
    ):
        self.field = field
        self.expected = expected
        self.actual = actual
        self.reason = reason
        message = f"Config error: {field}\n   Expected: {expected}\n   Got: {actual}\n   Reason: {reason}"
        super().__init__(message, suggestion)


class ProxyStartupError(TokenPakError):
    """Proxy failed to start."""

    def __init__(self, reason: str, suggestion: str):
        message = f"Proxy startup failed: {reason}"
        super().__init__(message, suggestion, code=2)


class PortInUseError(ProxyStartupError):
    """Port is already bound."""

    def __init__(self, port: int, pid: Optional[int] = None):
        reason = f"Port {port} is already in use"
        if pid:
            reason += f" (process {pid})"
        suggestion = (
            f"Either:\n"
            f"  • Kill the existing proxy: pkill -f 'tokenpak serve'\n"
            f"  • Use a different port: TOKENPAK_PORT={port+1} tokenpak start"
        )
        super().__init__(reason, suggestion)


class PermissionDeniedError(ProxyStartupError):
    """Permission denied for required operation."""

    def __init__(self, operation: str, path: str):
        reason = f"Permission denied: {operation} at {path}"
        suggestion = f"Check directory permissions: ls -ld {path}\nOr run with appropriate privileges."
        super().__init__(reason, suggestion)


class MissingDependencyError(ProxyStartupError):
    """Required Python dependency not installed."""

    def __init__(self, packages: List[str]):
        missing = ", ".join(packages)
        reason = f"Missing packages: {missing}"
        suggestion = f"Install with: pip install {' '.join(packages)}"
        super().__init__(reason, suggestion)


class LiteLLMError(TokenPakError):
    """LiteLLM proxy integration error."""

    def __init__(self, reason: str, suggestion: str):
        message = f"LiteLLM error: {reason}"
        super().__init__(message, suggestion)


class APIError(TokenPakError):
    """External API call failed."""

    def __init__(
        self,
        api_name: str,
        status_code: Optional[int],
        reason: str,
        suggestion: str,
    ):
        if status_code:
            message = f"{api_name} returned {status_code}: {reason}"
        else:
            message = f"{api_name} error: {reason}"
        super().__init__(message, suggestion)


class NetworkError(APIError):
    """Network connectivity error."""

    def __init__(self, operation: str, reason: str):
        suggestion = (
            "Check your network connection and try again.\n"
            "If the issue persists, the service may be experiencing downtime."
        )
        super().__init__("Network", None, f"{operation} failed: {reason}", suggestion)


class TimeoutError(APIError):
    """Request timed out."""

    def __init__(self, api_name: str, timeout_seconds: float):
        suggestion = (
            f"Increase the timeout and retry:\n"
            f"  • Set TOKENPAK_TIMEOUT={int(timeout_seconds * 1.5)}\n"
            f"  • Check if {api_name} is responding: curl -I https://api.{api_name.lower()}.com"
        )
        super().__init__(api_name, 408, "request timeout", suggestion)


class ValidationError(TokenPakError):
    """Request validation error."""

    def __init__(self, field: str, reason: str, suggestion: str):
        message = f"Invalid {field}: {reason}"
        super().__init__(message, suggestion)


class CLIError(TokenPakError):
    """Command-line interface error."""

    def __init__(self, reason: str, suggestion: str = ""):
        super().__init__(reason, suggestion)


class UnknownCommandError(CLIError):
    """User entered an unknown command."""

    def __init__(self, command: str, suggestions: Optional[List[str]] = None):
        message = f"Unknown command: '{command}'"
        if suggestions:
            suggestion = f"Did you mean:\n   • " + "\n   • ".join(suggestions)
        else:
            suggestion = "Run `tokenpak help` to see all available commands."
        super().__init__(message, suggestion)


# ─────────────────────────────────────────────────────────────────────────
# Error Factories
# ─────────────────────────────────────────────────────────────────────────


def wrap_json_error(status: int, detail: str) -> Dict[str, Any]:
    """Convert an error to JSON response format."""
    return {
        "error": {
            "status": status,
            "message": detail,
            "timestamp": __import__("time").time(),
        }
    }


def wrap_http_exception(status: int, detail: str) -> Exception:
    """Convert to FastAPI HTTPException-compatible dict."""
    try:
        from fastapi import HTTPException
        return HTTPException(status_code=status, detail=detail)
    except ImportError:
        # Fallback if FastAPI not available
        return Exception(detail)
