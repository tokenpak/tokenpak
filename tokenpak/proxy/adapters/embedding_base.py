"""Abstract base class for embedding provider adapters."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Dict, Mapping, Optional, Tuple

from .canonical import CanonicalEmbeddingRequest


class EmbeddingAdapter(ABC):
    """Abstract embedding adapter for provider-specific embedding payloads."""

    source_format: str = "unknown"

    @abstractmethod
    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Return True if this adapter should handle the given request."""
        raise NotImplementedError

    @abstractmethod
    def normalize_request(
        self, canonical: CanonicalEmbeddingRequest
    ) -> Tuple[str, Dict[str, str], bytes]:
        """Convert a canonical embedding request to provider-specific form.

        Returns:
            (url, headers, body) ready to forward to the upstream provider.
        """
        raise NotImplementedError

    @abstractmethod
    def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes:
        """Convert a provider-specific response body to the canonical OpenAI embedding format."""
        raise NotImplementedError

    @abstractmethod
    def get_default_upstream(self) -> str:
        """Return the default upstream base URL for this provider."""
        raise NotImplementedError

    @abstractmethod
    def get_env_key_name(self) -> str:
        """Return the environment variable name that holds this provider's API key."""
        raise NotImplementedError

    @abstractmethod
    def get_default_model(self) -> str:
        """Return the default embedding model identifier for this provider."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Return True if the provider's API key is present in the environment."""
        return bool(os.environ.get(self.get_env_key_name()))
