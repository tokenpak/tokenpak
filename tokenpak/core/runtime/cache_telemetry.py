"""
CacheTelemetry — Per-provider cache hit/miss tracking for TokenPak.

CACHE-P4-002: Lightweight telemetry layer built on top of CacheSpec.

Tracks:
- Cache hit/miss per provider (inferred from provider response signals)
- Cache mode resolved per provider per request
- Token counts (read + creation) per provider
- Estimated cost savings per provider

Provider signal extraction:
- Anthropic: usage.cache_read_input_tokens / usage.cache_creation_input_tokens
  (also available as response headers anthropic-cache-read-input-tokens /
   anthropic-cache-creation-input-tokens, but body fields are used here for
   consistency with the streaming path)
- OpenAI: usage.prompt_tokens_details.cached_tokens
- Gemini: usageMetadata.cachedContentTokenCount
- Bedrock: usage.cacheReadInputTokens / usage.cacheWriteInputTokens
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "CacheTelemetry",
    "ProviderCacheStats",
]

logger = logging.getLogger(__name__)


@dataclass
class ProviderCacheStats:
    """Accumulated cache stats for a single provider."""

    hits: int = 0
    misses: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    savings_usd: float = 0.0
    mode_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return (self.hits / self.total) if self.total > 0 else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total": self.total,
            "hit_rate": round(self.hit_rate, 4),
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "estimated_savings_usd": round(self.savings_usd, 6),
            "mode_counts": dict(self.mode_counts),
        }


class CacheTelemetry:
    """Thread-safe per-provider cache hit/miss/mode telemetry collector.

    Usage::

        telemetry = CacheTelemetry()

        # After processing a response:
        read_tok, create_tok = CacheTelemetry.extract_anthropic_signals(response_json)
        telemetry.record(
            provider="anthropic",
            mode="block_explicit",
            cache_read_tokens=read_tok,
            cache_creation_tokens=create_tok,
            savings_usd=0.001,
        )

        # In /status handler:
        stats_dict["cache_telemetry"] = telemetry.to_dict()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_provider: dict[str, ProviderCacheStats] = {}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        provider: str,
        mode: str | None,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        savings_usd: float = 0.0,
    ) -> None:
        """Record a cache result for a provider.

        Args:
            provider: Provider name string (e.g. "anthropic", "openai").
            mode: Resolved cache mode string (e.g. "block_explicit"), or None.
            cache_read_tokens: Tokens served from cache this request (>0 = hit).
            cache_creation_tokens: Tokens written to cache this request.
            savings_usd: Estimated cost savings from cache hit.
        """
        is_hit = cache_read_tokens > 0
        logger.debug(
            "cache_telemetry provider=%s mode=%s read_tokens=%d creation_tokens=%d "
            "hit=%s savings_usd=%.6f",
            provider,
            mode or "none",
            cache_read_tokens,
            cache_creation_tokens,
            is_hit,
            savings_usd,
        )
        with self._lock:
            stats = self._by_provider.setdefault(provider, ProviderCacheStats())
            if is_hit:
                stats.hits += 1
            else:
                stats.misses += 1
            stats.cache_read_tokens += cache_read_tokens
            stats.cache_creation_tokens += cache_creation_tokens
            stats.savings_usd += savings_usd
            if mode:
                stats.mode_counts[mode] = stats.mode_counts.get(mode, 0) + 1

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Return current telemetry state as a JSON-serializable dict."""
        with self._lock:
            by_provider = {k: v.to_dict() for k, v in self._by_provider.items()}
            total_hits = sum(v.hits for v in self._by_provider.values())
            total_misses = sum(v.misses for v in self._by_provider.values())
            total = total_hits + total_misses
            total_savings = sum(v.savings_usd for v in self._by_provider.values())

        return {
            "by_provider": by_provider,
            "totals": {
                "hits": total_hits,
                "misses": total_misses,
                "total": total,
                "hit_rate": round((total_hits / total) if total > 0 else 0.0, 4),
                "estimated_savings_usd": round(total_savings, 6),
            },
            "active_providers": sorted(by_provider.keys()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def reset(self) -> None:
        """Reset all counters (for testing or manual resets)."""
        with self._lock:
            self._by_provider.clear()

    # ------------------------------------------------------------------
    # Provider signal extractors (static — usable independently of instance)
    # ------------------------------------------------------------------

    @staticmethod
    def extract_anthropic_signals(
        response_body: Mapping[str, object],
    ) -> tuple[int, int]:
        """Extract (cache_read_tokens, cache_creation_tokens) from Anthropic response.

        Anthropic returns cache metrics directly in the usage object::

            {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 200
                }
            }

        Also available as response headers:
            anthropic-cache-read-input-tokens
            anthropic-cache-creation-input-tokens

        Args:
            response_body: Parsed JSON response dict from Anthropic.

        Returns:
            Tuple of (cache_read_tokens, cache_creation_tokens). Both default to 0.
        """
        usage = response_body.get("usage", {})
        if not isinstance(usage, dict):
            return 0, 0
        read = usage.get("cache_read_input_tokens", 0) or 0
        creation = usage.get("cache_creation_input_tokens", 0) or 0
        return _token_count(read), _token_count(creation)

    @staticmethod
    def extract_openai_signals(
        response_body: Mapping[str, object],
    ) -> tuple[int, int]:
        """Extract (cache_read_tokens, 0) from OpenAI response.

        OpenAI returns cached token counts in prompt_tokens_details::

            {
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 50,
                    "prompt_tokens_details": {
                        "cached_tokens": 800,
                        "audio_tokens": 0
                    }
                }
            }

        Args:
            response_body: Parsed JSON response dict from OpenAI.

        Returns:
            Tuple of (cache_read_tokens, 0). Creation tokens not reported by OpenAI.
        """
        usage = response_body.get("usage", {})
        if not isinstance(usage, dict):
            return 0, 0
        details = usage.get("prompt_tokens_details", {})
        if not isinstance(details, dict):
            return 0, 0
        cached = details.get("cached_tokens", 0) or 0
        return _token_count(cached), 0

    @staticmethod
    def extract_gemini_signals(
        response_body: Mapping[str, object],
    ) -> tuple[int, int]:
        """Extract (cache_read_tokens, 0) from Gemini response.

        Gemini returns cache usage in usageMetadata::

            {
                "usageMetadata": {
                    "promptTokenCount": 1000,
                    "candidatesTokenCount": 50,
                    "cachedContentTokenCount": 800
                }
            }

        Args:
            response_body: Parsed JSON response dict from Gemini.

        Returns:
            Tuple of (cache_read_tokens, 0). Creation tokens not reported by Gemini.
        """
        usage = response_body.get("usageMetadata", {})
        if not isinstance(usage, dict):
            return 0, 0
        cached = usage.get("cachedContentTokenCount", 0) or 0
        return _token_count(cached), 0

    @staticmethod
    def extract_bedrock_signals(
        response_body: Mapping[str, object],
    ) -> tuple[int, int]:
        """Extract (cache_read_tokens, cache_creation_tokens) from Bedrock response.

        Bedrock returns cache metrics in the usage object (Converse API)::

            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 50,
                    "cacheReadInputTokens": 800,
                    "cacheWriteInputTokens": 200
                }
            }

        Note: Some Bedrock response formats use cacheReadInputTokenCount instead.
        Both variants are checked.

        Args:
            response_body: Parsed JSON response dict from Bedrock.

        Returns:
            Tuple of (cache_read_tokens, cache_creation_tokens).
        """
        usage = response_body.get("usage", {})
        if not isinstance(usage, dict):
            return 0, 0
        read = usage.get("cacheReadInputTokens", 0) or usage.get("cacheReadInputTokenCount", 0) or 0
        creation = (
            usage.get("cacheWriteInputTokens", 0) or usage.get("cacheWriteInputTokenCount", 0) or 0
        )
        return _token_count(read), _token_count(creation)

    @staticmethod
    def extract_signals_from_headers(
        headers: Mapping[str, object],
    ) -> tuple[int, int]:
        """Extract Anthropic cache signals from HTTP response headers.

        Anthropic sends cache token counts as response headers in addition to
        the body. This method extracts them from the header dict for cases where
        body parsing is not available (e.g. early in SSE stream).

        Headers:
            anthropic-cache-read-input-tokens
            anthropic-cache-creation-input-tokens

        Args:
            headers: HTTP response header dict (case-insensitive lookup attempted).

        Returns:
            Tuple of (cache_read_tokens, cache_creation_tokens).
        """

        # Headers may be passed as http.client.HTTPMessage (case-insensitive)
        # or as a plain dict (case-sensitive). Try both.
        def _get(key: str) -> int:
            val = headers.get(key) or headers.get(key.lower()) or 0
            return _token_count(val)

        read = _get("anthropic-cache-read-input-tokens")
        creation = _get("anthropic-cache-creation-input-tokens")
        return read, creation


def _token_count(value: object) -> int:
    """Convert a provider token field to an integer without leaking errors."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float, str, bytes, bytearray)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
    return 0
