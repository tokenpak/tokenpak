"""Jina AI embedding adapter."""

from __future__ import annotations

import json
import os
from typing import Dict, Mapping, Optional, Tuple

from .canonical import CanonicalEmbeddingRequest
from .embedding_base import EmbeddingAdapter

_JINA_UPSTREAM = "https://api.jina.ai"

# Maps canonical input_type values to Jina task LoRA parameter values.
# Values not in this map are passed through unchanged.
_INPUT_TYPE_TO_TASK: Dict[str, str] = {
    "query": "retrieval.query",
    "document": "retrieval.passage",
    "text-matching": "text-matching",
    "classification": "classification",
    "separation": "separation",
}


class JinaEmbeddingAdapter(EmbeddingAdapter):
    """Embedding adapter for Jina AI.

    Detection: requests whose body model field starts with ``jina-embeddings-``.

    Field mapping (canonical → Jina):
        input_type      → task  (via _INPUT_TYPE_TO_TASK; unknown values passed through)
        encoding_format → embedding_type
        truncate        → truncate  (passed through verbatim)
        normalized      → normalized  (passed through verbatim)

    Auth: ``Authorization: Bearer $JINA_API_KEY``

    Response normalisation:
        - data[].object = 'embedding' injected if missing
        - usage.total_tokens mirrors prompt_tokens (Jina omits total_tokens)
    """

    source_format = "jina-embeddings"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Return True if the request targets a Jina embedding model.

        Matches model names starting with ``jina-embeddings-`` or path ``/v1/embeddings``
        combined with an ``Authorization: Bearer jina_`` header.
        """
        if body:
            try:
                data = json.loads(body)
                model = str(data.get("model", ""))
                if model.startswith("jina-embeddings-"):
                    return True
            except (json.JSONDecodeError, AttributeError):
                pass
        # Path-based detection when Authorization header carries a Jina API key
        if path.rstrip("/").endswith("/v1/embeddings"):
            auth = headers.get("Authorization", "") or headers.get("authorization", "")
            if "jina_" in auth:
                return True
        return False

    def normalize_request(
        self, canonical: CanonicalEmbeddingRequest
    ) -> Tuple[str, Dict[str, str], bytes]:
        """Convert canonical embedding request to Jina AI wire format.

        Returns:
            (url, headers, body) ready to forward to https://api.jina.ai/v1/embeddings.
        """
        payload: Dict = {
            "model": canonical.model,
            "input": canonical.input,
            # encoding_format → embedding_type
            "embedding_type": canonical.encoding_format,
            # truncate passed through verbatim
            "truncate": canonical.truncate,
            # normalized passed through verbatim
            "normalized": canonical.normalized,
        }

        # Map canonical input_type → Jina task LoRA parameter
        if canonical.input_type is not None:
            payload["task"] = _INPUT_TYPE_TO_TASK.get(
                canonical.input_type, canonical.input_type
            )
        elif canonical.task is not None:
            # task already in Jina format — pass through directly
            payload["task"] = canonical.task

        # dimensions not supported by Jina v1 embeddings; omit silently

        # Preserve any unknown canonical fields
        payload.update(canonical.raw_extra)

        api_key = os.environ.get(self.get_env_key_name(), "")
        out_headers: Dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{_JINA_UPSTREAM}/v1/embeddings"
        out_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return url, out_headers, out_body

    def normalize_response(self, status: int, headers: Dict[str, str], body: bytes) -> bytes:
        """Convert Jina response body to OpenAI-compatible embedding format.

        Ensures:
        - data[].object == 'embedding' is present on every embedding item.
        - usage.total_tokens mirrors prompt_tokens (Jina returns only prompt_tokens).
        """
        data = json.loads(body)

        # Inject object='embedding' on items that lack it
        for item in data.get("data", []):
            if "object" not in item:
                item["object"] = "embedding"

        # Copy prompt_tokens → total_tokens so callers see both fields
        usage = data.get("usage", {})
        if "prompt_tokens" in usage and "total_tokens" not in usage:
            usage["total_tokens"] = usage["prompt_tokens"]
        data["usage"] = usage

        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def get_env_key_name(self) -> str:
        return "JINA_API_KEY"

    def get_default_model(self) -> str:
        return "jina-embeddings-v3"

    def get_default_upstream(self) -> str:
        return _JINA_UPSTREAM
