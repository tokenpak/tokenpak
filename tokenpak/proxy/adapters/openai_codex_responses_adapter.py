"""OpenAI Codex Responses API adapter — routes to chatgpt.com/backend-api.

Extends the standard OpenAI Responses adapter with:
- Default upstream: chatgpt.com/backend-api (ChatGPT OAuth endpoint)
- Detection: /v1/responses with JWT bearer token (not sk- API key)
- Same wire format as openai-responses (Responses API)
- Path rewrite: /v1/responses → /codex/responses (ChatGPT backend path)
- Payload fixup: ensures stream=true, store=false, strips max_output_tokens
- Prompt cache: deterministic prompt_cache_key from stable prefix
  (instructions + tools), prompt_cache_retention="24h"

This enables TokenPak to proxy Codex subscription traffic with full
compression pipeline support (capsules, vault injection, compaction, etc.)
while routing to the correct ChatGPT backend instead of api.openai.com.

Requires: curl_cffi (pip install curl_cffi) for Cloudflare bypass on
chatgpt.com. Falls back to urllib3 if curl_cffi is unavailable, but
chatgpt.com requests will likely get 403'd by CF in that case.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional

from .openai_responses_adapter import OpenAIResponsesAdapter
from .canonical import CanonicalRequest


# ---------------------------------------------------------------------------
# ChatGPT OAuth tokens are JWTs (start with "eyJ").
# OpenAI API keys start with "sk-".
# This heuristic lets us auto-detect which upstream to use without
# requiring any user configuration — zero-config, just works.
# ---------------------------------------------------------------------------

def _is_chatgpt_oauth_token(auth_header: str) -> bool:
    """Return True if the Authorization header carries a ChatGPT OAuth JWT."""
    if not auth_header:
        return False
    # Strip "Bearer " prefix
    token = auth_header
    lower = auth_header.lower()
    if lower.startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        return False
    # ChatGPT OAuth tokens are JWTs: base64url header.payload.signature
    # They always start with "eyJ" (base64 of '{"')
    # API keys start with "sk-"
    return token.startswith("eyJ") and "." in token


# ---------------------------------------------------------------------------
# Deterministic prompt_cache_key generation for prefix_auto caching.
#
# Key format follows the TokenPak spec:
#   tokenpak:{model}:{instructions_hash}:{tools_hash}
#
# Maps to the spec's {tenant}:{workflow}:{prompt_bundle_version}:{toolset_version}
# ---------------------------------------------------------------------------

def _canonicalize(value: Any) -> bytes:
    """Canonical byte representation — same logic as prefix_registry.canonicalize."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash16(value: Any) -> str:
    """First 16 hex chars of SHA-256 over canonical representation."""
    return hashlib.sha256(_canonicalize(value)).hexdigest()[:16]


def _normalize_tools_for_hash(tools: Optional[List[Dict[str, Any]]]) -> str:
    """Normalize and hash tool schemas — sorted by name, recursive key sort."""
    if not tools:
        return "notool"
    normalized = sorted(
        (json.dumps(t, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for t in tools),
    )
    combined = "[" + ",".join(normalized) + "]"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def build_codex_cache_key(
    model: str,
    instructions: str,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build a deterministic prompt_cache_key for Codex prefix_auto caching.

    Format: tokenpak:{model}:{instructions_hash}:{tools_hash}

    Identical instructions + tools always produce the same key, maximizing
    prefix cache hit rates on the OpenAI backend. Changing instructions or
    tools bumps the version naturally (different hash → different key).
    """
    instructions_hash = _hash16(instructions) if instructions else "noinst"
    tools_hash = _normalize_tools_for_hash(tools)
    return f"tokenpak:{model}:{instructions_hash}:{tools_hash}"


# Minimum instruction length (chars) to justify 24h retention.
# Very short instructions don't benefit from extended cache retention.
_MIN_INSTRUCTIONS_FOR_RETENTION = 50


class OpenAICodexResponsesAdapter(OpenAIResponsesAdapter):
    """Codex Responses adapter — same format, different upstream + detection.

    Key differences from standard OpenAI Responses:
    - Upstream: chatgpt.com/backend-api (not api.openai.com)
    - Path: /codex/responses (not /v1/responses)
    - Requires: stream=true, store=false, no max_output_tokens
    - Prompt caching: generates deterministic prompt_cache_key from
      stable prefix (instructions + tools), sets retention="24h"
    - Uses curl_cffi for Cloudflare bypass
    """

    source_format = "openai-codex-responses"

    # The ChatGPT backend path for Codex
    CODEX_PATH = "/codex/responses"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Match Codex requests by path or by JWT auth.

        Three match cases:
        1. Bare ``/codex/responses`` — sent by OpenClaw / clients that use
           the chatgpt.com backend path directly. The proxy injects the
           ChatGPT OAuth token, so we can't rely on the client's auth header.
        2. ``/v1/codex/responses`` — explicit Codex namespace via the
           standard ``/v1`` prefix.
        3. ``/v1/responses`` *with* a ChatGPT OAuth JWT in the Authorization
           header — distinguishes Codex traffic from regular OpenAI API
           traffic on the shared ``/v1/responses`` endpoint.
        """
        if "/codex/responses" in path or "/v1/codex/responses" in path:
            return True
        if "/v1/responses" not in path:
            return False
        # /v1/responses with a ChatGPT OAuth JWT → Codex
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        return _is_chatgpt_oauth_token(auth)

    def get_default_upstream(self) -> str:
        return "https://chatgpt.com/backend-api"

    def get_sse_format(self) -> str:
        return "openai-responses-sse"

    def get_upstream_path(self, original_path: str = "") -> str:
        """Return the correct path for the ChatGPT Codex backend.

        Rewrites /v1/responses... or /v1/codex/responses... → /codex/responses...,
        preserving any trailing resource ID or sub-path.
        """
        # /v1/codex/responses... → /codex/responses...
        _CODEX_PREFIX = "/v1/codex/responses"
        if original_path.startswith(_CODEX_PREFIX):
            return self.CODEX_PATH + original_path[len(_CODEX_PREFIX):]
        # /v1/responses... → /codex/responses...
        _PREFIX = "/v1/responses"
        if original_path.startswith(_PREFIX):
            return self.CODEX_PATH + original_path[len(_PREFIX):]
        return self.CODEX_PATH

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        """Denormalize with ChatGPT Codex constraints applied.

        The ChatGPT Codex backend requires:
        - instructions: always present (even if empty string)
        - stream: true (always)
        - store: false (always)
        - no max_output_tokens parameter
        - input as a list (not string)

        Additionally applies prefix_auto caching:
        - prompt_cache_key: deterministic hash of instructions + tools
        - prompt_cache_retention: "24h" for substantial instructions
        """
        # Use parent denormalize to get the base payload
        base_bytes = super().denormalize(canonical)
        payload = json.loads(base_bytes)

        # Apply ChatGPT Codex constraints
        payload["stream"] = True
        payload["store"] = False  # required by chatgpt.com backend

        # Codex backend requires instructions to be present
        if "instructions" not in payload:
            payload["instructions"] = ""

        # Remove unsupported parameters
        payload.pop("max_output_tokens", None)

        # Ensure input is always a list
        if isinstance(payload.get("input"), str):
            text = payload["input"]
            if text:
                payload["input"] = [
                    {"role": "user", "content": [{"type": "input_text", "text": text}]}
                ]
            else:
                payload["input"] = []

        # --- Prompt prefix caching (prefix_auto) ---
        # Generate deterministic cache key from stable prefix only if
        # the client didn't already provide one.
        if "prompt_cache_key" not in payload:
            payload["prompt_cache_key"] = build_codex_cache_key(
                model=payload.get("model", "codex"),
                instructions=payload.get("instructions", ""),
                tools=payload.get("tools"),
            )
        # chatgpt.com backend does not support prompt_cache_retention — strip it
        payload.pop("prompt_cache_retention", None)

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
