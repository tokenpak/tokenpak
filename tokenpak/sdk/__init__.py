"""
tokenpak.sdk — Unified SDK/framework adapter layer.

All adapters share the ``TokenPakAdapter`` base contract:

    prepare_request(request)  → normalised proxy dict
    send(prepared_request)    → raw proxy response dict
    parse_response(response)  → provider-native response dict
    extract_tokens(response)  → token-usage summary dict

Quick start
-----------
>>> from tokenpak.sdk import AnthropicAdapter, OpenAIAdapter
>>> adapter = OpenAIAdapter(base_url="http://127.0.0.1:8767", api_key="sk-...")
>>> response = adapter.call({"model": "gpt-4o", "messages": [...]})
>>> usage = adapter.extract_tokens(response)

See also
--------
- ``tokenpak.sdk.base``     — abstract base + exception hierarchy
- ``tokenpak.sdk.anthropic`` — Anthropic Messages API
- ``tokenpak.sdk.openai``    — OpenAI Chat Completions API
- ``tokenpak.sdk.langchain`` — LangChain (ChatOpenAI / ChatAnthropic)
- ``tokenpak.sdk.litellm``   — LiteLLM provider-agnostic routing
"""

from tokenpak.sdk.anthropic import AnthropicAdapter
from tokenpak.sdk.base import (
    TokenPakAdapter,
    TokenPakAdapterError,
    TokenPakAuthError,
    TokenPakConfigError,
    TokenPakTimeoutError,
)
from tokenpak.sdk.langchain import LangChainAdapter
from tokenpak.sdk.litellm import LiteLLMAdapter
from tokenpak.sdk.openai import OpenAIAdapter

__all__ = [
    "TokenPakAdapter",
    "TokenPakAdapterError",
    "TokenPakAuthError",
    "TokenPakConfigError",
    "TokenPakTimeoutError",
    "AnthropicAdapter",
    "OpenAIAdapter",
    "LangChainAdapter",
    "LiteLLMAdapter",
    "anthropic",
    "autogen",
    "base",
    "claude_cli",
    "crewai",
    "generic",
    "langchain",
    "langchain_adapter",
    "litellm",
    "llamaindex",
    "local",
    "openai",
    "openclaw",
    "registry",
]
