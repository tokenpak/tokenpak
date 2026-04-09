"""Provider format adapters for TokenPak proxy."""

from .anthropic_adapter import AnthropicAdapter
from .base import FormatAdapter
from .canonical import CanonicalRequest, CanonicalResponse
from .google_adapter import GoogleGenerativeAIAdapter
from .grok_adapter import GrokAdapter
from .openai_chat_adapter import OpenAIChatAdapter
from .openai_codex_responses_adapter import OpenAICodexResponsesAdapter
from .openai_responses_adapter import OpenAIResponsesAdapter
from .passthrough_adapter import PassthroughAdapter
from .registry import AdapterRegistry


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(AnthropicAdapter(), priority=300)
    # Codex must be checked before standard OpenAI Responses so
    # /codex/responses paths route to chatgpt.com/backend-api, not api.openai.com.
    registry.register(OpenAICodexResponsesAdapter(), priority=270)
    registry.register(OpenAIResponsesAdapter(), priority=260)
    registry.register(OpenAIChatAdapter(), priority=250)
    registry.register(GoogleGenerativeAIAdapter(), priority=240)
    registry.register(
        GrokAdapter(), priority=255
    )  # Above OpenAI (250) — detect before generic chat
    registry.register(PassthroughAdapter(), priority=0)
    return registry


__all__ = [
    "AdapterRegistry",
    "AnthropicAdapter",
    "CanonicalRequest",
    "CanonicalResponse",
    "FormatAdapter",
    "GoogleGenerativeAIAdapter",
    "GrokAdapter",
    "OpenAIChatAdapter",
    "OpenAICodexResponsesAdapter",
    "OpenAIResponsesAdapter",
    "PassthroughAdapter",
    "build_default_registry",
]
