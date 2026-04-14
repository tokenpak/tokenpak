"""
CacheSpec — Normalized cache configuration schema for TokenPak.

CACHE-P4-001: Phase 4 of provider-agnostic prompt cache support.

Defines a unified abstraction layer that maps provider-agnostic configuration
to provider-specific cache behavior:
- CacheMode: four normalized cache modes
- FallbackPolicy: behavior when a requested mode isn't supported
- CacheSpec: unified cache configuration dataclass
- PROVIDER_CACHE_MODES: capability map (which modes each provider supports)
- resolve_cache_mode: central resolution (request hint > config override > default)
- load_cache_spec_from_config: build CacheSpec from tokenpak config

Config schema (config.yaml):
    cache:
      enabled: true
      fallback_policy: best-effort   # strict | best-effort | provider-default
      default_mode: null             # prefix_auto | block_explicit | cache_object | checkpoint

      anthropic:
        mode: block_explicit         # per-provider override
      openai:
        mode: prefix_auto
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from tokenpak.core.runtime.providers import Provider


__all__ = [
    "CacheMode",
    "FallbackPolicy",
    "CacheSpec",
    "PROVIDER_CACHE_MODES",
    "default_cache_mode",
    "resolve_cache_mode",
    "load_cache_spec_from_config",
]


class CacheMode(Enum):
    """Provider-agnostic cache mode identifiers."""

    PREFIX_AUTO = "prefix_auto"
    """Automatic prefix caching — provider tracks the stable prefix and caches it
    transparently. Supported by OpenAI, Azure OpenAI, Groq, Fireworks, Together,
    xAI, Codex, and Anthropic (auto mode via top-level cache_control)."""

    BLOCK_EXPLICIT = "block_explicit"
    """Per-block cache markers — the client explicitly marks which content blocks
    should be cached. Supported by Anthropic (cache_control on system/messages/tools)
    and Bedrock (as a secondary mode)."""

    CACHE_OBJECT = "cache_object"
    """External cache object reference — a pre-created cache resource is referenced
    per request. Supported by Gemini (cachedContent)."""

    CHECKPOINT = "checkpoint"
    """Insertion-based checkpoints — cache boundary markers are injected between
    messages. Supported by Bedrock (cachePoint blocks)."""


class FallbackPolicy(Enum):
    """Behavior when the requested cache mode isn't supported by the provider."""

    STRICT = "strict"
    """Raise ValueError if the requested mode isn't supported."""

    BEST_EFFORT = "best-effort"
    """Downgrade to the best available mode for the provider."""

    PROVIDER_DEFAULT = "provider-default"
    """Return None — let the provider handle caching automatically."""


@dataclass
class CacheSpec:
    """Unified cache configuration. Loaded once from tokenpak config.

    Provides defaults for all cache decisions across providers. Request-level
    hints (headers / body fields) always override config-level settings.
    """

    enabled: bool = True
    default_mode: Optional[CacheMode] = None
    """Global default mode. None = auto-select the best mode per provider."""
    fallback_policy: FallbackPolicy = FallbackPolicy.BEST_EFFORT
    provider_overrides: dict[str, dict] = field(default_factory=dict)
    """Per-provider config overrides keyed by Provider.value string.
    e.g. {"anthropic": {"mode": "block_explicit"}}"""


# ---------------------------------------------------------------------------
# Provider capability map — which cache modes each provider supports.
# The FIRST entry is the default mode for that provider.
# ---------------------------------------------------------------------------

PROVIDER_CACHE_MODES: dict[Provider, list[CacheMode]] = {
    # Anthropic: explicit (legacy default) and auto prefix mode (added CACHE-P3-001)
    Provider.ANTHROPIC:    [CacheMode.BLOCK_EXPLICIT, CacheMode.PREFIX_AUTO],
    # OpenAI-family: automatic prefix caching via prompt_cache_key
    Provider.OPENAI:       [CacheMode.PREFIX_AUTO],
    Provider.AZURE_OPENAI: [CacheMode.PREFIX_AUTO],
    Provider.XAI:          [CacheMode.PREFIX_AUTO],
    Provider.GROQ:         [CacheMode.PREFIX_AUTO],
    Provider.FIREWORKS:    [CacheMode.PREFIX_AUTO],
    Provider.TOGETHER:     [CacheMode.PREFIX_AUTO],
    # Codex uses the same Responses API as OpenAI (prompt_cache_key + prompt_cache_retention)
    Provider.CODEX:        [CacheMode.PREFIX_AUTO],
    # Gemini: external cachedContent object reference
    Provider.GEMINI:       [CacheMode.CACHE_OBJECT],
    # Bedrock: checkpoint-based (primary) and explicit block markers (secondary)
    Provider.BEDROCK:      [CacheMode.CHECKPOINT, CacheMode.BLOCK_EXPLICIT],
    # Unknown: no cache support
    Provider.UNKNOWN:      [],
}


def default_cache_mode(provider: Provider) -> Optional[CacheMode]:
    """Return the default (first supported) cache mode for a provider, or None."""
    modes = PROVIDER_CACHE_MODES.get(provider, [])
    return modes[0] if modes else None


def resolve_cache_mode(
    spec: CacheSpec,
    provider: Provider,
    request_hint: Optional[str] = None,
) -> Optional[CacheMode]:
    """Resolve the effective cache mode for a request.

    Priority order (highest to lowest):
    1. Request-level hint — from header (x-tokenpak-cache-mode) or body field
    2. Provider-level override in CacheSpec config (cache.<provider>.mode)
    3. Global default_mode in CacheSpec config
    4. Provider default — first entry in PROVIDER_CACHE_MODES

    Returns None when:
    - Cache is disabled (spec.enabled is False)
    - Provider has no supported modes (Provider.UNKNOWN)
    - fallback_policy is PROVIDER_DEFAULT and the requested mode isn't supported

    Args:
        spec: The loaded CacheSpec configuration.
        provider: Detected provider for this request.
        request_hint: Optional normalized mode string from the request
            (e.g. "prefix_auto", "block_explicit"). Unknown values are ignored.

    Raises:
        ValueError: When fallback_policy is STRICT and the requested mode isn't
            supported by the provider.
    """
    if not spec.enabled:
        return None

    supported = PROVIDER_CACHE_MODES.get(provider, [])

    # 1. Request-level hint takes highest priority
    if request_hint:
        try:
            requested = CacheMode(request_hint)
        except ValueError:
            pass  # Unknown hint string — fall through to config/default
        else:
            if requested in supported:
                return requested
            # Hint is unsupported by this provider — apply fallback policy
            if spec.fallback_policy == FallbackPolicy.STRICT:
                raise ValueError(
                    f"Provider {provider.value!r} does not support cache mode {requested.value!r}. "
                    f"Supported: {[m.value for m in supported]}"
                )
            if spec.fallback_policy == FallbackPolicy.BEST_EFFORT:
                return default_cache_mode(provider)
            return None  # PROVIDER_DEFAULT: let provider handle it

    # 2. Provider-level override from config
    override = spec.provider_overrides.get(provider.value, {})
    if "mode" in override:
        try:
            overridden = CacheMode(override["mode"])
            if overridden in supported:
                return overridden
            if spec.fallback_policy == FallbackPolicy.BEST_EFFORT:
                return default_cache_mode(provider)
        except ValueError:
            pass  # Invalid override value — fall through

    # 3. Global default_mode from config
    if spec.default_mode is not None:
        if spec.default_mode in supported:
            return spec.default_mode
        if spec.fallback_policy == FallbackPolicy.BEST_EFFORT:
            return default_cache_mode(provider)
        if spec.fallback_policy == FallbackPolicy.STRICT:
            raise ValueError(
                f"Provider {provider.value!r} does not support default cache mode "
                f"{spec.default_mode.value!r}. Supported: {[m.value for m in supported]}"
            )
        return None  # PROVIDER_DEFAULT

    # 4. Provider default
    return default_cache_mode(provider)


def load_cache_spec_from_config(cfg_fn: Callable) -> CacheSpec:
    """Build a CacheSpec from the tokenpak config.

    Reads ``cache.*`` keys using *cfg_fn* (the ``_cfg`` accessor from proxy.py).
    All settings are optional — missing ``[cache]`` section returns safe defaults.

    Args:
        cfg_fn: Config accessor with signature cfg_fn(key, default, env_var, cast).

    Returns:
        CacheSpec populated from config file + environment variable overrides.
    """
    enabled: bool = cfg_fn("cache.enabled", True, "TOKENPAK_CACHE_ENABLED", bool)

    fallback_str: str = cfg_fn(
        "cache.fallback_policy", "best-effort", "TOKENPAK_CACHE_FALLBACK", str
    )
    try:
        fallback_policy = FallbackPolicy(fallback_str)
    except ValueError:
        fallback_policy = FallbackPolicy.BEST_EFFORT

    default_mode_str: Optional[str] = cfg_fn(
        "cache.default_mode", None, "TOKENPAK_CACHE_DEFAULT_MODE", str
    )
    default_mode: Optional[CacheMode] = None
    if default_mode_str:
        try:
            default_mode = CacheMode(default_mode_str)
        except ValueError:
            pass  # Unknown mode string — leave as None

    # Per-provider mode overrides: cache.<provider_value>.mode
    provider_overrides: dict[str, dict] = {}
    for p in Provider:
        if p is Provider.UNKNOWN:
            continue
        mode_val: Optional[str] = cfg_fn(f"cache.{p.value}.mode", None, None, str)
        if mode_val:
            provider_overrides[p.value] = {"mode": mode_val}

    return CacheSpec(
        enabled=enabled,
        default_mode=default_mode,
        fallback_policy=fallback_policy,
        provider_overrides=provider_overrides,
    )
