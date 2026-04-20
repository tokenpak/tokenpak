"""
TokenPak Config Loader

Single source of truth: ~/.tokenpak/config.yaml
Env vars override config file values.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml as _yaml

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return _yaml.safe_load(f) or {}

except ImportError:
    import json

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return json.load(f)


CONFIG_PATH = Path.home() / ".tokenpak" / "config.yaml"

# Cached config
_config: Optional[Dict[str, Any]] = None


def _deep_get(d: dict, keys: str, default=None):
    """Get nested dict value by dot-path. e.g. 'compression.threshold_tokens'"""
    parts = keys.split(".")
    for part in parts:
        if not isinstance(d, dict):
            return default
        d = d.get(part, default)
        if d is default and part != parts[-1]:
            return default
    return d


def _bool_env(val: str) -> bool:
    """Parse env var as boolean."""
    return val.lower() in ("1", "true", "yes", "on")


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load config from YAML file. Returns empty dict if file missing."""
    global _config
    if _config is not None and path is None:
        return _config

    config_path = Path(path) if path else CONFIG_PATH
    if config_path.exists():
        try:
            _config = _load_yaml(str(config_path))
        except Exception:
            _config = {}
    else:
        _config = {}
    return _config


def get(key: str, default=None, env_var: Optional[str] = None, cast=None):
    """
    Get config value. Priority: env var > config file > default.

    Args:
        key: Dot-path into config YAML (e.g. 'compression.threshold_tokens')
        default: Default value if not found
        env_var: Override env var name (auto-derived if None)
        cast: Type cast function (int, float, bool, str)
    """
    cfg = load_config()

    # Check env var first
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val is not None:
            if cast is bool:
                return _bool_env(env_val)
            return cast(env_val) if cast else env_val

    # Check config file
    file_val = _deep_get(cfg, key)
    if file_val is not None:
        if cast is bool and isinstance(file_val, str):
            return _bool_env(file_val)
        return cast(file_val) if cast and not isinstance(file_val, cast) else file_val

    return default


def get_all() -> Dict[str, Any]:
    """Get full merged config (file + env overrides) as flat dict."""
    cfg = load_config()

    # Build result with env var overrides
    result = {}

    # Core
    result["port"] = get("port", 8766, "TOKENPAK_PORT", int)
    result["mode"] = get("mode", "hybrid", "TOKENPAK_MODE", str)
    result["db"] = get("db", None, "TOKENPAK_DB", str)

    # Compression
    result["compression.enabled"] = get("compression.enabled", True, "TOKENPAK_COMPACT", bool)
    result["compression.max_chars"] = get(
        "compression.max_chars", 120, "TOKENPAK_COMPACT_MAX_CHARS", int
    )
    result["compression.threshold_tokens"] = get(
        "compression.threshold_tokens", 4500, "TOKENPAK_COMPACT_THRESHOLD_TOKENS", int
    )
    result["compression.cache_size"] = get(
        "compression.cache_size", 2000, "TOKENPAK_COMPACT_CACHE_SIZE", int
    )

    # Features
    for feat, env_suffix, default in [
        ("skeleton", "SKELETON_ENABLED", True),
        ("shadow_reader", "SHADOW_ENABLED", True),
        ("router", "ROUTER_ENABLED", True),
        ("validation_gate", "VALIDATION_GATE", True),
        ("validation_gate_soft", "VALIDATION_GATE_SOFT", True),
        ("capsule_builder", "CAPSULE_BUILDER", False),
        ("term_resolver", "TERM_RESOLVER_ENABLED", False),
        ("chat_footer", "CHAT_FOOTER", False),
        ("semantic_cache", "SEMANTIC_CACHE", False),
        ("prefix_registry", "PREFIX_REGISTRY", False),
        ("compression_dict", "COMPRESSION_DICT", False),
        ("trace", "TRACE", False),
        ("strict_mode", "STRICT_MODE", False),
        ("error_normalizer", "ERROR_NORMALIZER", False),
        ("budget_controller", "BUDGET_CONTROLLER", False),
        ("request_logger", "REQUEST_LOGGER", False),
        ("salience_router", "SALIENCE_ROUTER", False),
        ("cache_registry", "CACHE_REGISTRY", False),
        ("retrieval_watchdog", "RETRIEVAL_WATCHDOG", False),
        ("failure_memory", "FAILURE_MEMORY", False),
        ("fidelity_tiers", "FIDELITY_TIERS", False),
        ("session_capsules", "SESSION_CAPSULES", False),
        ("precondition_gates", "PRECONDITION_GATES", False),
        ("query_rewriter", "QUERY_REWRITER", False),
        ("stability_scorer", "STABILITY_SCORER", False),
    ]:
        result[f"features.{feat}"] = get(
            f"features.{feat}", default, f"TOKENPAK_{env_suffix}", bool
        )

    # Budget
    result["budget.total_tokens"] = get("budget.total_tokens", 12000, "TOKENPAK_BUDGET_TOTAL", int)
    result["budget.validation_gate_cap"] = get(
        "budget.validation_gate_cap", 120000, "TOKENPAK_VALIDATION_GATE_BUDGET_CAP", int
    )

    # Capsule
    result["capsule.min_chars"] = get("capsule.min_chars", 400, "TOKENPAK_CAPSULE_MIN_CHARS", int)
    result["capsule.hot_window"] = get("capsule.hot_window", 2, "TOKENPAK_CAPSULE_HOT_WINDOW", int)

    # Vault / Injection
    result["vault.index_path"] = get(
        "vault.index_path", str(Path.home() / "vault" / ".tokenpak"), "TOKENPAK_VAULT_INDEX", str
    )
    result["vault.inject_budget"] = get("vault.inject_budget", 4000, "TOKENPAK_INJECT_BUDGET", int)
    result["vault.inject_top_k"] = get("vault.inject_top_k", 5, "TOKENPAK_INJECT_TOP_K", int)
    result["vault.inject_min_score"] = get(
        "vault.inject_min_score", 2.0, "TOKENPAK_INJECT_MIN_SCORE", float
    )
    result["vault.inject_skip_models"] = get(
        "vault.inject_skip_models", "haiku", "TOKENPAK_INJECT_SKIP_MODELS", str
    )
    result["vault.inject_min_prompt"] = get(
        "vault.inject_min_prompt", 1000, "TOKENPAK_INJECT_MIN_PROMPT", int
    )
    result["vault.retrieval_backend"] = get(
        "vault.retrieval_backend", "json_blocks", "TOKENPAK_RETRIEVAL_BACKEND", str
    )

    # Term resolver
    result["term_resolver.top_k"] = get(
        "term_resolver.top_k", 3, "TOKENPAK_TERM_RESOLVER_TOP_K", int
    )
    result["term_resolver.max_bytes"] = get(
        "term_resolver.max_bytes", 200, "TOKENPAK_TERM_RESOLVER_MAX_BYTES", int
    )

    # Upstream
    result["upstream.timeout"] = get("upstream.timeout", 300, "TOKENPAK_UPSTREAM_TIMEOUT", int)
    result["upstream.ollama"] = get(
        "upstream.ollama", "http://100.80.241.118:11434", "TOKENPAK_OLLAMA_UPSTREAM", str
    )
    result["upstream.ollama_timeout"] = get(
        "upstream.ollama_timeout", 20, "TOKENPAK_OLLAMA_TIMEOUT", int
    )

    # Rate limit
    result["rate_limit_rpm"] = get("rate_limit_rpm", 60, "TOKENPAK_RATE_LIMIT_RPM", int)

    return result


def generate_default_yaml() -> str:
    """Generate a default config.yaml with all settings documented."""
    return """# TokenPak Configuration
# =====================
# Edit this file to configure proxy behavior.
# Env vars override these values (TOKENPAK_<SETTING>).
# Restart proxy after changes: tokenpak restart

port: 8766
mode: hybrid  # strict|hybrid|aggressive

compression:
  enabled: true
  max_chars: 120
  threshold_tokens: 4500
  cache_size: 2000

features:
  skeleton: true
  shadow_reader: true
  router: true
  validation_gate: true
  validation_gate_soft: true
  capsule_builder: false
  term_resolver: false
  chat_footer: false
  semantic_cache: false
  prefix_registry: false
  compression_dict: false
  trace: false
  strict_mode: false
  # Tier 2 modules (disabled by default)
  error_normalizer: false
  budget_controller: false
  request_logger: false
  salience_router: false
  cache_registry: false
  retrieval_watchdog: false
  failure_memory: false
  fidelity_tiers: false
  session_capsules: false
  precondition_gates: false
  query_rewriter: false
  stability_scorer: false

budget:
  total_tokens: 12000
  validation_gate_cap: 120000

capsule:
  min_chars: 400
  hot_window: 2

vault:
  index_path: ~/vault/.tokenpak
  inject_budget: 4000
  inject_top_k: 5
  inject_min_score: 2.0
  inject_skip_models: haiku
  inject_min_prompt: 1000
  retrieval_backend: json_blocks  # json_blocks|sqlite

term_resolver:
  top_k: 3
  max_bytes: 200

upstream:
  timeout: 300
  ollama: http://100.80.241.118:11434
  ollama_timeout: 20

rate_limit_rpm: 60

failover:
  enabled: false
  chain:
    - provider: anthropic
      credential_env: ANTHROPIC_API_KEY
    - provider: openai
      model_map:
        claude-opus-4-5: gpt-4o
        claude-sonnet-4-5: gpt-4o-mini
      credential_env: OPENAI_API_KEY
    - provider: google
      model_map:
        claude-opus-4-5: gemini-1.5-pro
        claude-sonnet-4-5: gemini-1.5-pro
      credential_env: GOOGLE_API_KEY
"""
