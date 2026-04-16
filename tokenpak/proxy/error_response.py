"""Canonical upstream error response normalization.

When an upstream provider returns 4xx or 5xx, the proxy wraps the response
in a consistent envelope regardless of the provider:

    {
        "error": {
            "type": str,           # e.g. "authentication_error", "rate_limit_error"
            "message": str,        # human-readable, extracted from provider body
            "provider": str,       # e.g. "anthropic", "openai", "google"
            "upstream_status": int # HTTP status code from upstream
        }
    }

Provider-specific formats audited:
    - Anthropic:   {"type": "error", "error": {"type": "...", "message": "..."}}
    - OpenAI/Grok: {"error": {"message": "...", "type": "...", "code": ...}}
    - Google:      {"error": {"code": N, "message": "...", "status": "..."}}
    - Passthrough: arbitrary — falls back to generic message
    - Embedding adapters (Voyage, Jina, Ollama): arbitrary JSON — generic fallback

All formats are normalized to the canonical envelope defined above.
"""

from __future__ import annotations

import json
from typing import Optional


def _extract_message_from_provider_body(body: bytes, provider: str) -> Optional[str]:
    """Try to extract a human-readable error message from a provider-specific body."""
    try:
        data = json.loads(body)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    # Anthropic: {"type": "error", "error": {"type": "...", "message": "..."}}
    if provider == "anthropic":
        err = data.get("error", {})
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])

    # OpenAI / Grok / Azure: {"error": {"message": "...", "type": "...", "code": ...}}
    if provider in ("openai", "groq", "azure"):
        err = data.get("error", {})
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])

    # Google / Gemini: {"error": {"code": N, "message": "...", "status": "..."}}
    if provider == "google":
        err = data.get("error", {})
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])

    # Generic fallback: try nested error.message, then top-level message
    err = data.get("error", {})
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if isinstance(err, str) and err:
        return err
    if isinstance(data.get("message"), str) and data["message"]:
        return data["message"]

    return None


def _status_to_error_type(status_code: int) -> str:
    """Map an HTTP status code to a canonical error type string."""
    mapping = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        429: "rate_limit_error",
        500: "upstream_error",
        502: "upstream_error",
        503: "service_unavailable",
        504: "upstream_timeout",
    }
    if status_code in mapping:
        return mapping[status_code]
    if 400 <= status_code < 500:
        return "client_error"
    if 500 <= status_code < 600:
        return "upstream_error"
    return "upstream_error"


def normalize_upstream_error(status_code: int, body: bytes, provider: str) -> bytes:
    """Wrap an upstream provider error response in the canonical error envelope.

    Args:
        status_code: HTTP status code returned by the upstream provider.
        body: Raw response body bytes from the upstream provider.
        provider: Canonical provider name (e.g. "anthropic", "openai", "google").

    Returns:
        UTF-8 JSON bytes with canonical error structure.
    """
    error_type = _status_to_error_type(status_code)
    message = _extract_message_from_provider_body(body, provider)
    if not message:
        message = f"Upstream provider '{provider}' returned HTTP {status_code}."

    envelope = {
        "error": {
            "type": error_type,
            "message": message,
            "provider": provider,
            "upstream_status": status_code,
        }
    }
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")
