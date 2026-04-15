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

    Single input:  POST /v1beta/models/{model}:embedContent
                   Body: {content: {parts: [{text: ...}]}}
                   Response: {embedding: {values: [...]}}

    Batch input:   POST /v1beta/models/{model}:batchEmbedContents
                   Body: {requests: [{model: "models/{model}", content: {parts: [{text: ...}]}}, ...]}
                   Response: {embeddings: [{values: [...]}, ...]}

    Auth: query param ?key=<GEMINI_API_KEY>  (NOT Authorization header)
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
        """Convert a canonical embedding request to Gemini wire format.

        Single input  → embedContent endpoint (single-text API).
        Multiple inputs → batchEmbedContents endpoint (all inputs embedded in one call).

        Returns:
            (url, headers, body) ready to forward to the Gemini API.
        """
        model = canonical.model or self.get_default_model()
        api_key = os.environ.get(self.get_env_key_name(), "")
        out_headers: Dict[str, str] = {"Content-Type": "application/json"}

        if len(canonical.input) > 1:
            # Batch path: batchEmbedContents accepts multiple texts in one request.
            requests_list: List[Dict] = []
            for text in canonical.input:
                req: Dict = {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                }
                if canonical.dimensions is not None:
                    req["outputDimensionality"] = canonical.dimensions
                requests_list.append(req)

            payload: Dict = {"requests": requests_list}
            url = (
                f"{_GEMINI_EMBED_BASE}/v1beta/models/{model}:batchEmbedContents"
                f"?{urlencode({'key': api_key})}"
            )
        else:
            # Single-text path: embedContent.
            text = canonical.input[0] if canonical.input else ""
            payload = {"content": {"parts": [{"text": text}]}}
            if canonical.dimensions is not None:
                payload["outputDimensionality"] = canonical.dimensions
            url = (
                f"{_GEMINI_EMBED_BASE}/v1beta/models/{model}:embedContent"
                f"?{urlencode({'key': api_key})}"
            )

        out_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return url, out_headers, out_body

    def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes:
        """Convert a Gemini embedContent or batchEmbedContents response to OpenAI format.

        Single response:  {"embedding": {"values": [...]}}
        Batch response:   {"embeddings": [{"values": [...]}, ...]}

        OpenAI format returned:
            {
              "object": "list",
              "data": [{"object": "embedding", "index": N, "embedding": [...]}],
              "model": "<model>",
              "usage": {"prompt_tokens": 0, "total_tokens": 0}
            }
        """
        raw = json.loads(body)

        if "embeddings" in raw:
            # batchEmbedContents response
            data: List[Dict] = [
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": emb.get("values", []),
                }
                for i, emb in enumerate(raw["embeddings"])
            ]
        else:
            # embedContent response
            values: List[float] = raw.get("embedding", {}).get("values", [])
            data = [{"object": "embedding", "index": 0, "embedding": values}]

        openai_response: Dict = {
            "object": "list",
            "data": data,
            "model": raw.get("model", self.get_default_model()),
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

        return json.dumps(openai_response, ensure_ascii=False).encode("utf-8")

    def get_env_key_name(self) -> str:
        return "GEMINI_API_KEY"

    def get_default_model(self) -> str:
        return "gemini-embedding-001"

    def get_default_upstream(self) -> str:
        return _GEMINI_EMBED_BASE
