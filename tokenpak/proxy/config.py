"""
TokenPak proxy configuration — env vars, feature flags, constants, profiles, upstream routes.

Extracted from tokenpak/runtime/proxy.py (L1-607 extraction, phase 1a).
"""

import json
import os
from pathlib import Path
from typing import Dict

import urllib3

from tokenpak.proxy.adapters import build_default_registry

# ---------------------------------------------------------------------------
# Config — reads ~/.tokenpak/config.yaml with env var overrides
# ---------------------------------------------------------------------------
try:
    import logging as _logging

    from tokenpak.core.config_loader import get as _cfg
    _logging.getLogger("tokenpak.proxy.config").info("Config: ~/.tokenpak/config.yaml (env vars override)")
except ImportError:
    # Fallback: env-only mode (no config_loader available)
    def _cfg(key, default=None, env_var=None, cast=None):
        if env_var:
            val = os.environ.get(env_var)
            if val is not None:
                if cast is bool:
                    return val.lower() in ("1", "true", "yes", "on")
                return cast(val) if cast else val
        return default

    import logging as _logging
    _logging.getLogger("tokenpak.proxy.config").info("Config: env vars only (config_loader not available)")

# ---------------------------------------------------------------------------
# Named Workflow Profiles — TOKENPAK_PROFILE sets sensible flag bundles
# Profile is a floor: explicit env vars always win (setdefault semantics)
# ---------------------------------------------------------------------------
_PROFILE_PRESETS: dict[str, dict[str, str]] = {
    "safe": {
        # CCG-10: safe profile uses TOKENPAK_MODE=safe (Phase 2 Mode B).
        # Stable cache control fires unconditionally; no body compression.
        "TOKENPAK_MODE": "safe",
        "TOKENPAK_STABLE_CACHE_CONTROL_AUTO": "true",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "8000",
        "TOKENPAK_SKELETON_ENABLED": "false",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "balanced": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "1500",  # Flipped 2026-04-13 — see TRIX-01 / pmgtm initiative
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "aggressive": {
        "TOKENPAK_MODE": "aggressive",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "2000",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "true",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "agentic": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "3000",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
}

ACTIVE_PROFILE: str = os.environ.get("TOKENPAK_PROFILE", "balanced").lower()
if ACTIVE_PROFILE in _PROFILE_PRESETS:
    for _pk, _pv in _PROFILE_PRESETS[ACTIVE_PROFILE].items():
        os.environ.setdefault(_pk, _pv)
    _logging.getLogger("tokenpak.proxy.config").info("Profile: %s", ACTIVE_PROFILE)
else:
    _logging.getLogger("tokenpak.proxy.config").warning("Unknown TOKENPAK_PROFILE=%r — ignoring", ACTIVE_PROFILE)
    ACTIVE_PROFILE = "custom"

PROXY_PORT = _cfg("port", 8766, "TOKENPAK_PORT", int)
LISTEN_ADDRESS = _cfg("listen_address", "127.0.0.1", "TOKENPAK_BIND_ADDRESS", str)
PROXY_AUTH_KEY = os.environ.get("TOKENPAK_PROXY_KEY", "")
DASHBOARD_AUTH_ENABLED = _cfg("dashboard.require_token", False, "TOKENPAK_DASHBOARD_AUTH", bool)
MONITOR_DB = _cfg("db", str(Path(__file__).parent / "monitor.db"), "TOKENPAK_DB", str)
BUDGET_DAILY_LIMIT_USD = float(os.environ.get("TOKENPAK_BUDGET_DAILY_LIMIT_USD", "0"))
BUDGET_ALERT_THRESHOLD_PCT = float(os.environ.get("TOKENPAK_BUDGET_ALERT_PCT", "80"))
# CCG-02: mutation_audit TTL — prune rows older than this many days
MUTATION_AUDIT_TTL_DAYS: int = int(os.environ.get("TOKENPAK_MUTATION_AUDIT_TTL_DAYS", "30"))
VAULT_SYNC_INTERVAL = 60
ENABLE_COMPACTION = _cfg("compression.enabled", True, "TOKENPAK_COMPACT", bool)
COMPACT_MAX_CHARS = _cfg("compression.max_chars", 120, "TOKENPAK_COMPACT_MAX_CHARS", int)
COMPACT_THRESHOLD_TOKENS = _cfg(
    "compression.threshold_tokens", 1500, "TOKENPAK_COMPACT_THRESHOLD_TOKENS", int  # Flipped 2026-04-13 — see TRIX-01 / pmgtm initiative
)
# Skip compression for very large payloads — compression savings are marginal (<3%) but
# synchronous processing adds 10-25s of silence before first SSE chunk, causing client timeouts.
# Default: skip compression above 50,000 tokens (~200KB). Set to 0 to disable this cap.
COMPACT_MAX_TOKENS = _cfg(
    "compression.max_tokens", 50000, "TOKENPAK_COMPACT_MAX_TOKENS", int
)
COMPACT_CACHE_SIZE = _cfg("compression.cache_size", 2000, "TOKENPAK_COMPACT_CACHE_SIZE", int)
COMPILATION_MODE = _cfg("mode", "hybrid", "TOKENPAK_MODE", str).lower()
# CCG-10: Auto-apply stable cache control in safe mode (TOKENPAK_MODE=safe)
STABLE_CACHE_CONTROL_AUTO: bool = _cfg(
    "features.stable_cache_control_auto", False, "TOKENPAK_STABLE_CACHE_CONTROL_AUTO", bool
)

# Capsule Builder
ENABLE_CAPSULE_BUILDER = _cfg("features.capsule_builder", False, "TOKENPAK_CAPSULE_BUILDER", bool)
CAPSULE_MIN_CHARS = _cfg("capsule.min_chars", 400, "TOKENPAK_CAPSULE_MIN_CHARS", int)
CAPSULE_HOT_WINDOW = _cfg("capsule.hot_window", 2, "TOKENPAK_CAPSULE_HOT_WINDOW", int)

# Core features
ROUTER_ENABLED: bool = _cfg("features.router", True, "TOKENPAK_ROUTER_ENABLED", bool)
SKELETON_ENABLED: bool = _cfg("features.skeleton", True, "TOKENPAK_SKELETON_ENABLED", bool)
SHADOW_ENABLED: bool = _cfg("features.shadow_reader", True, "TOKENPAK_SHADOW_ENABLED", bool)
BUDGET_TOTAL_TOKENS: int = _cfg("budget.total_tokens", 12000, "TOKENPAK_BUDGET_TOTAL", int)
CHAT_FOOTER_ENABLED: bool = _cfg("features.chat_footer", False, "TOKENPAK_CHAT_FOOTER", bool)
HTTP100_KEEPALIVE_ENABLED: bool = _cfg("features.http100_keepalive", False, "TOKENPAK_HTTP100_KEEPALIVE", bool)

# Tier 1 modules
SEMANTIC_CACHE_ENABLED: bool = _cfg(
    "features.semantic_cache", False, "TOKENPAK_SEMANTIC_CACHE", bool
)

# Semantic cache singleton — instantiated once at module level to prevent per-request
# memory leak (fix for 2026-03-14 swap exhaustion incident: ~3.9GB after 14h uptime)
_SEM_CACHE_SINGLETON = None


def _get_sem_cache():
    global _SEM_CACHE_SINGLETON
    if _SEM_CACHE_SINGLETON is None and SEMANTIC_CACHE_ENABLED:
        try:
            from tokenpak.cache.semantic_cache import SemanticCache

            _SEM_CACHE_SINGLETON = SemanticCache()
        except Exception:
            pass
    return _SEM_CACHE_SINGLETON


PREFIX_REGISTRY_ENABLED: bool = _cfg(
    "features.prefix_registry", False, "TOKENPAK_PREFIX_REGISTRY", bool
)
COMPRESSION_DICT_ENABLED: bool = _cfg(
    "features.compression_dict", False, "TOKENPAK_COMPRESSION_DICT", bool
)
TRACE_ENABLED: bool = _cfg("features.trace", True, "TOKENPAK_TRACE", bool)

# Tier 2 modules
ERROR_NORMALIZER_ENABLED: bool = _cfg(
    "features.error_normalizer", False, "TOKENPAK_ERROR_NORMALIZER", bool
)
BUDGET_CONTROLLER_ENABLED: bool = _cfg(
    "features.budget_controller", True, "TOKENPAK_BUDGET_CONTROLLER", bool  # Flipped 2026-04-13 — see TRIX-01 / pmgtm initiative
)
REQUEST_LOGGER_ENABLED: bool = _cfg(
    "features.request_logger", False, "TOKENPAK_REQUEST_LOGGER", bool
)
SALIENCE_ROUTER_ENABLED: bool = _cfg(
    "features.salience_router", False, "TOKENPAK_SALIENCE_ROUTER", bool
)
CACHE_REGISTRY_ENABLED: bool = _cfg(
    "features.cache_registry", False, "TOKENPAK_CACHE_REGISTRY", bool
)
RETRIEVAL_WATCHDOG_ENABLED: bool = _cfg(
    "features.retrieval_watchdog", False, "TOKENPAK_RETRIEVAL_WATCHDOG", bool
)
FAILURE_MEMORY_ENABLED: bool = _cfg(
    "features.failure_memory", False, "TOKENPAK_FAILURE_MEMORY", bool
)
FIDELITY_TIERS_ENABLED: bool = _cfg(
    "features.fidelity_tiers", False, "TOKENPAK_FIDELITY_TIERS", bool
)

# Phase 3 modules
SESSION_CAPSULES_ENABLED: bool = _cfg(
    "features.session_capsules", False, "TOKENPAK_SESSION_CAPSULES", bool
)
PRECONDITION_GATES_ENABLED: bool = _cfg(
    "features.precondition_gates", False, "TOKENPAK_PRECONDITION_GATES", bool
)
QUERY_REWRITER_ENABLED: bool = _cfg(
    "features.query_rewriter", False, "TOKENPAK_QUERY_REWRITER", bool
)
STABILITY_SCORER_ENABLED: bool = _cfg(
    "features.stability_scorer", False, "TOKENPAK_STABILITY_SCORER", bool
)

# WebSocket proxy
WS_PORT: int = int(os.environ.get("TOKENPAK_WS_PORT", "8767"))
WS_MAX_CONNECTIONS: int = int(os.environ.get("TOKENPAK_WS_MAX_CONNECTIONS", "50"))

# ---------------------------------------------------------------------------
# Plugin system — run custom compressors first
# ---------------------------------------------------------------------------
_plugin_registry = None
try:
    from tokenpak.plugins.registry import PluginRegistry as _PluginRegistry

    _plugin_registry = _PluginRegistry()
    _plugin_registry.discover()
    _loaded = _plugin_registry.get_plugins()
    if _loaded:
        _logging.getLogger("tokenpak.proxy.config").info("Plugin system: %d plugin(s) loaded", len(_loaded))
    else:
        _logging.getLogger("tokenpak.proxy.config").info("Plugin system: no plugins configured")
except Exception as _plugin_init_err:
    _logging.getLogger("tokenpak.proxy.config").warning("Plugin system init failed (disabled): %s", _plugin_init_err)
    _plugin_registry = None


# --- Tier 2B Cache Registry singleton (initialized at module load if enabled) ---
_cache_registry = None
if CACHE_REGISTRY_ENABLED:
    try:
        from tokenpak.cache.registry import CacheRegistry

        _cache_registry = CacheRegistry()
        _logging.getLogger("tokenpak.proxy.config").info("Cache registry initialized")
    except Exception as _cr_init_err:
        _logging.getLogger("tokenpak.proxy.config").warning("Cache registry init failed: %s", _cr_init_err)
        CACHE_REGISTRY_ENABLED = False

# Upstream
UPSTREAM_TIMEOUT: int = _cfg("upstream.timeout", 90, "TOKENPAK_UPSTREAM_TIMEOUT", int)
# Query expansion — enabled by default; opt out with TOKENPAK_QUERY_EXPANSION_ENABLED=0
QUERY_EXPANSION_ENABLED: bool = _cfg(
    "features.query_expansion", True, "TOKENPAK_QUERY_EXPANSION_ENABLED", bool
)

# Legacy pool manager — only used by the monolith proxy.py.
# The modular proxy uses httpx via connection_pool.py.
# Lazy-init to avoid creating TCP connections on module import.
_POOL_MANAGER = None

def get_pool_manager():
    """Get or create the urllib3 pool manager (lazy init)."""
    global _POOL_MANAGER
    if _POOL_MANAGER is None:
        _POOL_MANAGER = urllib3.PoolManager(
            num_pools=10,
            maxsize=10,
            retries=False,
            timeout=urllib3.Timeout(connect=10.0, read=UPSTREAM_TIMEOUT),
            cert_reqs="CERT_REQUIRED",
        )
    return _POOL_MANAGER

# Validation gate
VALIDATION_GATE_ENABLED: bool = _cfg(
    "features.validation_gate", True, "TOKENPAK_VALIDATION_GATE", bool
)
VALIDATION_GATE_BUDGET_CAP: int = _cfg(
    "budget.validation_gate_cap", 120000, "TOKENPAK_VALIDATION_GATE_BUDGET_CAP", int
)
VALIDATION_GATE_SOFT: bool = _cfg(
    "features.validation_gate_soft", True, "TOKENPAK_VALIDATION_GATE_SOFT", bool
)

# Vault / Retrieval
VAULT_INDEX_PATH = _cfg(
    "vault.index_path", str(Path.home() / "vault" / ".tokenpak"), "TOKENPAK_VAULT_INDEX", str
)
INJECT_BUDGET = _cfg("vault.inject_budget", 4000, "TOKENPAK_INJECT_BUDGET", int)
INJECT_TOP_K = _cfg("vault.inject_top_k", 5, "TOKENPAK_INJECT_TOP_K", int)
INJECT_MIN_SCORE = _cfg("vault.inject_min_score", 2.0, "TOKENPAK_INJECT_MIN_SCORE", float)
INJECT_SKIP_MODELS = _cfg("vault.inject_skip_models", "haiku", "TOKENPAK_INJECT_SKIP_MODELS", str)
INJECT_MIN_PROMPT = _cfg("vault.inject_min_prompt", 1000, "TOKENPAK_INJECT_MIN_PROMPT", int)
# Max time budget for the entire compression pipeline (capsule + vault + compaction).
# If exceeded, compression is skipped and original body is forwarded uncompressed.
# Default: 5000ms. Set to 0 to disable the cap.
MAX_COMPRESSION_TIME_MS = _cfg("compression.max_time_ms", 5000, "MAX_COMPRESSION_TIME_MS", int)
VAULT_INDEX_RELOAD_INTERVAL = 300
# Auto-reindex: rebuild vault index from source files when stale.
# Runs in background thread. Default: 3600s (1 hour). Set to 0 to disable.
VAULT_AUTO_REINDEX_INTERVAL: int = _cfg(
    "vault.auto_reindex_interval", 3600, "TOKENPAK_VAULT_AUTO_REINDEX", int
)
# Tiered vault memory — LRU content cache config
VAULT_CACHE_MAX_BYTES: int = _cfg(
    "vault.cache_max_bytes", 256 * 1024 * 1024, "TOKENPAK_VAULT_MEMORY_MAX", int
)  # default 256MB
VAULT_CACHE_PRELOAD: int = _cfg(
    "vault.cache_preload", 200, "TOKENPAK_VAULT_CACHE_PRELOAD", int
)  # top-N recently-modified blocks to preload
RETRIEVAL_BACKEND = _cfg(
    "vault.retrieval_backend", "json_blocks", "TOKENPAK_RETRIEVAL_BACKEND", str
).lower()

# Term-Card Resolver — enabled by default; opt out with TOKENPAK_TERM_RESOLVER_ENABLED=0
TERM_RESOLVER_ENABLED: bool = _cfg(
    "features.term_resolver", True, "TOKENPAK_TERM_RESOLVER_ENABLED", bool
)
TERM_RESOLVER_TOP_K: int = _cfg("term_resolver.top_k", 3, "TOKENPAK_TERM_RESOLVER_TOP_K", int)
TERM_RESOLVER_MAX_BYTES: int = _cfg(
    "term_resolver.max_bytes", 200, "TOKENPAK_TERM_RESOLVER_MAX_BYTES", int
)

_COMPACT_CACHE: dict = {}
_COMPACT_CACHE_ORDER: list = []

ADAPTER_REGISTRY = build_default_registry()


def _load_tokenpak_upstream_overrides() -> Dict[str, str]:
    """
    Auto-discover upstream routes from tokenpak-* provider mirrors.
    Checks config.json first (pre-migration), then falls back to
    the loaded config.yaml (post-migration).
    Supports current shape at `models.providers` and legacy root `providers`.
    """
    cfg_path = Path.home() / ".tokenpak" / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
        except Exception:
            return {}
    else:
        # Post-migration: provider mirrors live in config.yaml now
        try:
            from tokenpak.core.config_loader import load_config
            cfg = load_config() or {}
        except ImportError:
            return {}
        if not cfg:
            return {}

    providers = None
    models = cfg.get("models")
    if isinstance(models, dict):
        model_providers = models.get("providers")
        if isinstance(model_providers, dict):
            providers = model_providers

    if providers is None:
        legacy_providers = cfg.get("providers")
        if isinstance(legacy_providers, dict):
            providers = legacy_providers

    if not isinstance(providers, dict):
        return {}

    aliases = {
        "anthropic": "anthropic-messages",
        "openai": "openai-chat",
        "openai-codex": "openai-responses",
        "google": "google-generative-ai",
        # P0 providers
        "openrouter": "openai-chat",
        "litellm": "openai-chat",
        "vercel-ai-gateway": "openai-chat",
        "kilocode": "openai-responses",
        "bedrock": "anthropic-messages",
    }

    overrides: Dict[str, str] = {}
    for name, entry in providers.items():
        if not isinstance(name, str) or not name.startswith("tokenpak-"):
            continue
        if not isinstance(entry, dict):
            continue
        source_provider = entry.get("source_provider") or name[len("tokenpak-") :]
        if not isinstance(source_provider, str):
            continue
        source_entry = providers.get(source_provider)
        if not isinstance(source_entry, dict):
            continue
        base_url = source_entry.get("base_url") or source_entry.get("baseUrl")
        if not isinstance(base_url, str) or not base_url:
            continue

        mapped = aliases.get(source_provider)
        if mapped:
            overrides[mapped] = base_url
            # OpenAI-compatible upstreams are usually shared for Chat + Responses.
            if mapped == "openai-chat":
                overrides.setdefault("openai-responses", base_url)

    return overrides


def _load_env_upstream_overrides() -> Dict[str, str]:
    """
    Read adapter upstream overrides from env:
      TOKENPAK_UPSTREAM_<SOURCE_FORMAT_IN_UPPERCASE_WITH_UNDERSCORES>
    """
    mapping: Dict[str, str] = {}
    for source_format in ADAPTER_REGISTRY.list_formats():
        key = "TOKENPAK_UPSTREAM_" + source_format.upper().replace("-", "_")
        value = os.environ.get(key, "").strip()
        if value:
            mapping[source_format] = value
    return mapping


def _build_upstream_routes() -> Dict[str, str]:
    routes = {
        adapter.source_format: adapter.get_default_upstream()
        for adapter in ADAPTER_REGISTRY.adapters()
    }
    routes.update(_load_tokenpak_upstream_overrides())
    routes.update(_load_env_upstream_overrides())
    return routes


UPSTREAM_ROUTES = _build_upstream_routes()


# ---------------------------------------------------------------------------
# Custom providers — user-registered endpoints from config.yaml `providers:`
# ---------------------------------------------------------------------------
from tokenpak.proxy.custom_providers import (
    build_custom_adapters,
    get_provider_display_list,
    load_custom_providers,
)

CUSTOM_PROVIDERS = load_custom_providers()

if CUSTOM_PROVIDERS:
    # Register adapter instances (modifies ADAPTER_REGISTRY in-place)
    _custom_adapters = build_custom_adapters(CUSTOM_PROVIDERS, ADAPTER_REGISTRY)

    # Add custom provider hostnames to the router intercept list
    from tokenpak.proxy.router import INTERCEPT_HOSTS as _INTERCEPT_HOSTS

    for _cp in CUSTOM_PROVIDERS:
        _INTERCEPT_HOSTS.add(_cp.hostname)

    # Add upstream routes for custom adapters
    for _cp in CUSTOM_PROVIDERS:
        _route_key = f"custom-{_cp.name}"
        UPSTREAM_ROUTES[_route_key] = _cp.endpoint

    _custom_names = ", ".join(cp.name for cp in CUSTOM_PROVIDERS)
    _logging.getLogger("tokenpak.proxy.config").info("Custom providers: %s", _custom_names)

# Build the display string for startup banners
PROVIDER_DISPLAY = get_provider_display_list(ADAPTER_REGISTRY, CUSTOM_PROVIDERS)


# ---------------------------------------------------------------------------
# ProxyConfig — convenience wrapper around module-level settings (FIN-07)
# ---------------------------------------------------------------------------

class ProxyConfig:
    """
    Read-only configuration object for the TokenPak proxy.

    Wraps the module-level constants so callers can access them as attributes
    on a single config instance::

        cfg = ProxyConfig()
        print(cfg.port, cfg.compilation_mode)
    """

    def __init__(self):
        self.port: int = PROXY_PORT
        self.listen_address: str = LISTEN_ADDRESS
        self.compilation_mode: str = COMPILATION_MODE
        self.enable_compaction: bool = ENABLE_COMPACTION
        self.upstream_routes: Dict[str, str] = UPSTREAM_ROUTES
        self.upstream_timeout: int = UPSTREAM_TIMEOUT
        self.vault_index_path: str = VAULT_INDEX_PATH
        self.active_profile: str = ACTIVE_PROFILE
        self.trace_enabled: bool = TRACE_ENABLED
        self.router_enabled: bool = ROUTER_ENABLED
        self.enable_capsule_builder: bool = ENABLE_CAPSULE_BUILDER
        self.adapter_registry = ADAPTER_REGISTRY
        self.custom_providers = CUSTOM_PROVIDERS
        self.provider_display: str = PROVIDER_DISPLAY

    def __repr__(self) -> str:
        return (
            f"ProxyConfig(port={self.port}, mode={self.compilation_mode!r}, "
            f"profile={self.active_profile!r})"
        )
