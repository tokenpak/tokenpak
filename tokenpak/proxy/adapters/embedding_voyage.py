"""Voyage AI embedding adapter (path-based detection)."""

from __future__ import annotations

import json
import os
from typing import Dict, Mapping, Optional, Tuple

from .canonical import CanonicalEmbeddingRequest
from .embedding_base import EmbeddingAdapter


class EmbeddingVoyageAdapter(EmbeddingAdapter):
    """Embedding adapter for Voyage AI.

    Detection strategy: matches requests whose path is ``/v1/embeddings`` when
    ``VOYAGE_API_KEY`` is present in the environment.

    Field mapping (canonical → Voyage):
        dimensions      → output_dimension
        input_type      → input_type  (passed through verbatim)
        truncate        → truncation
        encoding_format → encoding_format  (passed through verbatim)
    """

    source_format = "voyage-embeddings"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Return True when path is /v1/embeddings and VOYAGE_API_KEY is set."""
        return path == "/v1/embeddings" and bool(os.environ.get(self.get_env_key_name()))

    def normalize_request(
        self, canonical: CanonicalEmbeddingRequest
    ) -> Tuple[str, Dict[str, str], bytes]:
        """Convert canonical embedding request to Voyage AI wire format.

        Returns:
            (url, headers, body) ready to forward to https://api.voyageai.com/v1/embeddings.
        """
        payload: Dict = {
            "model": canonical.model,
            "input": canonical.input,
            # truncate → truncation
            "truncation": canonical.truncate,
        }
        # Voyage only accepts encoding_format="base64"; omit for the default "float"
        if canonical.encoding_format and canonical.encoding_format != "float":
            payload["encoding_format"] = canonical.encoding_format

        # dimensions → output_dimension (omit if not set)
        if canonical.dimensions is not None:
            payload["output_dimension"] = canonical.dimensions

        # input_type passed through directly (query / document)
        if canonical.input_type is not None:
            payload["input_type"] = canonical.input_type

        # Preserve any unknown canonical fields without dropping them
        payload.update(canonical.raw_extra)

        api_key = os.environ.get(self.get_env_key_name(), "")
        out_headers: Dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.get_default_upstream()}/v1/embeddings"
        out_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return url, out_headers, out_body

    def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes:
        """Convert Voyage response body to OpenAI-compatible embedding format.

        Ensures:
        - data[].object == 'embedding' is present on every embedding item.
        - usage.prompt_tokens mirrors usage.total_tokens (Voyage omits prompt_tokens).
        """
        data = json.loads(body)

        # Ensure each embedding item has object='embedding'
        for item in data.get("data", []):
            if "object" not in item:
                item["object"] = "embedding"

        # Copy total_tokens → prompt_tokens so callers see both fields
        usage = data.get("usage", {})
        if "total_tokens" in usage and "prompt_tokens" not in usage:
            usage["prompt_tokens"] = usage["total_tokens"]
        data["usage"] = usage

        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def get_env_key_name(self) -> str:
        return "VOYAGE_API_KEY"

    def get_default_model(self) -> str:
        return "voyage-3"

    def get_default_upstream(self) -> str:
        return "https://api.voyageai.com"
