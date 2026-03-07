"""OpenAI adapter for TokenPak telemetry.

Handles two OpenAI response shapes:

Chat Completions API (``/v1/chat/completions``)
------------------------------------------------
``{ "object": "chat.completion", "choices": [...], "usage": {...} }``

Responses API (``/v1/responses``)
----------------------------------
``{ "object": "response", "output": [...], "usage": {...} }``

Codex detection
---------------
A payload is treated as a Codex (code-model) request when:
- The model name contains ``"codex"`` (case-insensitive), OR
- A ``"reasoning"`` key is present at the top level.

Usage field mapping
-------------------
Chat Completions:
  ``prompt_tokens``             → ``input_billed``
  ``completion_tokens``         → ``output_billed``
  ``prompt_tokens_details.cached_tokens`` → ``cache_read``

Responses API uses the same field names.
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

# Mapping from OpenAI finish_reason → canonical finish_reason
_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "stop",
    "null": "unknown",
}


def _is_codex(raw: dict[str, Any]) -> bool:
    """Return True when the payload is identified as a Codex model call."""
    model: str = raw.get("model", "")
    return "codex" in model.lower() or "reasoning" in raw


class OpenAIAdapter(BaseAdapter):
    """Adapter for the OpenAI Chat Completions and Responses APIs.

    Automatically distinguishes between:
    - Chat Completions (``choices[].message``)
    - Responses API (``output[]`` list)
    - Codex / reasoning-enabled variants
    """

    provider_name: str = "openai"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]:
        """Return confidence score for OpenAI payloads.

        Detection signals:
        - ``"choices"`` key present                     → 0.8
        - ``"object"`` starts with ``"chat.completion"`` → +0.2
        - ``"output"`` key (list) present               → 0.7
        - ``"object"`` == ``"response"``                → +0.2
        - Negative: ``"candidates"`` or ``"stop_reason"`` keys → 0.0
        """
        score = 0.0

        # Negative signals — clearly another provider
        if "candidates" in raw_payload or "stop_reason" in raw_payload:
            return (self.provider_name, 0.0)

        obj = raw_payload.get("object", "")

        if "choices" in raw_payload:
            score = 0.8
            if isinstance(obj, str) and obj.startswith("chat.completion"):
                score = 1.0

        elif "output" in raw_payload and isinstance(raw_payload["output"], list):
            score = 0.7
            if obj == "response":
                score = 1.0

        # model name starting with "gpt", "o1", "o3", "text-davinci", etc.
        model: str = raw_payload.get("model", "")
        if score == 0.0 and any(
            model.lower().startswith(pfx) for pfx in ("gpt-", "o1", "o3", "text-davinci", "codex")
        ):
            score = 0.5

        return (self.provider_name, min(score, 1.0))

    # ------------------------------------------------------------------
    # Request normalisation
    # ------------------------------------------------------------------

    def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        """Normalise an OpenAI request payload.

        Both Chat Completions and Responses API requests share the same
        ``messages`` structure (``[{"role": ..., "content": ...}]``).
        """
        excluded = {"model", "messages", "tools", "functions"}
        params = {k: v for k, v in raw.items() if k not in excluded}

        # Merge legacy "functions" into tools
        tools: list[dict[str, Any]] = list(raw.get("tools", []))
        if "functions" in raw:
            for fn in raw["functions"]:
                tools.append({"type": "function", "function": fn})

        is_codex = _is_codex(raw)
        if is_codex:
            params["_tokenpak_codex"] = True

        return CanonicalRequest(
            provider=self.provider_name,
            model=raw.get("model", ""),
            messages=raw.get("messages", []),
            tools=tools,
            params=params,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Response normalisation
    # ------------------------------------------------------------------

    def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        """Normalise an OpenAI response payload.

        Handles:
        - Chat Completions: output comes from ``choices[0].message.content``
        - Responses API:    output comes from ``output[0].content`` or
          the full ``output`` list for multi-turn.
        """
        output: Any = None
        finish_reason: str = "unknown"
        error_str: str | None = None

        error = raw.get("error")
        if error:
            error_str = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return CanonicalResponse(
                output=None,
                finish_reason="error",
                error=error_str,
                raw=raw,
            )

        if "choices" in raw:
            choices = raw["choices"]
            if choices:
                first = choices[0]
                message = first.get("message", {})
                output = message.get("content") or message.get("tool_calls")
                raw_fr = first.get("finish_reason", "unknown") or "unknown"
                finish_reason = _FINISH_REASON_MAP.get(raw_fr, raw_fr)

        elif "output" in raw:
            output_list = raw["output"]
            if output_list:
                # Preserve all output items so reasoning/tool items are kept
                output = output_list
            finish_reason_raw = raw.get("status", "completed")
            if finish_reason_raw == "completed":
                finish_reason = "stop"
            else:
                finish_reason = finish_reason_raw

        return CanonicalResponse(
            output=output,
            finish_reason=finish_reason,
            error=error_str,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Usage extraction
    # ------------------------------------------------------------------

    def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage:
        """Extract token-usage from an OpenAI response.

        Fields mapped:
        ``usage.prompt_tokens``                              → ``input_billed``
        ``usage.completion_tokens``                          → ``output_billed``
        ``usage.prompt_tokens_details.cached_tokens``        → ``cache_read``
        ``usage.completion_tokens_details.reasoning_tokens`` → stored in output_est
        """
        usage = raw.get("usage", {})
        if not usage:
            return CanonicalUsage(
                usage_source=UsageSource.UNKNOWN,
                confidence=Confidence.LOW,
            )

        input_billed = int(usage.get("prompt_tokens", 0))
        output_billed = int(usage.get("completion_tokens", 0))

        details = usage.get("prompt_tokens_details", {}) or {}
        cache_read = int(details.get("cached_tokens", 0))

        return CanonicalUsage(
            input_billed=input_billed,
            output_billed=output_billed,
            input_est=input_billed,
            output_est=output_billed,
            cache_read=cache_read,
            cache_write=0,  # OpenAI does not expose cache-write counts
            usage_source=UsageSource.PROVIDER_REPORTED,
            confidence=Confidence.HIGH,
        )
