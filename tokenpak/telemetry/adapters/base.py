"""Abstract base class for TokenPak provider adapters.

Every concrete adapter must subclass ``BaseAdapter`` and implement all
abstract methods.  The registry uses the ``detect`` method to pick the
best adapter for an unknown raw payload.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from tokenpak.telemetry.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)


class BaseAdapter(ABC):
    """Protocol adapter that translates provider-specific payloads into
    canonical TokenPak telemetry types.

    Sub-classes
    -----------
    Each adapter is responsible for a single provider (e.g. Anthropic,
    OpenAI, Gemini).  Adapters are stateless; every method is a pure
    transformation from raw ``dict`` → canonical object.

    Detection contract
    ------------------
    ``detect`` returns ``(provider_name, confidence)`` where *confidence* is
    in the range ``[0.0, 1.0]``.  The registry picks the adapter with the
    highest confidence score.  Return ``0.0`` if the payload definitively
    does *not* match.
    """

    # Each subclass should set this to a stable provider identifier.
    provider_name: str = "unknown"

    @abstractmethod
    def detect(self, raw_payload: dict[str, Any]) -> tuple[str, float]:
        """Determine whether *raw_payload* came from this adapter's provider.

        Parameters
        ----------
        raw_payload:
            The raw API payload (request or response dict).

        Returns
        -------
        tuple[str, float]
            ``(provider_name, confidence)`` where *confidence* is in
            ``[0.0, 1.0]``.  Return ``0.0`` confidence when the payload
            clearly does NOT belong to this provider.
        """

    @abstractmethod
    def to_canonical_request(self, raw: dict[str, Any]) -> CanonicalRequest:
        """Normalise a raw request payload into a ``CanonicalRequest``.

        Parameters
        ----------
        raw:
            The raw request dict as captured at the proxy/interceptor layer.
        """

    @abstractmethod
    def to_canonical_response(self, raw: dict[str, Any]) -> CanonicalResponse:
        """Normalise a raw response payload into a ``CanonicalResponse``.

        Parameters
        ----------
        raw:
            The raw response dict returned by the provider API.
        """

    @abstractmethod
    def extract_usage(self, raw: dict[str, Any]) -> CanonicalUsage:
        """Extract token-usage information from a raw payload.

        The payload may be either a request or a response dict; adapters
        should handle both gracefully (returning zeroed ``CanonicalUsage``
        when usage data is not present).

        Parameters
        ----------
        raw:
            The raw request or response dict.
        """

    def __repr__(self) -> str:  # noqa: D105
        return f"<{type(self).__name__} provider={self.provider_name!r}>"
