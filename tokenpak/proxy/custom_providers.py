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
     the applicable route-policy and telemetry handling.
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
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from tokenpak.proxy.adapters.base import FormatAdapter, TokenCounter
from tokenpak.proxy.adapters.canonical import CanonicalRequest
from tokenpak.proxy.adapters.registry import AdapterRegistry
from tokenpak.proxy.router import (
    _endpoint_identity,
    _endpoint_parts,
    _host_header_parts,
    _normalize_configured_endpoint,
)

logger = logging.getLogger(__name__)

# Custom adapters match an explicitly configured hostname.  They must be
# considered before the generic wire-format adapters, whose path predicates
# (for example ``/v1/chat/completions``) intentionally match many providers.
CUSTOM_PROVIDER_PRIORITY = 400

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

_ENV_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
        """Resolve the API key on demand without retaining it on this object."""
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def endpoint_identity(self) -> str:
        """Normalized scheme, host, and effective port used for routing."""
        return _endpoint_identity(self.endpoint)

    @property
    def effective_port(self) -> int:
        parts = _endpoint_parts(self.endpoint)
        if parts is None:  # construction is validated by the loader
            raise ValueError(f"invalid custom-provider endpoint: {self.endpoint!r}")
        return parts[2]


def count_configured_providers() -> int:
    """Return the number of raw entries under the configured provider map.

    This intentionally counts invalid entries too.  Comparing it with the
    successfully registered count is how startup and doctor expose a partial
    load instead of silently presenting skipped configuration as absent.
    """
    from tokenpak.core import config_loader as _cl

    cfg = _cl.load_config()
    providers_section = cfg.get("providers") if isinstance(cfg, dict) else None
    return len(providers_section) if isinstance(providers_section, dict) else 0


def load_custom_providers() -> list[CustomProvider]:
    """Load custom providers from ``~/.tokenpak/config.yaml``.

    Returns a (possibly empty) list of valid ``CustomProvider`` objects.
    Invalid provider entries are logged and skipped.
    """
    from tokenpak.core import config_loader as _cl

    cfg = _cl.load_config()
    if not isinstance(cfg, dict):
        return []

    providers_section = cfg.get("providers")
    if not isinstance(providers_section, dict):
        return []

    result: list[CustomProvider] = []
    seen_endpoints: set[str] = set()
    for name, entry in providers_section.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            logger.warning("custom_providers: skipping invalid entry %r", name)
            continue
        if not name.strip():
            logger.warning("custom_providers: skipping provider with an empty name")
            continue

        endpoint_value = entry.get("endpoint", "")
        if not isinstance(endpoint_value, str) or not endpoint_value.strip():
            logger.warning("custom_providers: %s missing 'endpoint', skipping", name)
            continue
        endpoint = endpoint_value.strip()

        try:
            endpoint, endpoint_identity = _normalize_configured_endpoint(endpoint)
        except ValueError as exc:
            logger.warning("custom_providers: %s has invalid endpoint: %s; skipping", name, exc)
            continue

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
        if api_key_env and _ENV_VAR_NAME.fullmatch(api_key_env) is None:
            logger.warning(
                "custom_providers: %s has invalid 'api_key_env' name %r, skipping",
                name,
                api_key_env,
            )
            continue

        parts = _endpoint_parts(endpoint)
        assert parts is not None  # guaranteed by _normalize_configured_endpoint
        _scheme, hostname, _port = parts
        if endpoint_identity in seen_endpoints:
            logger.warning(
                "custom_providers: %s duplicates configured endpoint %s, skipping",
                name,
                endpoint_identity,
            )
            continue
        seen_endpoints.add(endpoint_identity)

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
    *,
    hostname_unique: bool,
    authority_unique: bool,
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
            # The custom adapter uses the same wire contract as its delegate.
            # Copy the declaration explicitly rather than inheriting an
            # accidental capability set from the base class.
            self.capabilities = frozenset(delegate.capabilities)

        def detect(self, path: str, headers: Mapping[str, str], body: Optional[bytes]) -> bool:
            if "://" in path:
                return _endpoint_identity(path) == self._provider.endpoint_identity

            host_value = next(
                (str(value) for key, value in headers.items() if str(key).lower() == "host"),
                "",
            )
            host_parts = _host_header_parts(host_value)
            if host_parts is None:
                return False
            hostname, explicit_port = host_parts
            if hostname != self._provider.hostname:
                return False
            if explicit_port is not None:
                return authority_unique and explicit_port == self._provider.effective_port
            # A portless Host is safe only when this configured hostname maps
            # to exactly one custom endpoint. Otherwise the request is
            # ambiguous and generic wire-format detection must handle it.
            return hostname_unique

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
        *registry* ahead of generic format adapters).  The custom predicate is
        hostname-exact, so this precedence cannot capture unrelated traffic.
    """
    if not providers:
        return []

    # Build a lookup: source_format -> adapter instance
    format_lookup: dict[str, FormatAdapter] = {}
    for adapter in registry.adapters():
        format_lookup[adapter.source_format] = adapter

    created: list[FormatAdapter] = []
    hostname_counts = Counter(provider.hostname for provider in providers)
    authority_counts = Counter(
        (provider.hostname, provider.effective_port) for provider in providers
    )
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

        adapter_cls = _make_custom_adapter(
            cp,
            hostname_unique=hostname_counts[cp.hostname] == 1,
            authority_unique=authority_counts[(cp.hostname, cp.effective_port)] == 1,
        )
        adapter_inst = adapter_cls(delegate, cp)
        registry.register(adapter_inst, priority=CUSTOM_PROVIDER_PRIORITY)
        created.append(adapter_inst)
        logger.debug(
            "custom_providers: registered adapter for %s -> %s",
            cp.name,
            cp.endpoint,
        )

    return created


def get_provider_display_list(
    registry: AdapterRegistry,
    registered_providers: list[CustomProvider],
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

    # Only providers with a successfully registered adapter are displayable.
    custom_names = [f"{cp.name} (custom)" for cp in registered_providers]

    all_names = builtin_names + custom_names
    return ", ".join(all_names) if all_names else "(none)"
