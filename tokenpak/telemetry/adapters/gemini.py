"""Google Gemini API adapter for TokenPak telemetry.

Handles the Gemini GenerateContent response format:

Response shape
--------------
```json
{
  "candidates": [
    {
      "content": {"parts": [{"text": "..."}], "role": "model"},
      "finishReason": "STOP"
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 100,
    "candidatesTokenCount": 50,
    "cachedContentTokenCount": 20,
    "totalTokenCount": 150
  }
}
```

Request shape
-------------
```json
{
  "contents": [{"role": "user", "parts": [{"text": "..."}]}],
  "tools": [...],
  "generationConfig": {...}
}
```

Missing usage handling
----------------------
``usageMetadata`` may be absent in streaming chunks or error responses.
When it is missing the adapter returns ``confidence: "low"`` and sets
``usage_source: "unknown"``.

Detection heuristics
--------------------
- ``"candidates"`` key present in response → high confidence
- ``"contents"`` key present in request    → medium confidence
- Neither ``"choices"`` nor ``"stop_reason"`` present → avoids false-positives
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

# Gemini finishReason → canonical finish_reason
_FINISH_REASON_MAP: dict[str, str] = {
    "STOP": "stop",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "stop",
    "RECITATION": "stop",
    "OTHER": "unknown",
    "FINISH_REASON_UNSPECIFIED": "unknown",
}


class GeminiAdapter(BaseAdapter):
    """Adapter for the Google Gemini GenerateContent API.

    Handles:
    - ``candidates[].content.parts[]`` for multi-part responses.
    - ``usageMetadata`` extraction including cached content tokens.
    - Graceful degradation when ``usageMetadata`` is absent.
    """

    provider_name: str = "gemini"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]:
        """Return confidence score for Gemini payloads.

        Signals:
        - ``"candidates"`` key present                → 0.9
        - ``"usageMetadata"`` key present             → +0.1 (capped at 1.0)
        - ``"contents"`` key present (request-side)   → 0.6
        - Negative: ``"choices"`` or ``"stop_reason"`` → 0.0
        """
        if "choices" in raw_payload or "stop_reason" in raw_payload:
            return (self.provider_name, 0.0)

        score = 0.0

        if "candidates" in raw_payload:
            score = 0.9
            if "usageMetadata" in raw_payload:
                score = 1.0

        elif "contents" in raw_payload:
            # Request payload
            score = 0.6
            if "generationConfig" in raw_payload:
                score = 0.75

        return (self.provider_name, min(score, 1.0))

    # ------------------------------------------------------------------
    # Request normalisation
    # ------------------------------------------------------------------

    def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        """Normalise a Gemini request payload.

        Gemini uses ``contents`` (list of turns) instead of ``messages``.
        Each turn has ``{"role": ..., "parts": [...]}`` structure.  We
        preserve them as-is under ``messages`` for uniform downstream access.
        """
        excluded = {"model", "contents", "tools", "generationConfig"}
        params: dict[str, Any] = {k: v for k, v in raw.items() if k not in excluded}
        if "generationConfig" in raw:
            params["generationConfig"] = raw["generationConfig"]

        return CanonicalRequest(
            provider=self.provider_name,
            model=raw.get("model", ""),
            messages=raw.get("contents", []),
            tools=raw.get("tools", []),
            params=params,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Response normalisation
    # ------------------------------------------------------------------

    def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        """Normalise a Gemini response payload.

        Extracts text parts from the first candidate.  If there are no
        candidates (error response), returns an error ``CanonicalResponse``.
        """
        error_str: str | None = None
        error = raw.get("error")
        if error:
            if isinstance(error, dict):
                error_str = error.get("message", str(error))
            else:
                error_str = str(error)
            return CanonicalResponse(
                output=None,
                finish_reason="error",
                error=error_str,
                raw=raw,
            )

        candidates = raw.get("candidates", [])
        if not candidates:
            return CanonicalResponse(
                output=None,
                finish_reason="unknown",
                error="No candidates in response",
                raw=raw,
            )

        first = candidates[0]
        content = first.get("content", {})
        parts = content.get("parts", [])

        # Flatten to a single string when all parts are text
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        non_text = [p for p in parts if "text" not in p]

        if text_parts and not non_text:
            output: Any = "\n".join(text_parts)
        else:
            output = parts  # preserve full structure for multimodal / function calls

        raw_finish = first.get("finishReason", "FINISH_REASON_UNSPECIFIED")
        finish_reason = _FINISH_REASON_MAP.get(raw_finish, raw_finish.lower())

        return CanonicalResponse(
            output=output,
            finish_reason=finish_reason,
            error=None,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Usage extraction
    # ------------------------------------------------------------------

    def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage:
        """Extract token-usage from a Gemini response.

        Gemini ``usageMetadata`` fields:
        - ``promptTokenCount``      → ``input_billed``
        - ``candidatesTokenCount``  → ``output_billed``
        - ``cachedContentTokenCount`` → ``cache_read``

        When ``usageMetadata`` is missing, returns ``confidence: "low"``
        with all counts set to 0.
        """
        meta = raw.get("usageMetadata")
        if not meta:
            return CanonicalUsage(
                usage_source=UsageSource.UNKNOWN,
                confidence=Confidence.LOW,
            )

        input_billed = int(meta.get("promptTokenCount", 0))
        output_billed = int(meta.get("candidatesTokenCount", 0))
        cache_read = int(meta.get("cachedContentTokenCount", 0))

        return CanonicalUsage(
            input_billed=input_billed,
            output_billed=output_billed,
            input_est=input_billed,
            output_est=output_billed,
            cache_read=cache_read,
            cache_write=0,  # Gemini does not expose cache-write counts separately
            usage_source=UsageSource.PROVIDER_REPORTED,
            confidence=Confidence.HIGH,
        )
