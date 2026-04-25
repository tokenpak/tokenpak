"""Provider format adapters for TokenPak proxy."""

from .anthropic_adapter import AnthropicAdapter
from .base import FormatAdapter
from .canonical import CanonicalRequest, CanonicalResponse
from .google_adapter import GoogleGenerativeAIAdapter
from .openai_chat_adapter import OpenAIChatAdapter
from .openai_codex_responses_adapter import OpenAICodexResponsesAdapter
from .openai_responses_adapter import OpenAIResponsesAdapter
from .passthrough_adapter import PassthroughAdapter
from .registry import AdapterRegistry


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(AnthropicAdapter(), priority=300)
    registry.register(OpenAICodexResponsesAdapter(), priority=270)
    registry.register(OpenAIResponsesAdapter(), priority=260)
    registry.register(OpenAIChatAdapter(), priority=250)
    registry.register(GoogleGenerativeAIAdapter(), priority=240)
    registry.register(PassthroughAdapter(), priority=0)
    return registry


__all__ = [
    "AdapterRegistry",
    "AnthropicAdapter",
    "CanonicalRequest",
    "CanonicalResponse",
    "FormatAdapter",
    "GoogleGenerativeAIAdapter",
    "OpenAIChatAdapter",
    "OpenAICodexResponsesAdapter",
    "OpenAIResponsesAdapter",
    "PassthroughAdapter",
    "build_default_registry",
]
