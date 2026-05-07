"""Ollama local embedding adapter."""

from __future__ import annotations

import json
import os
from typing import Dict, Mapping, Optional, Tuple
from urllib.error import URLError
from urllib.request import urlopen

from .canonical import CanonicalEmbeddingRequest
from .embedding_base import EmbeddingAdapter

_OLLAMA_PREFIXES = ("nomic-", "mxbai-", "all-minilm", "snowflake-")
_DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaEmbeddingAdapter(EmbeddingAdapter):
    """Embedding adapter for Ollama local inference.

    Upstream: $TOKENPAK_OLLAMA_URL/v1/embeddings (default http://localhost:11434)
    Auth:     None — Ollama requires no authentication
    Schema:   OpenAI-compatible on both request and response sides

    is_available() returns True when:
      - TOKENPAK_OLLAMA_URL is set in the environment, OR
      - the default Ollama URL responds to a HEAD/GET request
    """

    source_format = "ollama-embeddings"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Return True if the request targets a known Ollama embedding model."""
        if body:
            try:
                data = json.loads(body)
                model = str(data.get("model", ""))
                return any(model.startswith(prefix) for prefix in _OLLAMA_PREFIXES)
            except (json.JSONDecodeError, AttributeError):
                pass
        return False

    def normalize_request(
        self, canonical: CanonicalEmbeddingRequest
    ) -> Tuple[str, Dict[str, str], bytes]:
        """Convert a canonical embedding request to Ollama /v1/embeddings wire format.

        Ollama's /v1/embeddings is OpenAI-compatible, so minimal transformation is needed.
        Ensures input is a list (Ollama requires list, not bare string).

        Returns:
            (url, headers, body) ready to forward to Ollama.
        """
        # Ensure input is always a list
        input_value = canonical.input if isinstance(canonical.input, list) else [canonical.input]

        payload: Dict = {
            "model": canonical.model,
            "input": input_value,
        }

        # Pass through optional fields that Ollama supports
        if canonical.encoding_format:
            payload["encoding_format"] = canonical.encoding_format
        if canonical.dimensions is not None:
            payload["dimensions"] = canonical.dimensions

        # Preserve any unknown canonical fields
        payload.update(canonical.raw_extra)

        out_headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        url = f"{self.get_default_upstream()}/v1/embeddings"
        out_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return url, out_headers, out_body

    def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes:
        """Convert Ollama response to canonical OpenAI-compatible embedding format.

        Ollama /v1/embeddings returns OpenAI format already.
        Only normalisation needed: strip system_fingerprint if present.
        """
        data = json.loads(body)
        data.pop("system_fingerprint", None)
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def get_env_key_name(self) -> str:
        return "TOKENPAK_OLLAMA_URL"

    def get_default_model(self) -> str:
        return "nomic-embed-text"

    def get_default_upstream(self) -> str:
        return os.environ.get("TOKENPAK_OLLAMA_URL", _DEFAULT_OLLAMA_URL)

    def is_available(self) -> bool:
        """Return True if Ollama is reachable.

        Checks in order:
          1. TOKENPAK_OLLAMA_URL env var is set — explicit config means available.
          2. Try a lightweight HTTP request to the default Ollama URL.
        Returns False on any connection error (Ollama may not be running).
        """
        if os.environ.get("TOKENPAK_OLLAMA_URL"):
            return True
        try:
            urlopen(f"{_DEFAULT_OLLAMA_URL}/v1/models", timeout=2)
            return True
        except (URLError, OSError):
            return False
