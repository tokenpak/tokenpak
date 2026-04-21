"""
TokenPak Unified Adapter Base — TokenPakAdapter

All SDK/framework adapters (Anthropic, OpenAI, LangChain, LiteLLM, etc.)
inherit from this class and implement the four lifecycle hooks.

Lifecycle
---------
1. ``prepare_request(request)``  — validate & normalise SDK request → proxy format
2. ``send(prepared_request)``    — POST to TokenPak proxy, handle errors/timeouts
3. ``parse_response(response)``  — proxy response → SDK-native format
4. ``extract_tokens(response)``  — pull token-usage counts for budgeting

Error Handling Contract
-----------------------
All concrete adapters MUST:
- Wrap HTTP errors in ``TokenPakAdapterError``
- Wrap timeout errors in ``TokenPakTimeoutError``
- Wrap auth/config errors in ``TokenPakConfigError``
- Never raise bare ``requests.exceptions.*`` or provider SDK exceptions
  directly — always translate to the canonical hierarchy.

Logging
-------
Use ``self.logger`` (standard ``logging.Logger``) for all log output.
Log levels:
- DEBUG   : request/response body summaries (no credentials)
- INFO    : round-trip timings, cache-hit notices
- WARNING : retried requests, degraded fallbacks
- ERROR   : terminal failures before raising exceptions
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

# ── Canonical exception hierarchy ─────────────────────────────────────────


class TokenPakAdapterError(Exception):
    """Base exception for all TokenPak adapter errors."""

    def __init__(self, message: str, status_code: int | None = None, raw: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw


class TokenPakTimeoutError(TokenPakAdapterError):
    """Raised when the proxy does not respond within ``timeout_s`` seconds."""


class TokenPakConfigError(TokenPakAdapterError):
    """Raised when required configuration (base_url, api_key) is missing/invalid."""


class TokenPakAuthError(TokenPakAdapterError):
    """Raised on 401/403 responses from the proxy."""


# ── Base adapter ───────────────────────────────────────────────────────────


class TokenPakAdapter(ABC):
    """Abstract base class for all TokenPak SDK/framework adapters.

    Parameters
    ----------
    base_url:
        TokenPak proxy endpoint, e.g. ``"http://127.0.0.1:8767"``.
        Must not have a trailing slash.
    api_key:
        Provider API key forwarded transparently through the proxy.
    timeout_s:
        Request timeout in seconds.  Defaults to 120.
    """

    #: Subclasses set this to a stable, lowercase identifier (e.g. "anthropic").
    provider_name: str = "unknown"

    DEFAULT_TIMEOUT_S: float = 120.0

    def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None:
        if not base_url:
            raise TokenPakConfigError("base_url must not be empty.")
        if not api_key:
            raise TokenPakConfigError("api_key must not be empty.")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s if timeout_s is not None else self.DEFAULT_TIMEOUT_S
        self.logger = logging.getLogger(f"tokenpak.adapters.{self.provider_name}")

    # ── Lifecycle hooks ────────────────────────────────────────────────────

    @abstractmethod
    def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise an SDK request dict into proxy format.

        Parameters
        ----------
        request:
            Raw dict as the SDK caller would build it (model, messages, etc.).

        Returns
        -------
        dict
            Proxy-ready request dict.  Must contain at least ``"model"``
            and ``"messages"`` (or the provider's equivalent).

        Raises
        ------
        TokenPakConfigError
            If required fields are missing or have invalid types.
        """

    @abstractmethod
    def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]:
        """POST *prepared_request* to the TokenPak proxy and return the response.

        Parameters
        ----------
        prepared_request:
            The dict returned by ``prepare_request``.

        Returns
        -------
        dict
            Raw response dict from the proxy.

        Raises
        ------
        TokenPakTimeoutError
            If the request exceeds ``self.timeout_s``.
        TokenPakAuthError
            On 401/403 responses.
        TokenPakAdapterError
            For all other HTTP/transport errors.
        """

    @abstractmethod
    def parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Convert a raw proxy response into the provider's native SDK format.

        Parameters
        ----------
        response:
            Raw dict returned by ``send``.

        Returns
        -------
        dict
            Response shaped exactly as the provider's SDK would return it,
            so callers can switch to TokenPak without code changes.

        Raises
        ------
        TokenPakAdapterError
            If the response is malformed or contains a provider error.
        """

    @abstractmethod
    def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]:
        """Extract token usage counts from a response.

        Parameters
        ----------
        response:
            Either the raw proxy dict or the parsed SDK-format dict.

        Returns
        -------
        dict with keys:
            - ``input_tokens``  (int) — billed input tokens
            - ``output_tokens`` (int) — billed output tokens
            - ``cache_read``    (int) — tokens served from cache (0 if N/A)
            - ``cache_write``   (int) — tokens written to cache (0 if N/A)
            - ``total``         (int) — ``input_tokens + output_tokens``
        """

    # ── Convenience helpers ────────────────────────────────────────────────

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        """Full pipeline: prepare → send → parse_response.

        Returns the provider-native response dict.  Logs round-trip time
        at INFO level.

        Parameters
        ----------
        request:
            Raw SDK-style request dict.
        """
        t0 = time.monotonic()
        prepared = self.prepare_request(request)
        raw_response = self.send(prepared)
        parsed = self.parse_response(raw_response)
        elapsed_ms = (time.monotonic() - t0) * 1000
        self.logger.info(
            "call complete provider=%s model=%s elapsed_ms=%.1f",
            self.provider_name,
            request.get("model", "?"),
            elapsed_ms,
        )
        return parsed

    def __repr__(self) -> str:
        return f"<{type(self).__name__} provider={self.provider_name!r} base_url={self.base_url!r}>"
