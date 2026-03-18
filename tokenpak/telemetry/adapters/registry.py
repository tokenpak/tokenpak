"""Adapter registry for TokenPak telemetry.

The ``AdapterRegistry`` holds a list of ``BaseAdapter`` instances and
uses their ``detect`` methods to automatically identify the provider
of an unknown raw payload.

Design
------
- ``register(adapter)``   — add an adapter to the registry.
- ``detect(raw)``         — run all adapters, pick the highest-confidence
  match, return the matching adapter instance.
- Minimum confidence threshold: 0.5.  Anything below falls through to
  ``UnknownAdapter``.
- ``build_default()``     — class-method that returns a registry
  pre-populated with Anthropic, OpenAI, and Gemini adapters.

UnknownAdapter
--------------
Fallback adapter returned when no registered adapter exceeds the
minimum confidence threshold.  Stores the raw payload verbatim and
sets ``usage_source="proxy_estimate"`` to signal that usage numbers
are not reliable.
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

# Minimum confidence required for an adapter to be selected.
_MIN_CONFIDENCE: float = 0.5


class UnknownAdapter(BaseAdapter):
    """Fallback adapter used when the provider cannot be determined.

    All extraction methods return empty / zero-valued objects.
    ``extract_usage`` marks results with ``usage_source="proxy_estimate"``
    and ``confidence="low"`` to signal unreliable data.
    """

    provider_name: str = "unknown"

    def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]:
        """Always returns 0 confidence — used only as a fallback."""
        return (self.provider_name, 0.0)

    def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        """Return a minimal canonical request preserving the raw payload."""
        return CanonicalRequest(
            provider=self.provider_name,
            model=raw.get("model", ""),
            messages=raw.get("messages", raw.get("contents", [])),
            tools=[],
            params={},
            raw=raw,
        )

    def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        """Return a minimal canonical response preserving the raw payload."""
        return CanonicalResponse(
            output=None,
            finish_reason="unknown",
            error=None,
            raw=raw,
        )

    def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage:
        """Return a zero-usage record marked as proxy estimate."""
        return CanonicalUsage(
            usage_source=UsageSource.PROXY_ESTIMATE,
            confidence=Confidence.LOW,
        )


class AdapterRegistry:
    """Registry that maps raw LLM payloads to their provider adapter.

    Usage
    -----
    >>> registry = AdapterRegistry.build_default()
    >>> adapter = registry.detect(raw_response)
    >>> usage = adapter.extract_usage(raw_response)

    You can also build a custom registry:

    >>> registry = AdapterRegistry()
    >>> registry.register(MyCustomAdapter())
    >>> adapter = registry.detect(raw_payload)
    """

    def __init__(self) -> None:
        self._adapters: list[BaseAdapter] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, adapter: BaseAdapter) -> None:
        """Add *adapter* to the registry.

        Parameters
        ----------
        adapter:
            A ``BaseAdapter`` instance to register.

        Raises
        ------
        TypeError
            If *adapter* is not a ``BaseAdapter`` instance.
        """
        if not isinstance(adapter, BaseAdapter):
            raise TypeError(f"Expected a BaseAdapter instance, got {type(adapter).__name__!r}.")
        self._adapters.append(adapter)

    @property
    def adapters(self) -> list[BaseAdapter]:
        """Read-only view of registered adapters."""
        return list(self._adapters)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, raw: dict[str, Any]) -> BaseAdapter:
        """Return the best-matching adapter for *raw*.

        Runs ``detect`` on every registered adapter and picks the one
        with the highest confidence score.  Falls back to
        ``UnknownAdapter`` when no adapter reaches ``_MIN_CONFIDENCE``.

        Parameters
        ----------
        raw:
            The raw API payload (request or response dict).

        Returns
        -------
        BaseAdapter
            The selected adapter instance (never ``None``).
        """
        best_adapter: BaseAdapter | None = None
        best_score: float = 0.0

        for adapter in self._adapters:
            try:
                _name, score = adapter.detect(raw)
            except Exception:  # noqa: BLE001 — never crash on bad payload
                score = 0.0

            if score > best_score:
                best_score = score
                best_adapter = adapter

        if best_adapter is not None and best_score >= _MIN_CONFIDENCE:
            return best_adapter

        return UnknownAdapter()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def build_default(cls) -> "AdapterRegistry":
        """Return a registry pre-populated with all built-in adapters.

        Adapters registered (in order):
        1. ``AnthropicAdapter``
        2. ``OpenAIAdapter``
        3. ``GeminiAdapter``
        """
        # Import here to avoid circular-import issues at module load time.
        from tokenpak.telemetry.adapters.anthropic import AnthropicAdapter
        from tokenpak.telemetry.adapters.gemini import GeminiAdapter
        from tokenpak.telemetry.adapters.openai import OpenAIAdapter

        registry = cls()
        registry.register(AnthropicAdapter())
        registry.register(OpenAIAdapter())
        registry.register(GeminiAdapter())
        return registry

    def __repr__(self) -> str:  # noqa: D105
        names = [a.provider_name for a in self._adapters]
        return f"<AdapterRegistry adapters={names!r}>"
