"""Provider format adapters for TokenPak proxy."""

from .anthropic_adapter import AnthropicAdapter
from .base import FormatAdapter
from .canonical import CanonicalRequest, CanonicalResponse
from .embedding_router import EmbeddingRouter
from .gemini_embedding_adapter import GeminiEmbeddingAdapter
from .google_adapter import GoogleGenerativeAIAdapter
from .grok_adapter import GrokAdapter
from .jina_embedding_adapter import JinaEmbeddingAdapter
from .ollama_embedding_adapter import OllamaEmbeddingAdapter
from .openai_chat_adapter import OpenAIChatAdapter
from .openai_codex_responses_adapter import OpenAICodexResponsesAdapter
from .openai_embedding_adapter import OpenAIEmbeddingAdapter
from .openai_responses_adapter import OpenAIResponsesAdapter
from .passthrough_adapter import PassthroughAdapter
from tokenpak.proxy.adapters.registry import AdapterRegistry
from .voyage_embedding_adapter import VoyageEmbeddingAdapter


# Higher priority values are checked first when matching requests to adapters.
def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(AnthropicAdapter(), priority=300)
    # Codex adapter higher priority than standard Responses — JWT requests match first
    registry.register(OpenAICodexResponsesAdapter(), priority=270)
    registry.register(OpenAIResponsesAdapter(), priority=260)
    registry.register(OpenAIChatAdapter(), priority=250)
    registry.register(GoogleGenerativeAIAdapter(), priority=240)
    registry.register(
        GrokAdapter(), priority=255
    )  # Above OpenAI (250) — detect before generic chat
    registry.register(PassthroughAdapter(), priority=0)
    return registry


def build_embedding_registry() -> EmbeddingRouter:
    """Create and return an EmbeddingRouter with all embedding adapters registered.

    Provider availability is determined at construction time by scanning env vars.
    Priority order: Voyage > OpenAI > Gemini > Jina > Ollama.
    """
    return EmbeddingRouter()


__all__ = ['AdapterRegistry', 'AnthropicAdapter', 'CanonicalRequest', 'CanonicalResponse', 'EmbeddingRouter', 'FormatAdapter', 'GeminiEmbeddingAdapter', 'GoogleGenerativeAIAdapter', 'GrokAdapter', 'JinaEmbeddingAdapter', 'OllamaEmbeddingAdapter', 'OpenAIChatAdapter', 'OpenAICodexResponsesAdapter', 'OpenAIEmbeddingAdapter', 'OpenAIResponsesAdapter', 'PassthroughAdapter', 'VoyageEmbeddingAdapter', 'build_default_registry', 'build_embedding_registry', 'adapters', 'anthropic_adapter', 'base', 'canonical', 'embedding_base', 'embedding_router', 'embedding_voyage', 'gemini_embedding_adapter', 'google_adapter', 'grok_adapter', 'jina_embedding', 'jina_embedding_adapter', 'ollama_embedding_adapter', 'openai_chat_adapter', 'openai_codex_responses_adapter', 'openai_embedding', 'openai_embedding_adapter', 'openai_responses_adapter', 'passthrough_adapter', 'registry', 'utils', 'voyage_embedding_adapter']
