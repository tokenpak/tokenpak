"""Provider format adapters for TokenPak proxy."""

from .anthropic_adapter import AnthropicAdapter
from .base import FormatAdapter
from .canonical import CanonicalRequest, CanonicalResponse
from .discovery import (
    discover_entry_point_adapters,
    discover_filesystem_adapters,
    discovery_enabled,
    register_discovered,
)
from .google_adapter import GoogleGenerativeAIAdapter
from .openai_chat_adapter import OpenAIChatAdapter
from .openai_responses_adapter import OpenAIResponsesAdapter
from .passthrough_adapter import PassthroughAdapter
from .registry import AdapterRegistry


def build_default_registry() -> AdapterRegistry:
    """Closed-set factory: only the built-in TokenPak format adapters.

    Use this when tests or callers need a deterministic registry
    independent of the host's installed plugins or filesystem state.
    """
    registry = AdapterRegistry()
    registry.register(AnthropicAdapter(), priority=300)
    registry.register(OpenAIResponsesAdapter(), priority=260)
    registry.register(OpenAIChatAdapter(), priority=250)
    registry.register(GoogleGenerativeAIAdapter(), priority=240)
    registry.register(PassthroughAdapter(), priority=0)
    return registry


def build_registry() -> AdapterRegistry:
    """Production factory: built-ins + discovered plugin adapters.

    Mirrors the MCP-style integration UX — a third party publishes a
    ``tokenpak.format_adapters`` entry point or drops a ``.py`` file
    into ``~/.tokenpak/adapters/`` and the proxy picks the adapter up
    on startup. Built-ins always win on ``source_format`` collision.

    Discovery is suppressed when ``TOKENPAK_DISABLE_ADAPTER_PLUGINS=1``
    (test isolation, conformance suite, paranoid deployments).
    """
    registry = build_default_registry()
    register_discovered(registry)
    return registry


__all__ = [
    "AdapterRegistry",
    "AnthropicAdapter",
    "CanonicalRequest",
    "CanonicalResponse",
    "FormatAdapter",
    "GoogleGenerativeAIAdapter",
    "OpenAIChatAdapter",
    "OpenAIResponsesAdapter",
    "PassthroughAdapter",
    "build_default_registry",
    "build_registry",
    "discover_entry_point_adapters",
    "discover_filesystem_adapters",
    "discovery_enabled",
    "register_discovered",
]
