"""TokenPak Proxy Cache Pipeline.

Transferred from monolith (packages/core/tokenpak/runtime/proxy.py) as part of
TPK-CONSOLIDATION-A2c.  Contains per-provider cache injection, parsing helpers,
and the Anthropic auto-cache mode selector.

Provides:
- CacheMode enum (auto vs explicit)
- _select_anthropic_cache_mode / _apply_anthropic_auto_cache
- _inject_gemini_cache_ref / _parse_gemini_cached_tokens
- _extract_bedrock_checkpoints / _inject_bedrock_checkpoints
- _extract_cache_hints / _inject_prompt_cache_key
- _parse_bedrock_cached_tokens / _parse_bedrock_cache_creation_tokens
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from typing import TypeAlias

from tokenpak.core.runtime.providers import Provider

JsonValue: TypeAlias = bool | int | float | str | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


# ---------------------------------------------------------------------------
# CACHE-P3-001: Anthropic top-level auto cache mode
# ---------------------------------------------------------------------------


class CacheMode(Enum):
    AUTO = "auto"  # Top-level request-level cache_control (Anthropic auto mode)
    EXPLICIT = "explicit"  # Per-block cache_control markers (existing behaviour)


def _select_anthropic_cache_mode(headers: Mapping[str, str], body_dict: JsonObject) -> CacheMode:
    """Select auto vs explicit cache mode for an Anthropic request.

    Pops 'tokenpak_cache_mode' from body_dict if present — it must not be
    forwarded upstream.  Header takes precedence over body field; both take
    precedence over the conversation-length heuristic.
    """
    mode_hint = headers.get("x-tokenpak-cache-mode") or body_dict.pop("tokenpak_cache_mode", None)
    if mode_hint == "explicit":
        return CacheMode.EXPLICIT
    if mode_hint == "auto":
        return CacheMode.AUTO
    # Default: auto for multi-turn (>2 messages), explicit for short/single-turn
    messages = body_dict.get("messages", [])
    if isinstance(messages, list) and len(messages) > 2:
        return CacheMode.AUTO
    return CacheMode.EXPLICIT


def _apply_anthropic_auto_cache(body_dict: JsonObject) -> None:
    """Apply Anthropic top-level auto cache mode (in-place).

    Strips per-block cache_control markers injected by earlier pipeline stages
    and sets a single top-level cache_control field.  Anthropic automatically
    moves the cache breakpoint to the last cacheable block as the conversation
    grows, making this the preferred mode for multi-turn sessions.

    API reference: top-level ``cache_control: {"type": "ephemeral"}`` on the
    messages endpoint (same response fields as explicit mode —
    cache_creation_input_tokens and cache_read_input_tokens in usage).
    """
    system = body_dict.get("system", [])
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                block.pop("cache_control", None)
    tools = body_dict.get("tools", [])
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                tool.pop("cache_control", None)
    messages = body_dict.get("messages", [])
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block.pop("cache_control", None)
    # anthropic-only — caller guards Provider.ANTHROPIC
    body_dict["cache_control"] = {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Gemini explicit cache reference injection
# ---------------------------------------------------------------------------


def _inject_gemini_cache_ref(provider: Provider, headers: Mapping[str, str], body: bytes) -> bytes:
    """Inject cachedContent reference for Gemini requests.

    Accepts cache ref from:
    - Header: x-tokenpak-cache-ref
    - Body field: tokenpak_cache_object_ref (stripped before forwarding)

    Header takes precedence over body field.
    Only injects for Provider.GEMINI; returns body unchanged for other providers.

    Args:
        provider: The detected LLM provider
        headers: Request headers dict (case-sensitive keys)
        body: Request body bytes

    Returns:
        Modified body bytes with cachedContent injected (if applicable)
    """
    if provider != Provider.GEMINI:
        return body

    if not body:
        return body

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body

    if not isinstance(data, dict):
        return body

    # Get cache ref: header takes precedence over body field
    cache_ref = headers.get("x-tokenpak-cache-ref") or headers.get("X-TokenPak-Cache-Ref")
    body_ref = None

    if "tokenpak_cache_object_ref" in data:
        body_ref = data.pop("tokenpak_cache_object_ref")  # Strip from forwarded body

    final_ref = cache_ref or body_ref

    if not final_ref:
        # No cache ref provided — return body (possibly modified to strip field)
        if body_ref is not None:
            return json.dumps(data).encode()
        return body

    # Inject as Gemini's cachedContent field
    data["cachedContent"] = final_ref

    return json.dumps(data).encode()


def _parse_gemini_cached_tokens(response_data: Mapping[str, object]) -> int:
    """Parse cachedContentTokenCount from Gemini responses.

    Gemini returns cache usage in usageMetadata:
    {
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 50,
            "cachedContentTokenCount": 800  // tokens served from cache
        }
    }

    Args:
        response_data: Parsed JSON response from Gemini

    Returns:
        Number of tokens served from cache (0 if not present)
    """
    usage = response_data.get("usageMetadata", {})
    if not isinstance(usage, dict):
        return 0
    cached_tokens = usage.get("cachedContentTokenCount", 0)
    return cached_tokens if isinstance(cached_tokens, int) else 0


# ---------------------------------------------------------------------------
# Bedrock Cache Checkpoint Support (CACHE-P3-003)
# ---------------------------------------------------------------------------


def _extract_bedrock_checkpoints(body: JsonObject) -> list[int]:
    """Extract and validate checkpoint positions from TokenPak hints.

    Accepts tokenpak_checkpoints field containing array of insertion indices.
    Indices are 0-based and refer to positions in the messages array.
    A checkpoint at index N means: insert cachePoint AFTER message[N].

    Args:
        body: Request body dict (will be modified to strip tokenpak_checkpoints)

    Returns:
        List of valid checkpoint indices, sorted in reverse order (for safe insertion)
    """
    checkpoints = body.pop("tokenpak_checkpoints", [])
    if not isinstance(checkpoints, list):
        return []

    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return []
    max_idx = len(messages) - 1

    if max_idx < 0:
        return []

    # Validate: must be integers, in range [0, max_idx], deduplicate
    valid: list[int] = []
    for cp in checkpoints:
        if isinstance(cp, int) and 0 <= cp <= max_idx:
            valid.append(cp)

    # Sort in reverse order for safe insertion (higher indices first)
    # Deduplicate by converting to set
    return sorted(set(valid), reverse=True)


def _inject_bedrock_checkpoints(provider: Provider, body: bytes) -> bytes:
    """Insert cachePoint blocks at specified positions for Bedrock requests.

    Bedrock uses checkpoint blocks to mark cache boundaries. A cachePoint block
    inserted after message[N] means everything up to and including message[N]
    is eligible for caching.

    Format: {"cachePoint": {"type": "default"}}

    Args:
        provider: Detected LLM provider
        body: Request body bytes

    Returns:
        Modified body bytes with cachePoint blocks inserted (if Bedrock)
    """
    if provider != Provider.BEDROCK:
        return body

    if not body:
        return body

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body

    if not isinstance(data, dict):
        return body

    # Extract checkpoints (also strips tokenpak_checkpoints from body)
    checkpoints = _extract_bedrock_checkpoints(data)

    if not checkpoints:
        # No checkpoints specified, but still need to return body without tokenpak_checkpoints
        return json.dumps(data).encode()

    messages = data.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return json.dumps(data).encode()

    # Insert cachePoint blocks in reverse index order (preserves lower indices)
    for idx in checkpoints:
        # Insert AFTER the message at idx (so at position idx + 1)
        cache_point = {"cachePoint": {"type": "default"}}
        messages.insert(idx + 1, cache_point)

    data["messages"] = messages
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# OpenAI / Azure / Codex / xAI prompt_cache_key Passthrough (CACHE-P2-001)
# ---------------------------------------------------------------------------


def _extract_cache_hints(
    headers: Mapping[str, str], body: JsonObject
) -> tuple[str | None, str | None]:
    """Extract cache key and retention hints from request headers and body.

    Header takes precedence over body field when both are present.
    ``tokenpak_cache_hint`` and ``tokenpak_cache_retention`` are *popped* from
    *body* so they are never forwarded to the upstream provider.

    Args:
        headers: Request headers dict (case-sensitive).
        body: Parsed request body dict — modified in-place to strip fields.

    Returns:
        ``(cache_key, cache_retention)`` — either may be ``None``.
    """
    # Always pop body fields to ensure they are stripped from the forwarded body,
    # regardless of whether a header hint is also present.
    body_key = body.pop("tokenpak_cache_hint", None)
    body_retention = body.pop("tokenpak_cache_retention", None)
    cache_key = headers.get("x-tokenpak-cache-key")
    if cache_key is None and isinstance(body_key, str):
        cache_key = body_key
    cache_retention = headers.get("x-tokenpak-cache-retention")
    if cache_retention is None and isinstance(body_retention, str):
        cache_retention = body_retention
    return cache_key, cache_retention


def _inject_prompt_cache_key(provider: Provider, headers: Mapping[str, str], body: bytes) -> bytes:
    """Inject ``prompt_cache_key`` for OpenAI / Azure OpenAI / Codex / xAI requests.

    Accepts cache hints from:
    - Header ``x-tokenpak-cache-key`` (takes precedence over body field)
    - Body field ``tokenpak_cache_hint`` (stripped before forwarding)

    Accepts retention hint from:
    - Header ``x-tokenpak-cache-retention``
    - Body field ``tokenpak_cache_retention`` (stripped before forwarding)

    For providers that don't support ``prompt_cache_key``, ``tokenpak_*`` body
    fields are still stripped so they never reach the upstream provider.

    Args:
        provider: Detected LLM provider.
        headers: Request headers dict.
        body: Request body bytes.

    Returns:
        Modified body bytes with cache fields injected and ``tokenpak_*`` stripped.
    """
    if not body:
        return body

    # Fast path: skip JSON parse when no cache hints are present
    _has_header_hint = bool(
        headers.get("x-tokenpak-cache-key") or headers.get("x-tokenpak-cache-retention")
    )
    _has_body_hint = b"tokenpak_cache" in body
    if not _has_header_hint and not _has_body_hint:
        return body

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body

    if not isinstance(data, dict):
        return body

    # Extract hints (pops tokenpak_* fields from data)
    cache_key, cache_retention = _extract_cache_hints(headers, data)

    if not cache_key:
        # tokenpak_* fields may have been stripped; re-encode to apply removal
        return json.dumps(data).encode()

    if provider in (Provider.OPENAI, Provider.AZURE_OPENAI, Provider.CODEX):
        # Codex uses same Responses API as OpenAI — identical cache key fields
        data["prompt_cache_key"] = cache_key
        if cache_retention:
            data["prompt_cache_retention"] = cache_retention
    elif provider == Provider.XAI:
        data["prompt_cache_key"] = cache_key
        # x-grok-conv-id forwarding: handled by _sanitize_headers (not in blocked list)
    # All other providers: silently ignore — tokenpak_* already stripped above

    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# Bedrock cache token parsing
# ---------------------------------------------------------------------------


def _parse_bedrock_cached_tokens(response_data: Mapping[str, object]) -> int:
    """Parse cached token count from Bedrock responses.

    Bedrock returns cache metrics in the usage object:
    {
        "usage": {
            "inputTokens": 1000,
            "outputTokens": 50,
            "cacheReadInputTokens": 800,    // tokens read from cache
            "cacheWriteInputTokens": 200    // tokens written to cache
        }
    }

    Note: Bedrock also uses CacheReadInputTokens (camelCase) in some response formats.
    We check both snake_case and camelCase variants.

    Args:
        response_data: Parsed JSON response from Bedrock

    Returns:
        Number of tokens read from cache (0 if not present)
    """
    usage = response_data.get("usage", {})
    if not isinstance(usage, dict):
        return 0

    # Check both potential field names (Bedrock uses camelCase in Converse API)
    cache_read = usage.get("cacheReadInputTokens", 0)
    if not cache_read:
        # Some responses might use this format
        cache_read = usage.get("cacheReadInputTokenCount", 0)

    return cache_read if isinstance(cache_read, int) else 0


def _parse_bedrock_cache_creation_tokens(response_data: Mapping[str, object]) -> int:
    """Parse cache creation token count from Bedrock responses.

    Args:
        response_data: Parsed JSON response from Bedrock

    Returns:
        Number of tokens written to cache (0 if not present)
    """
    usage = response_data.get("usage", {})
    if not isinstance(usage, dict):
        return 0

    cache_write = usage.get("cacheWriteInputTokens", 0)
    if not cache_write:
        cache_write = usage.get("cacheWriteInputTokenCount", 0)

    return cache_write if isinstance(cache_write, int) else 0
