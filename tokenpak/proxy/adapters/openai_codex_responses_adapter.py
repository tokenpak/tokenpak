"""OpenAI Codex Responses API adapter — routes to chatgpt.com/backend-api.

Extends the standard OpenAI Responses adapter with:
- Default upstream: chatgpt.com/backend-api (ChatGPT OAuth endpoint)
- Detection: /v1/responses with JWT bearer token (not sk- API key)
- Same wire format as openai-responses (Responses API)
- Path rewrite: /v1/responses → /codex/responses (ChatGPT backend path)
- Payload fixup: ensures stream=true, store=false, strips max_output_tokens

This enables TokenPak to proxy Codex subscription traffic with full
compression pipeline support (capsules, vault injection, compaction, etc.)
while routing to the correct ChatGPT backend instead of api.openai.com.

Requires: curl_cffi (pip install curl_cffi) for Cloudflare bypass on
chatgpt.com. Falls back to urllib3 if curl_cffi is unavailable, but
chatgpt.com requests will likely get 403'd by CF in that case.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional

from .openai_responses_adapter import OpenAIResponsesAdapter
from .canonical import CanonicalRequest


# ---------------------------------------------------------------------------
# ChatGPT OAuth tokens are JWTs (start with "eyJ").
# OpenAI API keys start with "sk-".
# This heuristic lets us auto-detect which upstream to use without
# requiring any user configuration — zero-config, just works.
# ---------------------------------------------------------------------------

def _is_chatgpt_oauth_token(auth_header: str) -> bool:
    """Return True if the Authorization header carries a ChatGPT OAuth JWT."""
    if not auth_header:
        return False
    # Strip "Bearer " prefix
    token = auth_header
    lower = auth_header.lower()
    if lower.startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        return False
    # ChatGPT OAuth tokens are JWTs: base64url header.payload.signature
    # They always start with "eyJ" (base64 of '{"')
    # API keys start with "sk-"
    return token.startswith("eyJ") and "." in token


class OpenAICodexResponsesAdapter(OpenAIResponsesAdapter):
    """Codex Responses adapter — same format, different upstream + detection.

    Key differences from standard OpenAI Responses:
    - Upstream: chatgpt.com/backend-api (not api.openai.com)
    - Path: /codex/responses (not /v1/responses)
    - Requires: stream=true, store=false, no max_output_tokens
    - Uses curl_cffi for Cloudflare bypass
    """

    source_format = "openai-codex-responses"

    # The ChatGPT backend path for Codex
    CODEX_PATH = "/codex/responses"

    def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
        """Match Codex requests by path or by JWT auth.

        Three match cases:
        1. Bare ``/codex/responses`` — sent by clients that use
           the chatgpt.com backend path directly. The proxy injects the
           ChatGPT OAuth token, so we can't rely on the client's auth header.
        2. ``/v1/codex/responses`` — explicit Codex namespace via the
           standard ``/v1`` prefix.
        3. ``/v1/responses`` *with* a ChatGPT OAuth JWT in the Authorization
           header — distinguishes Codex traffic from regular OpenAI API
           traffic on the shared ``/v1/responses`` endpoint.
        """
        if "/codex/responses" in path or "/v1/codex/responses" in path:
            return True
        if "/v1/responses" not in path:
            return False
        # /v1/responses with a ChatGPT OAuth JWT → Codex
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        return _is_chatgpt_oauth_token(auth)

    def get_default_upstream(self) -> str:
        return "https://chatgpt.com/backend-api"

    def get_sse_format(self) -> str:
        return "openai-responses-sse"

    def get_upstream_path(self) -> str:
        """Return the correct path for the ChatGPT Codex backend."""
        return self.CODEX_PATH

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        """Denormalize with ChatGPT Codex constraints applied.

        The ChatGPT Codex backend requires:
        - stream: true (always)
        - store: false (always)
        - no max_output_tokens parameter
        - input as a list (not string)
        """
        # Use parent denormalize to get the base payload
        base_bytes = super().denormalize(canonical)
        payload = json.loads(base_bytes)

        # Apply ChatGPT Codex constraints
        payload["stream"] = True
        payload["store"] = False

        # Remove unsupported parameters
        payload.pop("max_output_tokens", None)

        # Ensure input is always a list
        if isinstance(payload.get("input"), str):
            text = payload["input"]
            if text:
                payload["input"] = [
                    {"role": "user", "content": [{"type": "input_text", "text": text}]}
                ]
            else:
                payload["input"] = []

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
