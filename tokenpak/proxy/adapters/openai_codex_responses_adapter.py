# SPDX-License-Identifier: Apache-2.0
"""OpenAI Codex Responses adapter — routes ``/v1/responses`` + JWT to ChatGPT.

Restored 2026-04-24 from the Apr-12 build (deleted in the Apr-16
wave-4 cleanup). Ported as a functional reiteration against the
current modular tree, not a verbatim copy.

Why this exists
---------------

The standard :class:`OpenAIResponsesAdapter` routes ``/v1/responses``
traffic to ``api.openai.com`` and authenticates with an OpenAI
``sk-`` API key. ChatGPT's Codex offering uses the same Responses
API wire format but a *different upstream* (``chatgpt.com/backend-api``)
and a *different auth shape* (JWT OAuth from the Codex CLI's
``~/.codex/auth.json``). Calling ``api.openai.com`` with a ChatGPT
JWT returns 401 every time.

This adapter sits at priority 270 (above generic OpenAIResponses at
260) and matches ``/v1/responses`` requests whose ``Authorization``
header carries a JWT (``Bearer eyJ…``). The standard adapter still
matches the API-key case at priority 260, so direct OpenAI traffic
keeps working unchanged.

Relationship to ``credential_injector``
---------------------------------------

The v1.3.15 ``CodexCredentialProvider`` in
``services/routing_service/credential_injector.py`` handles the
*platform-bridged* path — when OpenClaw or another adapter sets
``X-TokenPak-Backend`` / ``X-TokenPak-Provider: tokenpak-openai-codex``,
the proxy's pre-forward hook reads ``~/.codex/auth.json``, injects
the right headers, rewrites the URL, and normalises the body. That
runs *before* adapter selection.

This adapter handles the *direct* path — when a Codex CLI client (or
any caller carrying a JWT directly) hits the proxy at
``/v1/responses``, the adapter selects itself by token shape and
applies the same upstream / path / payload-shape transforms via the
adapter registry's normal denormalize→forward flow. No double
injection because the bridge path returns its own response before the
adapter registry runs.

Payload constraints
-------------------

The ChatGPT Codex backend rejects requests that don't conform to:

- ``stream: true`` always
- ``store: false`` always (no server-side conversation persistence)
- no ``max_output_tokens`` parameter (the backend computes its own cap)
- ``input`` as a list (string form is auto-promoted to a single
  ``user``-role text block)

These are enforced in :meth:`denormalize`. Tokenpak's compression
pipeline runs against the *canonical* form before denormalize, so
the constraints don't interfere with capsule injection / compaction.
"""

from __future__ import annotations

import copy
import json
from typing import Mapping, Optional

from .canonical import CanonicalRequest
from .openai_responses_adapter import OpenAIResponsesAdapter

# JWT detection: ChatGPT OAuth tokens are RFC-7519 JWTs that always
# start with ``eyJ`` (base64url of ``{"``). OpenAI API keys start
# with ``sk-``. No overlap, so a single prefix check is sufficient.
_JWT_PREFIX = "eyJ"


def _is_chatgpt_oauth_token(auth_header: str) -> bool:
    """True when ``Authorization`` carries a ChatGPT OAuth JWT."""
    if not auth_header:
        return False
    token = auth_header
    if auth_header[:7].lower() == "bearer ":
        token = auth_header[7:].strip()
    if not token:
        return False
    return token.startswith(_JWT_PREFIX) and "." in token


class OpenAICodexResponsesAdapter(OpenAIResponsesAdapter):
    """Codex Responses adapter — same wire format, different upstream."""

    source_format = "openai-codex-responses"

    # ChatGPT's Codex backend path. Used when the request's URL is
    # rewritten on the way out.
    CODEX_PATH = "/codex/responses"

    # ChatGPT backend host. Inherits ``/codex/responses`` to form the
    # full upstream URL.
    CODEX_UPSTREAM = "https://chatgpt.com/backend-api"

    def detect(
        self,
        path: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
    ) -> bool:
        """Match ``/v1/responses`` traffic carrying a ChatGPT OAuth JWT.

        Selected at priority 270 — checked before
        :class:`OpenAIResponsesAdapter` (priority 260). API-key
        traffic falls through to the standard adapter unchanged.
        """
        if "/v1/responses" not in path:
            return False
        # Header lookup is case-insensitive at the HTTP layer; we check
        # both common cases without converting the entire mapping.
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        return _is_chatgpt_oauth_token(auth)

    def get_default_upstream(self) -> str:
        return self.CODEX_UPSTREAM

    def get_sse_format(self) -> str:
        # ChatGPT's Codex backend streams in the same format as the
        # OpenAI Responses API SSE feed.
        return "openai-responses-sse"

    def get_upstream_path(self) -> str:
        """Path rewrite: ``/v1/responses`` → ``/codex/responses``."""
        return self.CODEX_PATH

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        """Apply the ChatGPT Codex backend's payload constraints.

        Required by the upstream:
          - ``stream: true``
          - ``store: false``
          - no ``max_output_tokens`` field
          - ``input`` is a list, not a string

        We delegate the wire-format work to the parent
        :meth:`OpenAIResponsesAdapter.denormalize` and then patch the
        result so the ChatGPT backend accepts it.
        """
        base_bytes = super().denormalize(canonical)
        try:
            payload = json.loads(base_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Parent gave us non-JSON for some reason — pass through;
            # the upstream will reject and the caller sees the error.
            return base_bytes

        if not isinstance(payload, dict):
            return base_bytes

        payload["stream"] = True
        payload["store"] = False
        payload.pop("max_output_tokens", None)

        # The ChatGPT backend wants ``input`` as a list of message
        # objects, not a raw string. The parent class may emit a
        # string when the canonical input is a single user turn; we
        # promote that into the structured form here.
        input_value = payload.get("input")
        if isinstance(input_value, str):
            text = input_value
            if text:
                payload["input"] = [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    }
                ]
            else:
                payload["input"] = []
        elif input_value is None:
            payload["input"] = []
        else:
            # Already a list / dict — pass through but defensively
            # deep-copy so any downstream mutation can't poison our
            # canonical state.
            payload["input"] = copy.deepcopy(input_value)

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")


__all__ = ["OpenAICodexResponsesAdapter"]
