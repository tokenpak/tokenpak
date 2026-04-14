"""Provider format adapters for TokenPak proxy."""

from .anthropic_adapter import AnthropicAdapter
from .base import FormatAdapter
from .canonical import CanonicalRequest, CanonicalResponse
from .google_adapter import GoogleGenerativeAIAdapter
from .grok_adapter import GrokAdapter
from .openai_chat_adapter import OpenAIChatAdapter
from .openai_responses_adapter import OpenAIResponsesAdapter
from .passthrough_adapter import PassthroughAdapter
from tokenpak.proxy.adapters.registry import AdapterRegistry


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(AnthropicAdapter(), priority=300)
    registry.register(OpenAIResponsesAdapter(), priority=260)
    registry.register(OpenAIChatAdapter(), priority=250)
    registry.register(GoogleGenerativeAIAdapter(), priority=240)
    registry.register(
        GrokAdapter(), priority=255
    )  # Above OpenAI (250) — detect before generic chat
    registry.register(PassthroughAdapter(), priority=0)
    return registry


__all__ = ['AdapterRegistry', 'AnthropicAdapter', 'CanonicalRequest', 'CanonicalResponse', 'FormatAdapter', 'GoogleGenerativeAIAdapter', 'GrokAdapter', 'OpenAIChatAdapter', 'OpenAIResponsesAdapter', 'PassthroughAdapter', 'build_default_registry', 'anthropic_adapter', 'base', 'canonical', 'google_adapter', 'grok_adapter', 'openai_chat_adapter', 'openai_responses_adapter', 'passthrough_adapter', 'registry']
