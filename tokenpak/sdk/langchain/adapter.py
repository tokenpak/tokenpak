"""
TokenPak LangChain Adapter

Bridges LangChain's ``ChatOpenAI`` and ``ChatAnthropic`` call pattern
to the TokenPak proxy via a unified ``TokenPakAdapter`` interface.
"""

from __future__ import annotations

from typing import Any

from tokenpak.sdk.anthropic import AnthropicAdapter
from tokenpak.sdk.base import TokenPakAdapter, TokenPakConfigError
from tokenpak.sdk.openai import OpenAIAdapter

# LangChain role → canonical role mapping
_ROLE_MAP: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "function": "tool",
    "tool": "tool",
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
    """TokenPak adapter for LangChain-style requests."""

    provider_name: str = "langchain"

    def __init__(self, base_url: str, api_key: str, timeout_s: float | None = None) -> None:
        super().__init__(base_url, api_key, timeout_s)
        self._openai = OpenAIAdapter(base_url, api_key, timeout_s)
        self._anthropic = AnthropicAdapter(base_url, api_key, timeout_s)

    def _get_delegate(self, provider: str) -> TokenPakAdapter:
        p = provider.lower()
        if p in ("openai", "gpt", "chatgpt"):
            return self._openai
        if p in ("anthropic", "claude"):
            return self._anthropic
        raise TokenPakConfigError(
            f"LangChainAdapter: unknown provider '{provider}'. Supported: 'openai', 'anthropic'."
        )

    def prepare_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if "messages" not in request:
            raise TokenPakConfigError(
                "LangChainAdapter.prepare_request: 'messages' field is required."
            )

        provider = request.get("provider", "openai")
        delegate = self._get_delegate(provider)

        prepared = dict(request)
        prepared["messages"] = _normalise_messages(request["messages"])
        prepared.pop("provider", None)
        prepared.setdefault("_tokenpak_source", "langchain")

        return delegate.prepare_request(prepared)

    def send(self, prepared_request: dict[str, Any]) -> dict[str, Any]:
        model: str = prepared_request.get("model", "")
        if model.startswith(("claude", "anthropic")):
            return self._anthropic.send(prepared_request)
        return self._openai.send(prepared_request)

    def parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        if "content" in response and "stop_reason" in response:
            return self._anthropic.parse_response(response)
        return self._openai.parse_response(response)

    def extract_tokens(self, response: dict[str, Any]) -> dict[str, Any]:
        if "content" in response and "stop_reason" in response:
            return self._anthropic.extract_tokens(response)
        return self._openai.extract_tokens(response)
