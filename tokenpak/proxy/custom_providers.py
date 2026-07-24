"""
Custom provider registration from ~/.tokenpak/config.yaml.

Users can register any OpenAI-compatible (or Anthropic/Google-compatible)
endpoint without editing Python source code.  Three lines of YAML is all
it takes::

    providers:
      my-local-llm:
        endpoint: http://localhost:8000/v1
        format: openai
        api_key_env: MY_LLM_API_KEY

      deepseek:
        endpoint: https://api.deepseek.com/v1
        format: openai
        api_key_env: DEEPSEEK_API_KEY

At proxy startup the loader:
  1. Reads the ``providers`` section from config.yaml.
  2. Creates a lightweight adapter per provider (delegates to the matching
     built-in format adapter for normalise/denormalise).
  3. Adds each provider's hostname to the intercept list so requests get
     the full compression/caching pipeline.
  4. Registers upstream routes so the proxy knows where to forward.

See ``load_custom_providers()`` for the public API.
"""

from __future__ import annotations

__all__ = (
    "CustomProvider",
    "build_custom_adapters",
    "get_provider_display_list",
    "load_custom_providers",
)


import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional
from urllib.parse import urlparse

from tokenpak.proxy.adapters.base import FormatAdapter, TokenCounter
from tokenpak.proxy.adapters.canonical import CanonicalRequest
from tokenpak.proxy.adapters.registry import AdapterRegistry

logger = logging.getLogger(__name__)

# Supported format names → the source_format value of the built-in adapter
# that handles normalise/denormalise for that wire format.
_FORMAT_ALIASES: dict[str, str] = {
    "openai": "openai-chat",
    "openai-chat": "openai-chat",
    "openai-responses": "openai-responses",
    "anthropic": "anthropic-messages",
    "anthropic-messages": "anthropic-messages",
    "google": "google-generative-ai",
    "google-generative-ai": "google-generative-ai",
}


@dataclass
class CustomProvider:
    """Parsed representation of a single custom provider entry."""

    name: str
    endpoint: str  # e.g. "https://api.deepseek.com/v1"
    format: str  # resolved source_format, e.g. "openai-chat"
    api_key_env: str  # env var name holding the API key
    hostname: str  # extracted from endpoint, e.g. "api.deepseek.com"
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def api_key(self) -> Optional[str]:
        """Resolve the API key from the environment (never stored in memory)."""
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


def load_custom_providers() -> list[CustomProvider]:
    """Load custom providers from ``~/.tokenpak/config.yaml``.

    Returns a (possibly empty) list of ``CustomProvider`` objects.
    Never raises -- config errors are logged and the offending entry skipped.
    """
    try:
        from tokenpak import config_loader as _cl
    except ImportError:
        return []

    cfg = _cl.load_config()
    if not isinstance(cfg, dict):
        return []

    providers_section = cfg.get("providers")
    if not isinstance(providers_section, dict):
        return []

    result: list[CustomProvider] = []
    for name, entry in providers_section.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            logger.warning("custom_providers: skipping invalid entry %r", name)
            continue

        endpoint_value = entry.get("endpoint", "")
        if not isinstance(endpoint_value, str) or not endpoint_value.strip():
            logger.warning("custom_providers: %s missing 'endpoint', skipping", name)
            continue
        endpoint = endpoint_value.strip()

        # Normalise endpoint -- strip trailing slash for consistency
        endpoint = endpoint.rstrip("/")

        # Resolve format
        format_value = entry.get("format", "openai")
        if not isinstance(format_value, str):
            logger.warning("custom_providers: %s has non-string 'format', skipping", name)
            continue
        raw_format = format_value.strip().lower()
        resolved = _FORMAT_ALIASES.get(raw_format)
        if resolved is None:
            logger.warning(
                "custom_providers: %s has unknown format %r (expected one of %s), skipping",
                name,
                raw_format,
                ", ".join(sorted(_FORMAT_ALIASES)),
            )
            continue

        api_key_env_value = entry.get("api_key_env", "")
        if not isinstance(api_key_env_value, str):
            logger.warning("custom_providers: %s has non-string 'api_key_env', skipping", name)
            continue
        api_key_env = api_key_env_value.strip()

        # Extract hostname for intercept matching
        parsed = urlparse(endpoint)
        hostname = parsed.hostname or ""
        if not hostname:
            logger.warning(
                "custom_providers: %s has unparseable endpoint %r, skipping",
                name,
                endpoint,
            )
            continue

        # Collect any extra keys the user specified (future-proofing)
        known_keys = {"endpoint", "format", "api_key_env"}
        extra = {k: v for k, v in entry.items() if k not in known_keys}

        result.append(
            CustomProvider(
                name=name,
                endpoint=endpoint,
                format=resolved,
                api_key_env=api_key_env,
                hostname=hostname,
                extra=extra,
            )
        )

    if result:
        names = ", ".join(p.name for p in result)
        logger.info("custom_providers: loaded %d provider(s): %s", len(result), names)

    return result


# ---------------------------------------------------------------------------
# Adapter factory -- creates a FormatAdapter subclass for a custom provider
# ---------------------------------------------------------------------------


def _make_custom_adapter(
    provider: CustomProvider,
) -> Callable[[FormatAdapter, CustomProvider], FormatAdapter]:
    """Create a FormatAdapter that detects requests to a custom provider's
    hostname and delegates normalise/denormalise to the matching built-in
    format adapter.

    The returned adapter class is a thin wrapper: it only overrides
    ``detect()`` (hostname matching) and ``get_default_upstream()``
    (provider endpoint).  Everything else (normalise, denormalise,
    token extraction, SSE format) is delegated to the built-in adapter.
    """

    class _CustomProviderAdapter(FormatAdapter):
        """Auto-generated adapter for custom provider '{name}'."""

        source_format = f"custom-{provider.name}"

        def __init__(self, delegate: FormatAdapter, cp: CustomProvider) -> None:
            self._delegate = delegate
            self._provider = cp

        def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
            # Match by hostname in URL path (forward proxy) or Host header
            if self._provider.hostname in path:
                return True
            lower = {k.lower(): v for k, v in headers.items()}
            host = lower.get("host", "")
            if self._provider.hostname in host:
                return True
            return False

        def normalize(self, body: bytes) -> CanonicalRequest:
            return self._delegate.normalize(body)

        def denormalize(self, canonical: CanonicalRequest) -> bytes:
            return self._delegate.denormalize(canonical)

        def get_default_upstream(self) -> str:
            return self._provider.endpoint

        def get_sse_format(self) -> str:
            return self._delegate.get_sse_format()

        def extract_request_tokens(
            self, body: bytes, token_counter: TokenCounter | None = None
        ) -> tuple[str, int]:
            return self._delegate.extract_request_tokens(body, token_counter)

        def extract_response_tokens(self, body: bytes, is_sse: bool = False) -> int:
            return self._delegate.extract_response_tokens(body, is_sse)

        def extract_query_signal(self, body: bytes) -> str:
            return self._delegate.extract_query_signal(body)

        def inject_system_context(self, body: bytes, injection_text: str) -> bytes:
            return self._delegate.inject_system_context(body, injection_text)

        def __repr__(self) -> str:
            return (
                f"CustomProviderAdapter(name={self._provider.name!r}, "
                f"endpoint={self._provider.endpoint!r}, "
                f"format={self._provider.format!r})"
            )

    _CustomProviderAdapter.__doc__ = f"Auto-generated adapter for custom provider '{provider.name}'"
    return _CustomProviderAdapter


def build_custom_adapters(
    providers: list[CustomProvider],
    registry: AdapterRegistry,
) -> list[FormatAdapter]:
    """Build adapter instances for custom providers and return them.

    Args:
        providers: List of CustomProvider objects from ``load_custom_providers()``.
        registry: The ``AdapterRegistry`` containing built-in adapters.  Used
            to look up the delegate adapter for each provider's wire format.

    Returns:
        List of instantiated custom adapter objects (already registered in
        *registry* with priority 200 -- above passthrough but below built-in
        providers so built-in detection takes precedence for known hosts).
    """
    if not providers:
        return []

    # Build a lookup: source_format -> adapter instance
    format_lookup: dict[str, FormatAdapter] = {}
    for adapter in registry.adapters():
        format_lookup[adapter.source_format] = adapter

    created: list[FormatAdapter] = []
    for cp in providers:
        delegate = format_lookup.get(cp.format)
        if delegate is None:
            logger.warning(
                "custom_providers: %s needs format %r but no adapter is "
                "registered for it -- skipping",
                cp.name,
                cp.format,
            )
            continue

        adapter_cls = _make_custom_adapter(cp)
        adapter_inst = adapter_cls(delegate, cp)
        # Priority 200 -- below built-in adapters (240-300) but above
        # passthrough (0).  Custom providers should not shadow built-in hosts.
        registry.register(adapter_inst, priority=200)
        created.append(adapter_inst)
        logger.debug(
            "custom_providers: registered adapter for %s -> %s",
            cp.name,
            cp.endpoint,
        )

    return created


def get_provider_display_list(
    registry: AdapterRegistry,
    custom_providers: list[CustomProvider],
) -> str:
    """Return a human-readable provider list for the startup banner.

    Built-in providers are listed by their canonical name.  Custom providers
    get a ``(custom)`` suffix.

    Example output::

        anthropic, openai, google, xai-grok, my-local-llm (custom), deepseek (custom)
    """
    # Built-in adapters (exclude passthrough and custom-* adapters)
    builtin_names: list[str] = []
    for adapter in registry.adapters():
        fmt = adapter.source_format
        if fmt == "passthrough" or fmt.startswith("custom-"):
            continue
        builtin_names.append(fmt)

    # Custom providers
    custom_names = [f"{cp.name} (custom)" for cp in custom_providers]

    all_names = builtin_names + custom_names
    return ", ".join(all_names) if all_names else "(none)"
