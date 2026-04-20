"""
TokenPak OAuth Flow Support
============================

Handles OAuth Bearer token routing for subscription-based providers,
primarily OpenAI Codex (subscription OAuth) and Anthropic Claude Code
(OAuth session tokens).

SECURITY GUARANTEES (same as passthrough.py):
  - ZERO LOGGING of token values
  - ZERO STORAGE of OAuth tokens
  - Tokens treated as opaque pass-through strings
  - Pattern detection operates on format shape only, not token content

Auth Type Detection
-------------------
TokenPak distinguishes two auth flavors carried in ``Authorization: Bearer``:

1. **API Key** — ``sk-...`` or ``sk-ant-...`` prefix
   - Static, long-lived keys
   - Safe to cache keyed by key prefix (never full value)
   - Typical providers: Anthropic API, OpenAI API

2. **OAuth Bearer** — JWT / opaque token (non-``sk-`` prefix)
   - Session-scoped, may expire mid-session
   - Responses MUST NOT be shared across OAuth sessions
   - Typical providers: Codex subscription, Claude Code OAuth, Google Workspace

Codex OAuth Endpoint
--------------------
OpenAI Codex subscription uses the OpenAI API endpoint (api.openai.com).
Requests use ``Authorization: Bearer <oauth_token>`` instead of
``Authorization: Bearer sk-<api_key>``.

Routing Logic
-------------
1. Request has ``Authorization: Bearer sk-...``  → API key, route normally
2. Request has ``Authorization: Bearer <non-sk>`` → OAuth token
   a. Path ``/v1/messages`` or ``x-api-key``      → Anthropic OAuth (Claude Code)
   b. Path ``/v1/chat/completions`` or ``/v1/responses`` → OpenAI OAuth (Codex)
   c. Model body contains ``codex``                → Codex OAuth
3. ``x-api-key`` present                          → Anthropic API key (no OAuth)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict

# ---------------------------------------------------------------------------
# Auth type constants
# ---------------------------------------------------------------------------

AUTH_TYPE_APIKEY = "apikey"  # Static API key (sk-...)
AUTH_TYPE_OAUTH = "oauth"  # OAuth Bearer token (JWT / opaque)
AUTH_TYPE_NONE = "none"  # No auth header


# ---------------------------------------------------------------------------
# Codex-specific endpoint and model detection
# ---------------------------------------------------------------------------

# OpenAI Codex models — subscription OAuth uses the same api.openai.com
# endpoint but with a Bearer OAuth token instead of an API key.
#
# Responses API (newer, preferred for Codex models):
CODEX_RESPONSES_PATH = "/v1/responses"
# Chat Completions API (also supported):
CODEX_CHAT_PATH = "/v1/chat/completions"

# Model name patterns that indicate a Codex subscription model
_CODEX_MODEL_RE = re.compile(
    r"\b(gpt-[0-9]+[\.\-][0-9]*[-]codex|codex|gpt-5\.[0-9]+[-]codex[-\w]*)\b",
    re.IGNORECASE,
)

# Bearer token shape patterns (format only — never log values)
# API keys start with "sk-" (OpenAI) or "sk-ant-" (Anthropic)
_API_KEY_PREFIX_RE = re.compile(r"^sk-", re.IGNORECASE)

# JWT tokens have three base64-URL segments separated by dots
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


# ---------------------------------------------------------------------------
# OAuthRequest — detected auth context for a single request
# ---------------------------------------------------------------------------


@dataclass
class OAuthContext:
    """
    Auth context detected for a single request.

    Attributes
    ----------
    auth_type : str
        "apikey", "oauth", or "none"
    is_codex : bool
        True when request targets a Codex/subscription model
    is_oauth_anthropic : bool
        True when OAuth token is used with an Anthropic endpoint
        (Claude Code subscription mode)
    skip_cache_keying : bool
        True when responses should NOT be cached across sessions.
        Always True for OAuth tokens (token expiry invalidates cache keys).
    token_format : str
        Shape hint: "jwt", "opaque", "apikey", "unknown"
        Never contains actual token value.
    """

    auth_type: str = AUTH_TYPE_NONE
    is_codex: bool = False
    is_oauth_anthropic: bool = False
    skip_cache_keying: bool = False
    token_format: str = "unknown"


# ---------------------------------------------------------------------------
# Core detection functions
# ---------------------------------------------------------------------------


def detect_auth_type(headers: Dict[str, str]) -> str:
    """
    Determine auth type from request headers.

    Returns AUTH_TYPE_APIKEY, AUTH_TYPE_OAUTH, or AUTH_TYPE_NONE.

    SECURITY: Operates on format shape only. Never logs token values.
    """
    lower = {k.lower(): v for k, v in headers.items()}

    # x-api-key is always a static API key (Anthropic format)
    if "x-api-key" in lower:
        val = lower["x-api-key"].strip()
        if val:
            return AUTH_TYPE_APIKEY

    auth = lower.get("authorization", "").strip()
    if not auth:
        return AUTH_TYPE_NONE

    # Must be "Bearer <token>"
    if not auth.lower().startswith("bearer "):
        return AUTH_TYPE_NONE

    token = auth[7:].strip()  # strip "Bearer "
    if not token:
        return AUTH_TYPE_NONE

    # API key: starts with "sk-"
    if _API_KEY_PREFIX_RE.match(token):
        return AUTH_TYPE_APIKEY

    # Everything else is treated as OAuth
    return AUTH_TYPE_OAUTH


def detect_token_format(headers: Dict[str, str]) -> str:
    """
    Detect the shape of the Bearer token without inspecting its value.

    Returns "jwt", "opaque", "apikey", or "unknown".
    SECURITY: Only examines format (dots, length, base64 chars) — never logs value.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    auth = lower.get("authorization", "").strip()
    if not auth.lower().startswith("bearer "):
        return "unknown"

    token = auth[7:].strip()
    if not token:
        return "unknown"

    if _API_KEY_PREFIX_RE.match(token):
        return "apikey"

    # Check for JWT structure (header.payload.signature)
    if _JWT_RE.match(token):
        return "jwt"

    # Long opaque token (e.g. UUIDv4, random hex, base64)
    if len(token) >= 32:
        return "opaque"

    return "unknown"


def is_codex_model(model: str) -> bool:
    """Return True when the model name indicates a Codex subscription model."""
    return bool(_CODEX_MODEL_RE.search(model))


def analyze_request(
    path: str,
    headers: Dict[str, str],
    model: str = "",
) -> OAuthContext:
    """
    Analyze a request and return an OAuthContext describing its auth posture.

    Parameters
    ----------
    path : str
        Request path (e.g. "/v1/messages", "/v1/chat/completions")
    headers : dict
        Request headers (case-insensitive)
    model : str
        Model name extracted from body (optional)

    Returns
    -------
    OAuthContext
        Auth context with type, Codex flag, and cache guidance.
    """
    auth_type = detect_auth_type(headers)
    token_format = detect_token_format(headers)

    # Detect Codex via model name
    codex_by_model = is_codex_model(model)

    # Detect Anthropic OAuth (Claude Code subscription)
    # Anthropic OAuth uses Bearer token but hits /v1/messages
    lower_path = path.lower()
    is_anthropic_path = "/v1/messages" in lower_path or "/messages" in lower_path
    lower_headers = {k.lower(): v for k, v in headers.items()}
    has_anthropic_version = "anthropic-version" in lower_headers

    is_oauth_anthropic = auth_type == AUTH_TYPE_OAUTH and (
        is_anthropic_path or has_anthropic_version
    )

    # Codex = OAuth + OpenAI path OR model name contains codex
    is_codex = codex_by_model or (
        auth_type == AUTH_TYPE_OAUTH
        and not is_oauth_anthropic
        and ("/chat/completions" in lower_path or "/responses" in lower_path or not lower_path)
    )

    # OAuth tokens should never share cache keys across sessions
    skip_cache_keying = auth_type == AUTH_TYPE_OAUTH

    return OAuthContext(
        auth_type=auth_type,
        is_codex=is_codex,
        is_oauth_anthropic=is_oauth_anthropic,
        skip_cache_keying=skip_cache_keying,
        token_format=token_format,
    )


# ---------------------------------------------------------------------------
# Telemetry helpers (safe for logging — no credential values)
# ---------------------------------------------------------------------------


def oauth_telemetry_tags(ctx: OAuthContext) -> Dict[str, str]:
    """
    Return telemetry tags safe for logging/tracing.

    SECURITY: Contains NO token values or credential material.
    """
    tags: Dict[str, str] = {
        "auth_type": ctx.auth_type,
        "token_format": ctx.token_format,
        "skip_cache": str(ctx.skip_cache_keying).lower(),
    }
    if ctx.is_codex:
        tags["provider_variant"] = "codex"
    if ctx.is_oauth_anthropic:
        tags["provider_variant"] = "claude-code-oauth"
    return tags
