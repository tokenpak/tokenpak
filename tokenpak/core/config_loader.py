"""
TokenPak Config Loader

Single source of truth: ~/.tokenpak/config.yaml
Env vars override config file values.

Auto-migration: if ~/.tokenpak/config.json exists but config.yaml does not,
the JSON is converted to YAML and the original renamed to config.json.migrated.
"""

import json as _json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml as _yaml

    _HAS_YAML = True

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return _yaml.safe_load(f) or {}

except ImportError:
    _HAS_YAML = False

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return _json.load(f)


CONFIG_PATH = Path(os.environ.get("TOKENPAK_CONFIG", str(Path.home() / ".tokenpak" / "config.yaml")))


def _maybe_migrate_json_to_yaml() -> None:
    """Auto-migrate ~/.tokenpak/config.json -> config.yaml (one-shot).

    Runs only when config.yaml does NOT exist and config.json DOES.
    After writing config.yaml the original is renamed to config.json.migrated
    so it is preserved but no longer picked up by any loader.
    """
    yaml_path = CONFIG_PATH
    json_path = CONFIG_PATH.parent / "config.json"
    migrated_path = CONFIG_PATH.parent / "config.json.migrated"

    if yaml_path.exists() or not json_path.exists():
        return  # nothing to do

    try:
        data = _json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"tokenpak: failed to read config.json for migration: {exc}", file=sys.stderr)
        return

    try:
        if _HAS_YAML:
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            with open(yaml_path, "w", encoding="utf-8") as f:
                _yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        else:
            # Fallback: write JSON with a .yaml extension (still valid for our loader)
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            with open(yaml_path, "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"tokenpak: failed to write config.yaml during migration: {exc}", file=sys.stderr)
        return

    try:
        json_path.rename(migrated_path)
    except Exception as exc:
        print(f"tokenpak: migrated config.yaml written but failed to rename config.json: {exc}",
              file=sys.stderr)
        return

    print("tokenpak: migrated config.json \u2192 config.yaml")

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
    """Load config from YAML file. Returns empty dict if file missing.

    On first call (no custom *path*), runs automatic JSON-to-YAML migration
    so users never need to run ``tokenpak config migrate`` manually.
    """
    global _config
    if _config is not None and path is None:
        return _config

    # Auto-migrate legacy config.json -> config.yaml (only for default path)
    if path is None:
        _maybe_migrate_json_to_yaml()

    config_path = Path(path) if path else CONFIG_PATH
    if config_path.exists():
        try:
            _config = _load_yaml(str(config_path))
        except Exception:
            _config = {}
    else:
        _config = {}

    return _config


def get(key: str, default=None, env_var: str = None, cast=None):
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
        "compression.threshold_tokens", 1500, "TOKENPAK_COMPACT_THRESHOLD_TOKENS", int
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
        ("budget_controller", "BUDGET_CONTROLLER", True),
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
        "upstream.ollama", "http://localhost:11434", "TOKENPAK_OLLAMA_UPSTREAM", str
    )
    result["upstream.ollama_timeout"] = get(
        "upstream.ollama_timeout", 20, "TOKENPAK_OLLAMA_TIMEOUT", int
    )

    # Rate limit
    result["rate_limit_rpm"] = get("rate_limit_rpm", 60, "TOKENPAK_RATE_LIMIT_RPM", int)

    # Logging (merged from legacy config.json)
    result["logging.enabled"] = get("logging.enabled", True, "TOKENPAK_LOG_ENABLED", bool)
    result["logging.level"] = get("logging.level", "info", "TOKENPAK_LOG_LEVEL", str)
    result["logging.destination"] = get("logging.destination", "file", "TOKENPAK_LOG_DESTINATION", str)
    result["logging.retention_days"] = get("logging.retention_days", 30, "TOKENPAK_LOG_RETENTION_DAYS", int)
    result["logging.include_request_body"] = get("logging.include_request_body", False, "TOKENPAK_LOG_REQUEST_BODY", bool)
    result["logging.include_response_body"] = get("logging.include_response_body", False, "TOKENPAK_LOG_RESPONSE_BODY", bool)

    # Validation (merged from legacy config.json)
    result["validation.mode"] = get("validation.mode", "warn", "TOKENPAK_REQUEST_VALIDATION", str)
    result["validation.strict"] = get("validation.strict", False, "TOKENPAK_VALIDATION_STRICT", bool)

    # Plugins (merged from legacy config.json)
    result["plugins.enabled"] = get("plugins.enabled", [], None, list)
    result["plugins.registry_path"] = get("plugins.registry_path", None, None, str)

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
  threshold_tokens: 1500  # was 4500 pre-TRIX-01; lowered for default savings
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
  # Tier 2 modules
  error_normalizer: false
  budget_controller: true  # was false pre-TRIX-01; enabled for default budget enforcement
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
  ollama: http://localhost:11434
  ollama_timeout: 20

rate_limit_rpm: 60

logging:
  enabled: true
  level: info  # debug|info|warn
  destination: file  # file|stdout|syslog
  retention_days: 30
  include_request_body: false
  include_response_body: false

validation:
  mode: warn  # silent|warn|strict
  strict: false

plugins:
  enabled: []  # List of CompressorPlugin class paths to load
  registry_path: null  # Optional custom plugin registry path

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

# TIP Spend Guard (proxy-side pre-send circuit breaker, available v1.5.1+)
# Standard 29 governs the wire contract; docs/spend-guard.md is the user guide.
# Every key has a TOKENPAK_SPEND_GUARD_* env-var counterpart.
spend_guard:
  enabled: true                       # global on/off
  warn_tokens: 100000                 # advisory band — no UX surface yet
  warn_cost_usd: 2.0
  block_tokens: 500000                # holds request, prompts user
  block_cost_usd: 10.0
  hard_block_tokens: 1000000          # immutable ceiling
  hard_block_cost_usd: 50.0
  session_block_cost_usd: 10.0        # death-by-1000-cuts defense
  session_window_seconds: 3600        # 1h sliding window
  pending_ttl_seconds: 600            # held requests expire after 10 min
  audit_db_path: ~/.tokenpak/spend_guard.db

# MultiPak Pro (Std 32) — local-first cross-platform AI context continuity.
# Phase 1 OSS surface: Vault Pak adapter, companion Pak-aware journal,
# tokenpak pak CLI, /pak/v1/* proxy stubs. Pro daemon (closed source)
# is gated by Std 25 §9.3 and ships separately.
# multipak.enabled defaults to false until 1-week soak per Std 32 §13.1
# Decision #6. The OSS surface (read-only Vault Pak inspection,
# /pak/v1/status diagnostic) works regardless of this flag.
pro:
  multipak:
    enabled: false                    # opt-in until soak (Std 32 §13.1 D6)

# Custom providers — register any OpenAI/Anthropic/Google-compatible endpoint.
# Each entry becomes a routable provider with full compression/caching pipeline.
# providers:
#   my-local-llm:
#     endpoint: http://localhost:8000/v1
#     format: openai          # openai | anthropic | google
#     api_key_env: MY_LLM_API_KEY   # env var holding the API key
#   deepseek:
#     endpoint: https://api.deepseek.com/v1
#     format: openai
#     api_key_env: DEEPSEEK_API_KEY
"""
