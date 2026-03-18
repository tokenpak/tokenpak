"""Anthropic Messages API adapter for TokenPak telemetry.

Handles the Anthropic Messages API format:
  - Request:  ``{ "model": ..., "system": ..., "messages": [...], ... }``
  - Response: ``{ "id": ..., "type": "message", "content": [...], "usage": {...} }``

Detection heuristics
--------------------
1. Request contains ``"anthropic-version"`` header key.
2. Response has ``"type": "message"`` and a ``"content"`` list whose items
   have a ``"type"`` field (e.g. ``text``, ``tool_use``, ``tool_result``).
3. ``"stop_reason"`` key is present (Anthropic-specific field name).

Cache token extraction
----------------------
Anthropic reports cache activity in the ``usage`` block:
  - ``cache_creation_input_tokens`` — tokens written to the prompt cache
  - ``cache_read_input_tokens``     — tokens served from the prompt cache
"""

from __future__ import annotations

from typing import Any

from tokenpak.telemetry.adapters.base import BaseAdapter
from tokenpak.telemetry.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Confidence,
    UsageSource,
)

# Mapping from Anthropic stop_reason → canonical finish_reason
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    "tool_use": "tool_use",
}


class AnthropicAdapter(BaseAdapter):
    """Adapter for the Anthropic Messages API.

    Supports:
    - Text and tool-use content blocks in requests and responses.
    - Prompt-caching usage fields (``cache_creation_input_tokens``,
      ``cache_read_input_tokens``).
    - All ``stop_reason`` variants (end_turn, max_tokens, stop_sequence,
      tool_use).
    """

    provider_name: str = "anthropic"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]:
        """Return high confidence for Anthropic payloads.

        Detection signals (cumulative scoring):
        - ``"anthropic-version"`` key present            → +0.7
        - ``"type" == "message"`` key present            → +0.5
        - ``"stop_reason"`` key present                  → +0.4
        - ``"content"`` is list of dicts with ``"type"`` → +0.3

        Score is clamped to 1.0.
        """
        score = 0.0

        if "anthropic-version" in raw_payload:
            score += 0.7

        if raw_payload.get("type") == "message":
            score += 0.5

        if "stop_reason" in raw_payload:
            score += 0.4

        content = raw_payload.get("content")
        if isinstance(content, list) and content:
            if all(isinstance(block, dict) and "type" in block for block in content):
                score += 0.3

        # Small negative signal if clearly another provider
        if "choices" in raw_payload or "candidates" in raw_payload:
            score = 0.0

        return (self.provider_name, min(score, 1.0))

    # ------------------------------------------------------------------
    # Request normalisation
    # ------------------------------------------------------------------

    def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        """Normalise an Anthropic request payload.

        The Anthropic API passes the *system* prompt as a top-level field
        rather than as the first message.  We inject it as a synthetic
        ``{"role": "system", "content": ...}`` message at position 0 to
        produce a unified message list.
        """
        messages: list[dict[str, Any]] = []

        system = raw.get("system")
        if system:
            if isinstance(system, str):
                messages.append({"role": "system", "content": system})
            elif isinstance(system, list):
                # Anthropic supports structured system blocks
                messages.append({"role": "system", "content": system})

        messages.extend(raw.get("messages", []))

        # Remaining fields → params
        excluded = {"model", "system", "messages", "tools"}
        params = {k: v for k, v in raw.items() if k not in excluded}

        return CanonicalRequest(
            provider=self.provider_name,
            model=raw.get("model", ""),
            messages=messages,
            tools=raw.get("tools", []),
            params=params,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Response normalisation
    # ------------------------------------------------------------------

    def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        """Normalise an Anthropic response payload.

        Content blocks are preserved as a list so tool-use blocks are not
        lost.  For simple text-only responses the ``output`` field will be
        a list containing a single ``{"type": "text", "text": "..."}`` dict.
        """
        content = raw.get("content", [])

        # Normalise stop_reason
        stop_reason = raw.get("stop_reason", "unknown")
        finish_reason = _STOP_REASON_MAP.get(stop_reason, stop_reason or "unknown")

        error = raw.get("error")
        error_str: str | None = None
        if error:
            if isinstance(error, dict):
                error_str = error.get("message") or str(error)
            else:
                error_str = str(error)

        return CanonicalResponse(
            output=content,
            finish_reason=finish_reason,
            error=error_str,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Usage extraction
    # ------------------------------------------------------------------

    def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage:
        """Extract token-usage from an Anthropic response.

        Anthropic always reports exact token counts; confidence is
        ``"high"`` when the ``usage`` block is present.

        Fields mapped:
        - ``input_tokens``                 → ``input_billed``
        - ``output_tokens``                → ``output_billed``
        - ``cache_read_input_tokens``      → ``cache_read``
        - ``cache_creation_input_tokens``  → ``cache_write``
        """
        usage = raw.get("usage", {})
        if not usage:
            return CanonicalUsage(
                usage_source=UsageSource.UNKNOWN,
                confidence=Confidence.LOW,
            )

        input_billed = int(usage.get("input_tokens", 0))
        output_billed = int(usage.get("output_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
        cache_write = int(usage.get("cache_creation_input_tokens", 0))

        return CanonicalUsage(
            input_billed=input_billed,
            output_billed=output_billed,
            input_est=input_billed,  # provider-reported is our best estimate
            output_est=output_billed,
            cache_read=cache_read,
            cache_write=cache_write,
            usage_source=UsageSource.PROVIDER_REPORTED,
            confidence=Confidence.HIGH,
        )
