"""
TokenPak OpenAI SDK Adapter

Routes OpenAI ``chat.completions.create`` requests through the TokenPak proxy.
Preserves the full OpenAI Chat Completions API shape on both request and response.

Request format handled:
  {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "..."}],
    "max_tokens": 4096,     # optional
    "tools": [...],         # optional
    ...
  }

Also handles the legacy ``functions`` key (auto-promoted to ``tools``).

Token extraction
----------------
  ``usage.prompt_tokens``  → ``input_tokens``
  ``usage.completion_tokens`` → ``output_tokens``
  ``usage.prompt_tokens_details.cached_tokens`` → ``cache_read``

Error handling
--------------
- 401/403 → TokenPakAuthError
- timeout → TokenPakTimeoutError
- ``error`` block in response → TokenPakAdapterError
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

from tokenpak.adapters.base import (
    TokenPakAdapter,
    TokenPakAdapterError,
    TokenPakAuthError,
    TokenPakConfigError,
    TokenPakTimeoutError,
)

_log = logging.getLogger("tokenpak.adapters.openai")

_REQUIRED_FIELDS = frozenset({"model", "messages"})

_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "stop",
}


class OpenAIAdapter(TokenPakAdapter):
    """TokenPak adapter for the OpenAI Chat Completions API.

    Usage
    -----
    >>> adapter = OpenAIAdapter(
    ...     base_url="http://127.0.0.1:8767",
    ...     api_key="sk-...",
    ... )
    >>> response = adapter.call({
    ...     "model": "gpt-4o",
    ...     "messages": [{"role": "user", "content": "Hello"}],
    ... })
    >>> tokens = adapter.extract_tokens(response)
    """

    provider_name: str = "openai"

    PROXY_PATH: str = "/v1/chat/completions"

    # ── prepare_request ───────────────────────────────────────────────────

    def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise an OpenAI request.

        - Validates required fields and message structure
        - Promotes legacy ``functions`` → ``tools``
        - Adds ``stream: false`` default
        """
        missing = _REQUIRED_FIELDS - request.keys()
        if missing:
            raise TokenPakConfigError(
                f"OpenAIAdapter.prepare_request: missing required fields: {sorted(missing)}"
            )

        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise TokenPakConfigError(
                "OpenAIAdapter.prepare_request: 'messages' must be a non-empty list."
            )

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise TokenPakConfigError(
                    f"OpenAIAdapter.prepare_request: messages[{i}] must be a dict."
                )
            if "role" not in msg:
                raise TokenPakConfigError(
                    f"OpenAIAdapter.prepare_request: messages[{i}] must have 'role'."
                )

        prepared = dict(request)

        # Promote legacy functions → tools
        if "functions" in prepared and "tools" not in prepared:
            prepared["tools"] = [
                {"type": "function", "function": fn}
                for fn in prepared.pop("functions")
            ]
        elif "functions" in prepared:
            prepared.pop("functions")  # tools already present, discard duplicate

        prepared.setdefault("stream", False)

        self.logger.debug(
            "prepare_request model=%s messages=%d",
            prepared.get("model"),
            len(messages),
        )
        return prepared

    # ── send ──────────────────────────────────────────────────────────────

    def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]:
        """POST to ``{base_url}/v1/chat/completions`` through the proxy."""
        if not _REQUESTS_AVAILABLE:
            raise TokenPakAdapterError(
                "OpenAIAdapter.send: 'requests' package is required. "
                "Install with: pip install requests"
            )

        url = f"{self.base_url}{self.PROXY_PATH}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        self.logger.debug("send POST %s model=%s", url, prepared_request.get("model"))
        t0 = time.monotonic()

        try:
            resp = _requests.post(
                url,
                headers=headers,
                data=json.dumps(prepared_request, ensure_ascii=False).encode("utf-8"),
                timeout=self.timeout_s,
            )
        except _requests.exceptions.Timeout as exc:
            raise TokenPakTimeoutError(
                f"OpenAIAdapter.send: request timed out after {self.timeout_s}s."
            ) from exc
        except _requests.exceptions.RequestException as exc:
            raise TokenPakAdapterError(
                f"OpenAIAdapter.send: HTTP transport error: {exc}"
            ) from exc

        elapsed_ms = (time.monotonic() - t0) * 1000
        self.logger.info("send complete status=%d elapsed_ms=%.1f", resp.status_code, elapsed_ms)

        if resp.status_code in (401, 403):
            raise TokenPakAuthError(
                f"OpenAIAdapter.send: authentication failed (HTTP {resp.status_code}).",
                status_code=resp.status_code,
            )

        if not resp.ok:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            raise TokenPakAdapterError(
                f"OpenAIAdapter.send: proxy returned HTTP {resp.status_code}.",
                status_code=resp.status_code,
                raw=err_body,
            )

        try:
            return resp.json()
        except Exception as exc:
            raise TokenPakAdapterError(
                "OpenAIAdapter.send: response body is not valid JSON."
            ) from exc

    # ── parse_response ────────────────────────────────────────────────────

    def parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Validate proxy response and surface provider errors."""
        error = response.get("error")
        if error:
            if isinstance(error, dict):
                msg = error.get("message", str(error))
                err_type = error.get("type", "unknown")
            else:
                msg, err_type = str(error), "unknown"
            raise TokenPakAdapterError(
                f"OpenAIAdapter.parse_response: provider error [{err_type}]: {msg}",
                raw=response,
            )

        if "choices" not in response:
            self.logger.warning(
                "parse_response: response missing 'choices' key — may be malformed: %s",
                list(response.keys()),
            )

        return response

    # ── extract_tokens ────────────────────────────────────────────────────

    def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]:
        """Extract OpenAI Chat Completions usage block."""
        usage = response.get("usage", {})
        if not usage:
            self.logger.warning(
                "extract_tokens: no 'usage' block in response — returning zeros."
            )
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total": 0,
            }

        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))

        details = usage.get("prompt_tokens_details") or {}
        cache_read = int(details.get("cached_tokens", 0))

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read": cache_read,
            "cache_write": 0,  # OpenAI does not expose cache-write counts
            "total": input_tokens + output_tokens,
        }
