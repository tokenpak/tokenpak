"""
TokenPak Anthropic SDK Adapter

Routes Anthropic ``messages.create`` requests through the TokenPak proxy.
Preserves the full Anthropic Messages API shape on both request and response
so callers require zero code changes beyond swapping in this adapter.

Request format handled:
  {
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 4096,
    "system": "...",
    "messages": [{"role": "user", "content": "..."}],
    ...
  }

Token extraction
----------------
Anthropic reports exact usage in every non-streaming response:
  ``usage.input_tokens``, ``usage.output_tokens``,
  ``usage.cache_read_input_tokens``, ``usage.cache_creation_input_tokens``

Error handling
--------------
- 401/403 → TokenPakAuthError
- timeout → TokenPakTimeoutError
- provider error block in response → TokenPakAdapterError
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

_log = logging.getLogger("tokenpak.adapters.anthropic")

# Required fields in every request
_REQUIRED_FIELDS = frozenset({"model", "messages", "max_tokens"})

# Anthropic stop_reason → canonical finish_reason
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    "tool_use": "tool_use",
}


class AnthropicAdapter(TokenPakAdapter):
    """TokenPak adapter for the Anthropic Messages API.

    Usage
    -----
    >>> adapter = AnthropicAdapter(
    ...     base_url="http://127.0.0.1:8767",
    ...     api_key="sk-ant-...",
    ... )
    >>> response = adapter.call({
    ...     "model": "claude-3-5-sonnet-20241022",
    ...     "max_tokens": 1024,
    ...     "messages": [{"role": "user", "content": "Hello"}],
    ... })
    >>> tokens = adapter.extract_tokens(response)
    """

    provider_name: str = "anthropic"

    PROXY_PATH: str = "/v1/messages"
    ANTHROPIC_VERSION: str = "2023-06-01"

    # ── prepare_request ───────────────────────────────────────────────────

    def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise an Anthropic request.

        Validation:
        - ``model``, ``messages``, ``max_tokens`` must be present
        - ``messages`` must be a non-empty list
        - Each message must have ``role`` (str) and ``content`` (str | list)

        Normalisation:
        - Strips unknown top-level keys that would be rejected by the proxy
          (conservative: passes through everything; proxy decides)
        - Adds ``stream: false`` default if not specified
        """
        missing = _REQUIRED_FIELDS - request.keys()
        if missing:
            raise TokenPakConfigError(
                f"AnthropicAdapter.prepare_request: missing required fields: {sorted(missing)}"
            )

        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise TokenPakConfigError(
                "AnthropicAdapter.prepare_request: 'messages' must be a non-empty list."
            )

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise TokenPakConfigError(
                    f"AnthropicAdapter.prepare_request: messages[{i}] must be a dict."
                )
            if "role" not in msg or "content" not in msg:
                raise TokenPakConfigError(
                    f"AnthropicAdapter.prepare_request: messages[{i}] must have 'role' and 'content'."
                )

        prepared = dict(request)
        prepared.setdefault("stream", False)

        self.logger.debug(
            "prepare_request model=%s messages=%d",
            prepared.get("model"),
            len(messages),
        )
        return prepared

    # ── send ──────────────────────────────────────────────────────────────

    def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]:
        """POST to ``{base_url}/v1/messages`` through the proxy."""
        if not _REQUESTS_AVAILABLE:
            raise TokenPakAdapterError(
                "AnthropicAdapter.send: 'requests' package is required. "
                "Install with: pip install requests"
            )

        url = f"{self.base_url}{self.PROXY_PATH}"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
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
                f"AnthropicAdapter.send: request timed out after {self.timeout_s}s."
            ) from exc
        except _requests.exceptions.RequestException as exc:
            raise TokenPakAdapterError(
                f"AnthropicAdapter.send: HTTP transport error: {exc}"
            ) from exc

        elapsed_ms = (time.monotonic() - t0) * 1000
        self.logger.info("send complete status=%d elapsed_ms=%.1f", resp.status_code, elapsed_ms)

        if resp.status_code in (401, 403):
            raise TokenPakAuthError(
                f"AnthropicAdapter.send: authentication failed (HTTP {resp.status_code}).",
                status_code=resp.status_code,
            )

        if not resp.ok:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            raise TokenPakAdapterError(
                f"AnthropicAdapter.send: proxy returned HTTP {resp.status_code}.",
                status_code=resp.status_code,
                raw=err_body,
            )

        try:
            return resp.json()
        except Exception as exc:
            raise TokenPakAdapterError(
                "AnthropicAdapter.send: response body is not valid JSON."
            ) from exc

    # ── parse_response ────────────────────────────────────────────────────

    def parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Validate proxy response and surface provider errors.

        Returns the response unchanged if valid; raises ``TokenPakAdapterError``
        when the response contains an Anthropic error block.
        """
        error = response.get("error")
        if error:
            if isinstance(error, dict):
                msg = error.get("message", str(error))
                err_type = error.get("type", "unknown")
            else:
                msg, err_type = str(error), "unknown"
            raise TokenPakAdapterError(
                f"AnthropicAdapter.parse_response: provider error [{err_type}]: {msg}",
                raw=response,
            )

        if "content" not in response and "type" not in response:
            self.logger.warning(
                "parse_response: response missing 'content' and 'type' — "
                "may be malformed: %s",
                list(response.keys()),
            )

        return response

    # ── extract_tokens ────────────────────────────────────────────────────

    def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]:
        """Extract Anthropic usage block.

        Returns zeros with a warning if ``usage`` is absent (e.g. streaming
        partial chunks where billing data hasn't arrived yet).
        """
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

        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
        cache_write = int(usage.get("cache_creation_input_tokens", 0))

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "total": input_tokens + output_tokens,
        }
