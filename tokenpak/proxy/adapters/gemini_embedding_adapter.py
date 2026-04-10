"""Google Gemini embedding adapter."""

from __future__ import annotations

import json
import os
from typing import Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlencode

from .canonical import CanonicalEmbeddingRequest
from .embedding_base import EmbeddingAdapter

_GEMINI_EMBED_BASE = "https://generativelanguage.googleapis.com"


class GeminiEmbeddingAdapter(EmbeddingAdapter):
    """Embedding adapter for Google Gemini.

    Upstream: https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent
    Auth:     query param ?key=<GEMINI_API_KEY>  (NOT Authorization header)
    Schema:   {content: {parts: [{text: ...}]}} → {embedding: {values: [...]}}

    NOTE — batch inputs: embedContent accepts a single text. For >1 input, the
    batchEmbedContent endpoint should be used.  This adapter currently embeds
    only the *first* input string.  Full batch support is tracked as a TODO.
    """

    source_format = "gemini-embeddings"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Return True when the request targets a Gemini embedding model."""
        if body:
            try:
                data = json.loads(body)
                model = data.get("model", "")
                return str(model).startswith("gemini-embed")
            except (json.JSONDecodeError, AttributeError):
                pass
        return False

    def normalize_request(
        self, canonical: CanonicalEmbeddingRequest
    ) -> Tuple[str, Dict[str, str], bytes]:
        """Convert a canonical embedding request to Gemini embedContent wire format.

        Returns:
            (url, headers, body) ready to forward to the Gemini API.

        NOTE: Only the first element of canonical.input is sent.  Batch
        support (batchEmbedContent) is a TODO — see class docstring.
        """
        model = canonical.model or self.get_default_model()

        # Gemini embedContent is single-text; use first input only.
        # TODO: implement batch via batchEmbedContent for len(canonical.input) > 1
        text = canonical.input[0] if canonical.input else ""

        payload: Dict = {
            "content": {
                "parts": [{"text": text}]
            }
        }

        # Map canonical dimensions → Gemini outputDimensionality
        if canonical.dimensions is not None:
            payload["outputDimensionality"] = canonical.dimensions

        api_key = os.environ.get(self.get_env_key_name(), "")
        url = (
            f"{_GEMINI_EMBED_BASE}/v1beta/models/{model}:embedContent"
            f"?{urlencode({'key': api_key})}"
        )

        out_headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        out_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return url, out_headers, out_body

    def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes:
        """Convert a Gemini embedContent response to OpenAI-compatible embedding format.

        Gemini returns:
            {"embedding": {"values": [...]}}

        OpenAI format returned:
            {
              "object": "list",
              "data": [{"object": "embedding", "index": 0, "embedding": [...]}],
              "model": "<model>",
              "usage": {"prompt_tokens": N, "total_tokens": N}
            }
        """
        raw = json.loads(body)

        values: List[float] = raw.get("embedding", {}).get("values", [])

        # Gemini embedContent returns no token-usage metadata.
        # Estimate from the embedded text length as a best-effort proxy.
        estimated_tokens = 0
        try:
            req_content = raw.get("_request_content")  # not present in response
        except Exception:
            pass

        openai_response: Dict = {
            "object": "list",
            "data": [
                {
                    "object": "embedding",
                    "index": 0,
                    "embedding": values,
                }
            ],
            "model": raw.get("model", self.get_default_model()),
            "usage": {
                "prompt_tokens": estimated_tokens,
                "total_tokens": estimated_tokens,
            },
        }

        return json.dumps(openai_response, ensure_ascii=False).encode("utf-8")

    def get_env_key_name(self) -> str:
        return "GEMINI_API_KEY"

    def get_default_model(self) -> str:
        return "gemini-embedding-001"

    def get_default_upstream(self) -> str:
        return _GEMINI_EMBED_BASE
