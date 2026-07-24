"""OpenAI embedding adapter."""

from __future__ import annotations

import json
import os
from typing import Dict, Mapping, Optional, Tuple

from .canonical import CanonicalEmbeddingRequest
from .embedding_base import EmbeddingAdapter

_OPENAI_MODEL_PREFIXES = (
    "text-embedding-3-small",
    "text-embedding-3-large",
    "text-embedding-ada-002",
)


class OpenAIEmbeddingAdapter(EmbeddingAdapter):
    """Embedding adapter for OpenAI.

    OpenAI's /v1/embeddings is the canonical format, so normalize_request is a
    near-identity transform and normalize_response is a passthrough.

    Upstream: https://api.openai.com/v1/embeddings
    Auth:     Authorization: Bearer $OPENAI_API_KEY
    """

    source_format = "openai-embeddings"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Return True if the request targets a known OpenAI embedding model."""
        if body:
            try:
                data = json.loads(body)
                model = str(data.get("model", ""))
                return any(model.startswith(prefix) for prefix in _OPENAI_MODEL_PREFIXES)
            except (json.JSONDecodeError, AttributeError):
                pass
        return False

    def normalize_request(
        self, canonical: CanonicalEmbeddingRequest
    ) -> Tuple[str, Dict[str, str], bytes]:
        """Convert a canonical embedding request to OpenAI /v1/embeddings wire format.

        Near-identity transform: canonical IS OpenAI format.
        Ensures input is a list; drops provider-specific fields
        (input_type, task, normalized) that OpenAI does not accept.

        Returns:
            (url, headers, body) ready to forward to https://api.openai.com/v1/embeddings.
        """
        input_value = canonical.input if isinstance(canonical.input, list) else [canonical.input]

        payload: Dict[str, object] = {
            "model": canonical.model,
            "input": input_value,
            "encoding_format": canonical.encoding_format,
        }

        # dimensions only supported on text-embedding-3-* models; include when set
        if canonical.dimensions is not None:
            payload["dimensions"] = canonical.dimensions

        # Preserve unknown extra fields without overriding core keys or injecting
        # provider-specific fields that OpenAI rejects
        for k, v in canonical.raw_extra.items():
            if k not in ("input_type", "task", "normalized"):
                payload.setdefault(k, v)

        api_key = os.environ.get(self.get_env_key_name(), "")
        out_headers: Dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.get_default_upstream()}/v1/embeddings"
        out_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return url, out_headers, out_body

    def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes:
        """Return the response body unchanged — OpenAI response is already canonical format."""
        return body

    def get_env_key_name(self) -> str:
        return "OPENAI_API_KEY"

    def get_default_model(self) -> str:
        return "text-embedding-3-small"

    def get_default_upstream(self) -> str:
        return "https://api.openai.com"
