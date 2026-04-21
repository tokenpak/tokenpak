"""
TokenPak LangChain Adapter

Bridges LangChain's ``ChatOpenAI`` and ``ChatAnthropic`` call pattern
to the TokenPak proxy via a unified ``TokenPakAdapter`` interface.

This adapter does NOT require langchain to be installed — it operates
on raw request dicts that match the LangChain output format.  If you
want a drop-in LangChain LLM class, see
``tokenpak.integrations.langchain`` (planned).

Request format handled
----------------------
LangChain serialises messages as::

    {
      "model": "...",
      "messages": [
        {"role": "system", "content": "..."},
        {"role": "human", "content": "..."},
        {"role": "ai", "content": "..."},
      ],
      "provider": "openai" | "anthropic"
    }

LangChain uses ``"human"`` and ``"ai"`` role names instead of
``"user"`` and ``"assistant"``.  This adapter normalises them before
forwarding to the underlying provider adapter.

Token extraction
----------------
Delegates to the underlying Anthropic or OpenAI adapter, depending on
``provider`` field in the request.

Error handling
--------------
Same hierarchy as other adapters: ``TokenPakAdapterError``,
``TokenPakTimeoutError``, ``TokenPakAuthError``.
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

_log = logging.getLogger("tokenpak.adapters.langchain")

# LangChain role → canonical role mapping
_ROLE_MAP: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "function": "tool",
    "tool": "tool",
    # passthrough
    "user": "user",
    "assistant": "assistant",
}


def _normalise_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate LangChain role names to provider-standard names."""
    normalised = []
    for msg in messages:
        norm = dict(msg)
        original_role = msg.get("role", "user")
        norm["role"] = _ROLE_MAP.get(original_role, original_role)
        normalised.append(norm)
    return normalised


class LangChainAdapter(TokenPakAdapter):
    """TokenPak adapter for LangChain-style requests.

    Routes to the underlying ``AnthropicAdapter`` or ``OpenAIAdapter``
    based on the ``provider`` field in the request (default: ``"openai"``).

    Usage
    -----
    >>> adapter = LangChainAdapter(
    ...     base_url="http://127.0.0.1:8767",
    ...     api_key="sk-...",
    ... )
    >>> response = adapter.call({
    ...     "model": "gpt-4o",
    ...     "provider": "openai",
    ...     "messages": [
    ...         {"role": "system", "content": "You are helpful."},
    ...         {"role": "human", "content": "Hello"},
    ...     ],
    ... })
    """

    provider_name: str = "langchain"

    def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None:
        super().__init__(base_url, api_key, timeout_s)
        self._openai = OpenAIAdapter(base_url, api_key, timeout_s)
        self._anthropic = AnthropicAdapter(base_url, api_key, timeout_s)

    def _get_delegate(self, provider: str) -> TokenPakAdapter:
        """Return the underlying provider adapter."""
        p = provider.lower()
        if p in ("openai", "gpt", "chatgpt"):
            return self._openai
        elif p in ("anthropic", "claude"):
            return self._anthropic
        else:
            raise TokenPakConfigError(
                f"LangChainAdapter: unknown provider '{provider}'. "
                f"Supported: 'openai', 'anthropic'."
            )

    # ── prepare_request ───────────────────────────────────────────────────

    def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Normalise LangChain roles and delegate to provider adapter.

        Mutates a copy of the request:
        1. Resolves ``"human"``/``"ai"`` → ``"user"``/``"assistant"``
        2. Injects ``_langchain: true`` metadata for proxy audit log
        3. Delegates to Anthropic or OpenAI ``prepare_request``
        """
        if "messages" not in request:
            raise TokenPakConfigError(
                "LangChainAdapter.prepare_request: 'messages' field is required."
            )

        provider = request.get("provider", "openai")
        delegate = self._get_delegate(provider)

        prepared = dict(request)
        prepared["messages"] = _normalise_messages(request["messages"])
        # remove LangChain-specific metadata before forwarding
        prepared.pop("provider", None)
        prepared.setdefault("_tokenpak_source", "langchain")

        self.logger.debug(
            "prepare_request provider=%s model=%s messages=%d",
            provider,
            prepared.get("model"),
            len(prepared["messages"]),
        )

        return delegate.prepare_request(prepared)

    def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]:
        """Delegate send to the matching provider adapter."""
        # Infer provider from prepared request shape
        model: str = prepared_request.get("model", "")
        if model.startswith(("claude", "anthropic")):
            return self._anthropic.send(prepared_request)
        return self._openai.send(prepared_request)

    def parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Delegate response parsing to the matching provider adapter."""
        # Infer from response shape
        if "content" in response and "stop_reason" in response:
            return self._anthropic.parse_response(response)
        return self._openai.parse_response(response)

    def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]:
        """Delegate token extraction to the matching provider adapter."""
        if "content" in response and "stop_reason" in response:
            return self._anthropic.extract_tokens(response)
        return self._openai.extract_tokens(response)
