"""tokenpak.sdk.claude_cli — Claude Code CLI adapter."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

from tokenpak.sdk.base import (
    TokenPakAdapter,
    TokenPakAdapterError,
    TokenPakAuthError,
    TokenPakTimeoutError,
)

_log = logging.getLogger("tokenpak.sdk.claude_cli")


class ClaudeCLIAdapter(TokenPakAdapter):
    """Adapter for Claude Code CLI environments.

    In Claude CLI mode, the proxy handles auth — api_key is optional.
    Requests are forwarded as-is (no normalization needed).
    """

    provider_name = "claude_cli"

    PROXY_PATH: str = "/v1/messages"

    def __init__(self, base_url: str = "", api_key: str = "") -> None:
        url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8766")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        super().__init__(base_url=url, api_key=key)

    def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]:
        return request

    def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]:
        """POST to the TokenPak proxy."""
        if not _REQUESTS_AVAILABLE:
            raise TokenPakAdapterError(
                "ClaudeCLIAdapter.send: 'requests' package is required. "
                "Install with: pip install requests"
            )

        url = f"{self.base_url}{self.PROXY_PATH}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        self.logger.debug("send POST %s", url)
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
                f"ClaudeCLIAdapter.send: request timed out after {self.timeout_s}s."
            ) from exc
        except _requests.exceptions.RequestException as exc:
            raise TokenPakAdapterError(
                f"ClaudeCLIAdapter.send: HTTP transport error: {exc}"
            ) from exc

        elapsed_ms = (time.monotonic() - t0) * 1000
        self.logger.info("send complete status=%d elapsed_ms=%.1f", resp.status_code, elapsed_ms)

        if resp.status_code in (401, 403):
            raise TokenPakAuthError(
                f"ClaudeCLIAdapter.send: authentication failed (HTTP {resp.status_code}).",
                status_code=resp.status_code,
            )

        if not resp.ok:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            raise TokenPakAdapterError(
                f"ClaudeCLIAdapter.send: proxy returned HTTP {resp.status_code}.",
                status_code=resp.status_code,
                raw=err_body,
            )

        try:
            return resp.json()
        except Exception as exc:
            raise TokenPakAdapterError(
                "ClaudeCLIAdapter.send: response body is not valid JSON."
            ) from exc

    def parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        return response

    def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]:
        usage = response.get("usage", {})
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "total": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        }


__all__ = ["ClaudeCLIAdapter"]
