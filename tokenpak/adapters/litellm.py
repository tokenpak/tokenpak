"""
TokenPak LiteLLM Adapter

Bridges LiteLLM's provider-agnostic call format to the TokenPak proxy.

LiteLLM uses a unified ``completion(model, messages, ...)`` interface that
routes to any provider using a ``provider/model`` prefix convention:
- ``"openai/gpt-4o"``
- ``"anthropic/claude-3-5-sonnet-20241022"``
- ``"gpt-4o"`` (implicitly OpenAI)

This adapter parses the model string to pick the correct underlying
provider adapter, strips the prefix, then forwards to Anthropic or OpenAI.

Request format handled
----------------------
LiteLLM passes the same OpenAI-compatible ``messages`` format for all
providers::

    {
      "model": "anthropic/claude-3-5-sonnet-20241022",
      "messages": [{"role": "user", "content": "Hello"}],
      "max_tokens": 1024,   # optional
    }

Provider detection
------------------
1. ``model`` starts with ``"anthropic/"`` → AnthropicAdapter
2. ``model`` starts with ``"openai/"``    → OpenAIAdapter
3. ``model`` starts with ``"claude"``     → AnthropicAdapter
4. ``model`` starts with ``"gpt"``        → OpenAIAdapter
5. fallback                               → OpenAIAdapter (LiteLLM default)

Token extraction
----------------
Delegates to the resolved provider adapter.

Error handling
--------------
Same hierarchy: ``TokenPakAdapterError``, ``TokenPakTimeoutError``,
``TokenPakAuthError``, ``TokenPakConfigError``.
"""

from __future__ import annotations

import logging
from typing import Any

from tokenpak.adapters.anthropic import AnthropicAdapter
from tokenpak.adapters.base import (
    TokenPakAdapter,
    TokenPakConfigError,
)
from tokenpak.adapters.openai import OpenAIAdapter

_log = logging.getLogger("tokenpak.adapters.litellm")

# LiteLLM provider-prefix → adapter key
_PREFIX_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "gpt": "openai",
    "gpt-": "openai",
    "o1": "openai",
    "o3": "openai",
    "chatgpt": "openai",
}


def _resolve_provider(model: str) -> tuple[str, str]:
    """Return ``(provider_key, bare_model_name)`` from a LiteLLM model string.

    Examples
    --------
    >>> _resolve_provider("anthropic/claude-3-5-sonnet-20241022")
    ("anthropic", "claude-3-5-sonnet-20241022")
    >>> _resolve_provider("gpt-4o")
    ("openai", "gpt-4o")
    """
    if "/" in model:
        prefix, bare = model.split("/", 1)
        key = _PREFIX_MAP.get(prefix.lower(), "openai")
        return key, bare

    # No prefix — infer from model name
    lower = model.lower()
    for prefix, key in _PREFIX_MAP.items():
        if lower.startswith(prefix):
            return key, model

    # Default fallback: OpenAI (LiteLLM default behaviour)
    return "openai", model


class LiteLLMAdapter(TokenPakAdapter):
    """TokenPak adapter for LiteLLM-style requests.

    Automatically resolves the provider from the model string and
    delegates to the appropriate underlying adapter.

    Usage
    -----
    >>> adapter = LiteLLMAdapter(
    ...     base_url="http://127.0.0.1:8767",
    ...     api_key="sk-...",
    ... )
    >>> # OpenAI via LiteLLM prefix
    >>> response = adapter.call({
    ...     "model": "openai/gpt-4o",
    ...     "messages": [{"role": "user", "content": "Hi"}],
    ... })
    >>> # Anthropic via LiteLLM prefix
    >>> response = adapter.call({
    ...     "model": "anthropic/claude-3-5-sonnet-20241022",
    ...     "messages": [{"role": "user", "content": "Hi"}],
    ...     "max_tokens": 512,
    ... })
    >>> tokens = adapter.extract_tokens(response)
    """

    provider_name: str = "litellm"

    def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None:
        super().__init__(base_url, api_key, timeout_s)
        self._openai = OpenAIAdapter(base_url, api_key, timeout_s)
        self._anthropic = AnthropicAdapter(base_url, api_key, timeout_s)
        self._delegates: dict[str, TokenPakAdapter] = {
            "openai": self._openai,
            "anthropic": self._anthropic,
        }

    def _delegate_for_model(self, model: str) -> tuple[TokenPakAdapter, str]:
        """Return ``(delegate_adapter, bare_model_name)``."""
        key, bare = _resolve_provider(model)
        delegate = self._delegates.get(key, self._openai)
        self.logger.debug("resolved model=%r → provider=%s bare=%s", model, key, bare)
        return delegate, bare

    # ── prepare_request ───────────────────────────────────────────────────

    def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Parse LiteLLM model string, strip provider prefix, delegate.

        Strips provider prefix from ``model`` (e.g.
        ``"anthropic/claude-3-5-sonnet-20241022"`` → ``"claude-3-5-sonnet-20241022"``)
        before forwarding to the underlying adapter so the proxy sees the
        bare model name.
        """
        if "model" not in request:
            raise TokenPakConfigError("LiteLLMAdapter.prepare_request: 'model' field is required.")
        if "messages" not in request:
            raise TokenPakConfigError(
                "LiteLLMAdapter.prepare_request: 'messages' field is required."
            )

        delegate, bare_model = self._delegate_for_model(request["model"])

        prepared = dict(request)
        prepared["model"] = bare_model
        prepared.setdefault("_tokenpak_source", "litellm")

        self.logger.debug(
            "prepare_request original_model=%r bare=%r messages=%d",
            request["model"],
            bare_model,
            len(request.get("messages", [])),
        )

        return delegate.prepare_request(prepared)

    def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]:
        """Delegate send based on the resolved provider."""
        model: str = prepared_request.get("model", "")
        delegate, _ = self._delegate_for_model(model)
        return delegate.send(prepared_request)

    def parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Delegate response parsing based on response shape."""
        # Anthropic responses have 'content' + 'stop_reason'
        if "content" in response and "stop_reason" in response:
            return self._anthropic.parse_response(response)
        return self._openai.parse_response(response)

    def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]:
        """Delegate token extraction based on response shape."""
        if "content" in response and "stop_reason" in response:
            return self._anthropic.extract_tokens(response)
        return self._openai.extract_tokens(response)
