"""
tokenpak.adapters — Unified SDK/framework adapter layer.

All adapters share the ``TokenPakAdapter`` base contract:

    prepare_request(request)  → normalised proxy dict
    send(prepared_request)    → raw proxy response dict
    parse_response(response)  → provider-native response dict
    extract_tokens(response)  → token-usage summary dict

Quick start
-----------
>>> from tokenpak.adapters import AnthropicAdapter, OpenAIAdapter
>>> adapter = OpenAIAdapter(base_url="http://127.0.0.1:8767", api_key="sk-...")
>>> response = adapter.call({"model": "gpt-4o", "messages": [...]})
>>> usage = adapter.extract_tokens(response)

See also
--------
- ``tokenpak.adapters.base``     — abstract base + exception hierarchy
- ``tokenpak.adapters.anthropic`` — Anthropic Messages API
- ``tokenpak.adapters.openai``    — OpenAI Chat Completions API
- ``tokenpak.adapters.langchain`` — LangChain (ChatOpenAI / ChatAnthropic)
- ``tokenpak.adapters.litellm``   — LiteLLM provider-agnostic routing
"""

from tokenpak.adapters.anthropic import AnthropicAdapter
from tokenpak.adapters.base import (
    TokenPakAdapter,
    TokenPakAdapterError,
    TokenPakAuthError,
    TokenPakConfigError,
    TokenPakTimeoutError,
)
from tokenpak.adapters.langchain import LangChainAdapter
from tokenpak.adapters.litellm import LiteLLMAdapter
from tokenpak.adapters.openai import OpenAIAdapter

__all__ = [
    # Base
    "TokenPakAdapter",
    "TokenPakAdapterError",
    "TokenPakAuthError",
    "TokenPakConfigError",
    "TokenPakTimeoutError",
    # Concrete adapters
    "AnthropicAdapter",
    "OpenAIAdapter",
    "LangChainAdapter",
    "LiteLLMAdapter",
]
