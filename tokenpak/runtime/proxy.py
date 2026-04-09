#!/usr/bin/env python3
"""
TokenPak Forward Proxy v4 — Two-Tier Context Injection

Changes from v3:
- Two-Tier Index: loads BOTH workspace + vault indexes
- Context Injection: BM25 search across vault index, inject relevant blocks
  into system prompt as supplementary context
- Style Contracts preserved from v3
- All v3 features maintained (compilation modes, protected content, etc.)

Env vars:
    TOKENPAK_PORT             (default: 8766)
    TOKENPAK_MODE             (default: hybrid) — strict|hybrid|aggressive
    TOKENPAK_COMPACT          (default: 1) — master on/off switch
    TOKENPAK_COMPACT_MAX_CHARS      (default: 120) — max chars for compressed text
    TOKENPAK_COMPACT_THRESHOLD_TOKENS (default: 4500) — skip compaction below this
    TOKENPAK_COMPACT_CACHE_SIZE     (default: 2000)
    TOKENPAK_DB               (default: .tokenpak/monitor.db)
    TOKENPAK_VAULT_INDEX      (default: ~/vault/.tokenpak) — path to shared vault index
    TOKENPAK_INJECT_BUDGET    (default: 4000) — max tokens to inject from vault
    TOKENPAK_INJECT_TOP_K     (default: 5) — max vault blocks to inject
    TOKENPAK_INJECT_MIN_SCORE (default: 2.0) — minimum BM25 score to include
    TOKENPAK_RETRIEVAL_BACKEND (default: json_blocks) — json_blocks|sqlite — vault retrieval backend
    TOKENPAK_CAPSULE_BUILDER  (default: 0) — enable capsule builder stage (0|1)
    TOKENPAK_CAPSULE_MIN_CHARS (default: 400) — min chars for a block to be capsulised
    TOKENPAK_CAPSULE_HOT_WINDOW (default: 2) — trailing messages excluded from capsule compression
    TOKENPAK_MAX_COMPRESSION_TIME_MS (default: 5000) — max compression time in ms before skipping; 0 = no cap
    TOKENPAK_HTTP100_KEEPALIVE (default: 0) — send HTTP 100 Continue before compression (SSE keepalive)

    # Tier 1 Modules (2026-03-11, all default OFF for safe rollout)
    TOKENPAK_SEMANTIC_CACHE     (default: 0) — enable short-circuit cache for duplicate queries
    TOKENPAK_PREFIX_REGISTRY    (default: 0) — enable stable prefix tracking for cache optimization
    TOKENPAK_COMPRESSION_DICT   (default: 0) — enable post-compaction dictionary compression
    TOKENPAK_TRACE             (default: 0) — enable pipeline tracing (WIP)

    # Tier 2A Modules (2026-03-11, all default OFF)
    TOKENPAK_ERROR_NORMALIZER  (default: 0) — normalize error responses across providers
    TOKENPAK_BUDGET_CONTROLLER (default: 0) — enforce token budget limits per request
    TOKENPAK_REQUEST_LOGGER    (default: 0) — structured request/response logging
    TOKENPAK_SALIENCE_ROUTER   (default: 0) — content-type-aware extraction before compaction

    # Tier 2B Cache (2026-03-11, all default OFF)
    TOKENPAK_CACHE_REGISTRY    (default: 0) — unified stable/volatile cache registry
"""

import asyncio
import gzip
import http.client
import json
import urllib3
import math
import os
import re
import signal
import socket
import ssl
import sys
import threading
import time
import uuid
from collections import deque, OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from tokenpak.proxy.adapters import build_default_registry
from tokenpak.proxy.adapters.base import FormatAdapter
from tokenpak.proxy.streaming import _extract_sse_tokens  # noqa: F401
from tokenpak.proxy.cache_poison import (  # noqa: F401
    _strip_cache_poisons,
    _classify_cache_miss_reason,
    _UUID_PATTERN,
    _TIMESTAMP_PATTERN,
    _HEARTBEAT_COUNTER,
)
from tokenpak.proxy.stats import build_health_response, build_stats_response  # noqa: F401
from tokenpak.proxy.request_pipeline import (  # noqa: F401
    _get_router,
    _get_validation_gate,
    _has_validation_gate,
    _RouterResult,
    _classify_intent,
    _extract_user_text,
    _run_router,
    _router_health,
    _health_cache,
    _HEALTH_CACHE_TTL,
    _get_route_engine,
    _get_cached_route_rules,
    _get_precond_gates,
    _get_budget_controller,
    PROTECTED_MARKERS,
    is_protected_content,
    classify_message_risk,
    can_compress,
)

# Try to import migration system (for DB schema version tracking)
try:
    from db_migrations import migrate as db_migrate, get_current_schema_version
    MIGRATION_AVAILABLE = True
except ImportError:
    MIGRATION_AVAILABLE = False
    def db_migrate(conn): pass
    def get_current_schema_version(conn): return 0

# ---------------------------------------------------------------------------
# Feature imports — CANON dedup
# ---------------------------------------------------------------------------
try:
    import os as _os_canon
    import sys as _sys_canon

    _sys_canon.path.insert(0, _os_canon.path.expanduser("~/.openclaw/workspace/.tokenpak"))
    from canon_session import apply_canon_refs
    from canon_session import get_session as get_canon_session

    CANON_AVAILABLE = True
except ImportError:
    CANON_AVAILABLE = False

    def apply_canon_refs(body, session_id=""):
        return body, 0, 0


# ---------------------------------------------------------------------------
# PromptBuilder — stable/volatile prefix split for cache efficiency
# ---------------------------------------------------------------------------
try:
    from tokenpak.agent.proxy.prompt_builder import (
        apply_stable_cache_control as _apply_stable_cache_control,
    )
    from tokenpak.agent.proxy.prompt_builder import (
        inject_with_cache_boundary as _inject_with_cache_boundary,
    )

    PROMPT_BUILDER_AVAILABLE = True
except ImportError:
    PROMPT_BUILDER_AVAILABLE = False

    def _apply_stable_cache_control(body_bytes):
        return body_bytes

    def _inject_with_cache_boundary(body_bytes, volatile_text):
        return body_bytes


# ---------------------------------------------------------------------------
# Tool Schema Registry — normalizes tools array to byte-identical JSON
# Enables Anthropic prompt cache hits on repeated tool calls
# ---------------------------------------------------------------------------
try:
    from tokenpak.agent.proxy.tool_schema_registry import get_registry as _get_tool_registry

    TOOL_REGISTRY_AVAILABLE = True
except ImportError:
    TOOL_REGISTRY_AVAILABLE = False

    def _get_tool_registry():
        return None


# ---------------------------------------------------------------------------
# Term Resolver — deterministic glossary term extraction
# ---------------------------------------------------------------------------
try:
    from tokenpak.agent.semantic import TermResolver, TermResolverConfig

    TERM_RESOLVER_AVAILABLE = True
except ImportError:
    TERM_RESOLVER_AVAILABLE = False
    TermResolver = None
    TermResolverConfig = None


# ---------------------------------------------------------------------------
# Pipeline Trace — extracted to tokenpak/proxy/tracing.py
# ---------------------------------------------------------------------------
from tokenpak.proxy.tracing import (  # noqa: E402
    _CompressionTimeout,
    StageTrace,
    PipelineTrace,
    TraceStorage,
    TRACE_STORAGE,
)


# ---------------------------------------------------------------------------
# Config + constants — extracted to tokenpak/proxy/config.py
# ---------------------------------------------------------------------------
from tokenpak.proxy.config import (  # noqa: E402
    _cfg,
    ACTIVE_PROFILE,
    PROXY_PORT,
    LISTEN_ADDRESS,
    PROXY_AUTH_KEY,
    DASHBOARD_AUTH_ENABLED,
    MONITOR_DB,
    BUDGET_DAILY_LIMIT_USD,
    BUDGET_ALERT_THRESHOLD_PCT,
    VAULT_SYNC_INTERVAL,
    ENABLE_COMPACTION,
    COMPACT_MAX_CHARS,
    COMPACT_THRESHOLD_TOKENS,
    COMPACT_MAX_TOKENS,
    COMPACT_CACHE_SIZE,
    COMPILATION_MODE,
    ENABLE_CAPSULE_BUILDER,
    CAPSULE_MIN_CHARS,
    CAPSULE_HOT_WINDOW,
    ROUTER_ENABLED,
    SKELETON_ENABLED,
    SHADOW_ENABLED,
    BUDGET_TOTAL_TOKENS,
    CHAT_FOOTER_ENABLED,
    HTTP100_KEEPALIVE_ENABLED,
    SEMANTIC_CACHE_ENABLED,
    _get_sem_cache,
    PREFIX_REGISTRY_ENABLED,
    COMPRESSION_DICT_ENABLED,
    TRACE_ENABLED,
    ERROR_NORMALIZER_ENABLED,
    BUDGET_CONTROLLER_ENABLED,
    REQUEST_LOGGER_ENABLED,
    SALIENCE_ROUTER_ENABLED,
    CACHE_REGISTRY_ENABLED,
    RETRIEVAL_WATCHDOG_ENABLED,
    FAILURE_MEMORY_ENABLED,
    FIDELITY_TIERS_ENABLED,
    SESSION_CAPSULES_ENABLED,
    PRECONDITION_GATES_ENABLED,
    QUERY_REWRITER_ENABLED,
    STABILITY_SCORER_ENABLED,
    WS_PORT,
    WS_MAX_CONNECTIONS,
    _plugin_registry,
    _cache_registry,
    UPSTREAM_TIMEOUT,
    STRICT_VALIDATION,
    _POOL_MANAGER,
    VALIDATION_GATE_ENABLED,
    VALIDATION_GATE_BUDGET_CAP,
    VALIDATION_GATE_SOFT,
    VAULT_INDEX_PATH,
    INJECT_BUDGET,
    INJECT_TOP_K,
    INJECT_MIN_SCORE,
    INJECT_SKIP_MODELS,
    INJECT_MIN_PROMPT,
    MAX_COMPRESSION_TIME_MS,
    VAULT_INDEX_RELOAD_INTERVAL,
    VAULT_CACHE_MAX_BYTES,
    VAULT_CACHE_PRELOAD,
    RETRIEVAL_BACKEND,
    TERM_RESOLVER_ENABLED,
    TERM_RESOLVER_TOP_K,
    TERM_RESOLVER_MAX_BYTES,
    _COMPACT_CACHE,
    _COMPACT_CACHE_ORDER,
    ADAPTER_REGISTRY,
    UPSTREAM_ROUTES,
)


# ---------------------------------------------------------------------------
# Key pool, upstream routing, circuit breakers, error helpers, rate limiting
# Extracted to proxy/fallback.py (TPK-RESTRUCTURE-002)
# ---------------------------------------------------------------------------
from tokenpak.proxy.fallback import (
    _build_key_pool,
    _ANTHROPIC_KEY_POOL,
    _reload_config_from_env,
    _key_is_available,
    _cool_down_key,
    _get_next_key,
    _strip_empty_text_blocks,
    _cap_cache_control_blocks,
    _resolve_upstream,
    _extract_host,
    INTERCEPT_HOSTS,
    OLLAMA_UPSTREAM,
    OLLAMA_CONNECT_TIMEOUT,
    _provider_for_url,
    _circuit_check,
    _circuit_record_failure,
    _circuit_record_success,
    _sanitize_headers,
    _suggest_model,
    _make_structured_error,
    _enrich_upstream_error,
    _rate_limit_check,
    _KEY_ROTATION_MODE,
    _KEY_COOLDOWN_429,
    _KEY_COOLDOWN_401,
)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------
# LRU cache so repeated count_tokens calls on the same text (e.g. injection text
# counted before and after skeleton) are O(1) lookups instead of re-encoding.
_TOKEN_COUNT_CACHE: Dict[int, int] = {}  # hash(text) -> token_count
_TOKEN_COUNT_CACHE_MAX = 1024

# ── Swap Pressure Monitoring ──────────────────────────────────────────────────
SWAP_PRESSURE_THRESHOLD_MB: int = int(os.environ.get("TOKENPAK_SWAP_WARN_MB", "600"))
_SWAP_WARN_LAST_LOGGED: float = 0.0
_SWAP_WARN_COOLDOWN_SEC: int = 300  # max once per 5 min


def get_swap_mb() -> int:
    """Read current swap usage for this process from /proc/self/status."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmSwap:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def check_swap_pressure() -> int:
    """Check swap usage; log warning if above threshold. Returns swap_mb."""
    global _SWAP_WARN_LAST_LOGGED
    import time as _time
    swap_mb = get_swap_mb()
    if swap_mb > SWAP_PRESSURE_THRESHOLD_MB:
        now = _time.time()
        if now - _SWAP_WARN_LAST_LOGGED > _SWAP_WARN_COOLDOWN_SEC:
            logging.warning(
                "[WARN] High swap pressure: %dMB — compression may be slow "
                "(threshold: %dMB)", swap_mb, SWAP_PRESSURE_THRESHOLD_MB
            )
            _SWAP_WARN_LAST_LOGGED = now
    return swap_mb


def _token_count_cached(text: str, encoder) -> int:
    """Count tokens with hash-keyed FIFO cache. Avoids re-encoding repeated text."""
    key = hash(text)
    if key in _TOKEN_COUNT_CACHE:
        _inc_token_cache_hit()
        return _TOKEN_COUNT_CACHE[key]
    _inc_token_cache_miss()
    result = len(encoder.encode(text))
    if len(_TOKEN_COUNT_CACHE) >= _TOKEN_COUNT_CACHE_MAX:
        # Evict oldest key (dict insertion order preserved in Python 3.7+)
        _TOKEN_COUNT_CACHE.pop(next(iter(_TOKEN_COUNT_CACHE)))
    _TOKEN_COUNT_CACHE[key] = result
    return result

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return _token_count_cached(text, _ENC)
except ImportError:

    def count_tokens(text: str) -> int:
        return len(text) // 4


# ---------------------------------------------------------------------------
# Two-Tier Vault Index — delegated to tokenpak.proxy.vault_bridge
# (TPK-RESTRUCTURE-011: extracted from runtime/proxy.py shim)
# ---------------------------------------------------------------------------
from tokenpak.proxy.vault_bridge import (  # noqa: E402
    VaultIndex,
    _bm25_tokenize,
    _build_vault_index as _build_vault_index_backend,
    _init_singletons as initialize_singletons,
    get_vault_index,
    get_term_resolver,
    get_capsule_builder,
    _LazyAlias,
    VAULT_INDEX,
    TERM_RESOLVER,
    CAPSULE_BUILDER,
)

# ---------------------------------------------------------------------------
# (REMOVED) VaultIndex class (lines ~342–832) now lives in proxy/vault_bridge.py
# Below is the original class stub kept here only for documentation purposes:
#   class VaultIndex:


# ---------------------------------------------------------------------------
# Skeleton extraction — strips function bodies from code blocks before injection
# Reduces code-heavy vault blocks by 70-90% (signatures + docstrings only)
# ---------------------------------------------------------------------------
def _skeletonize_block(content: str, file_ext: str) -> str:
    """Apply skeleton extraction to a code block if the language is supported."""
    if not SKELETON_ENABLED:
        return content
    lang_map = {
        ".py": "python",
        ".ts": "typescript",
        ".js": "javascript",
        ".go": "go",
        ".rs": "rust",
    }
    lang = lang_map.get(file_ext.lower(), "")
    if not lang:
        return content
    try:
        sys.path.insert(
            0, str(Path.home() / "vault" / "01_PROJECTS" / "tokenpak" / "packages" / "pypi")
        )
        from tokenpak.skeleton_extractor import extract_skeleton

        return extract_skeleton(content, lang)
    except Exception:
        return content


def _inject_skeleton_into_blocks(blocks_text: str) -> str:
    """Walk a multi-block injection string and skeletonize code blocks."""
    if not SKELETON_ENABLED or not blocks_text:
        return blocks_text

    def _replace_fence(m):
        lang_hint = m.group(1).strip().lower()
        ext_map = {
            "python": ".py",
            "py": ".py",
            "typescript": ".ts",
            "ts": ".ts",
            "javascript": ".js",
            "js": ".js",
            "go": ".go",
            "rust": ".rs",
        }
        ext = ext_map.get(lang_hint, "")
        code = m.group(2)
        skeletonized = _skeletonize_block(code, ext) if ext else code
        return f"```{m.group(1)}\n{skeletonized}\n```"

    return re.sub(r"```([^\n]*)\n(.*?)```", _replace_fence, blocks_text, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# Shadow reader validation — coherence-check compressed output
# ---------------------------------------------------------------------------
def _shadow_validate(original: str, compressed: str) -> bool:
    """Returns True if compressed text passes coherence check, False = use original."""
    if not SHADOW_ENABLED:
        return True
    if not compressed or not original:
        return True
    try:
        sys.path.insert(
            0, str(Path.home() / "vault" / "01_PROJECTS" / "tokenpak" / "packages" / "pypi")
        )
        from tokenpak.shadow_reader import ShadowReader

        reader = ShadowReader()
        result = reader.validate(original=original, compressed=compressed)
        return result.passed
    except Exception:
        return True  # fail-open: if shadow reader errors, allow compressed version


# ---------------------------------------------------------------------------
# Budget controller — enforce per-bucket token limits
# ---------------------------------------------------------------------------
def _apply_budget(components: dict, total_tokens: int = None) -> dict:
    """Apply Budgeter allocation policy to context components."""
    total = total_tokens or BUDGET_TOTAL_TOKENS
    try:
        sys.path.insert(
            0, str(Path.home() / "vault" / "01_PROJECTS" / "tokenpak" / "packages" / "pypi")
        )
        from tokenpak.budgeter import Budgeter

        b = Budgeter()
        return b.allocate(components, total_tokens=total)
    except Exception:
        return components  # fail-open


# ---------------------------------------------------------------------------
# Router wiring, route engine, intent classification, style contract
# Extracted to tokenpak.proxy.request_pipeline (TPK-RESTRUCTURE-005)
# All symbols imported above: _get_router, _run_router, _router_health,
# _RouterResult, _classify_intent, _extract_user_text, _get_route_engine,
# _get_cached_route_rules, _get_precond_gates, _get_budget_controller,
# _get_validation_gate, _has_validation_gate, _health_cache, _HEALTH_CACHE_TTL,
# PROTECTED_MARKERS, is_protected_content, classify_message_risk, can_compress
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SQLite monitor — extracted to proxy/monitor.py (TPK-RESTRUCTURE-006)
# ---------------------------------------------------------------------------
import sqlite3  # noqa: F401 — kept for inline sqlite3.connect() calls elsewhere
import tokenpak.proxy.monitor as _monitor_mod
from tokenpak.proxy.monitor import Monitor, _get_db_connection  # noqa: F401
from tokenpak.proxy.server import ForwardProxyHandler  # noqa: F401

MONITOR = Monitor(MONITOR_DB)

# ---------------------------------------------------------------------------
# Request latency tracking (rolling window, p50/p99 for /health)
# ---------------------------------------------------------------------------
_request_latencies: deque = deque(maxlen=100)
_latency_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Session stats
# ---------------------------------------------------------------------------
SESSION = {
    "requests": 0,
    "input_tokens": 0,
    "sent_input_tokens": 0,
    "saved_tokens": 0,
    "protected_tokens": 0,
    "output_tokens": 0,
    "cost": 0.0,
    "cost_saved": 0.0,
    "start_time": time.time(),
    "errors": 0,
    "compilation_mode": COMPILATION_MODE,
    "active_profile": ACTIVE_PROFILE,
    "injected_tokens": 0,
    "injection_hits": 0,
    "injection_skips": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_miss_reasons": {
        "timestamp_poison": 0,
        "uuid_request_id_poison": 0,
        "schema_tool_change": 0,
        "retrieval_order_drift_or_unknown": 0,
    },
    "token_cache_hits": 0,
    "token_cache_misses": 0,
    "canon_hits": 0,
    "canon_tokens_saved": 0,
    "ingest_entries": 0,
    "compression_timeouts": 0,
    "vault_last_timing_ms": {},
}

# ---------------------------------------------------------------------------
# Graceful Shutdown — SIGTERM/SIGINT drain support
# ---------------------------------------------------------------------------
_shutdown_event = threading.Event()
_active_request_count = 0
_active_request_lock = threading.Lock()
_active_requests_drained = threading.Event()

# ---------------------------------------------------------------------------
# Last Request Stats — captures most recent request for /stats/last
# ---------------------------------------------------------------------------
LAST_REQUEST = {
    "request_id": None,
    "timestamp": None,
    "model": None,
    "input_tokens_raw": 0,
    "input_tokens_sent": 0,
    "tokens_saved": 0,
    "percent_saved": 0.0,
    "cost_saved": 0.0,
    "output_tokens": 0,
}
_LAST_REQUEST_LOCK = threading.Lock()


def update_last_request(
    request_id: str,
    model: str,
    input_raw: int,
    input_sent: int,
    tokens_saved: int,
    cost_saved: float,
    output_tokens: int,
):
    """Thread-safe update of last request stats."""
    with _LAST_REQUEST_LOCK:
        LAST_REQUEST["request_id"] = request_id
        LAST_REQUEST["timestamp"] = datetime.now().isoformat()
        LAST_REQUEST["model"] = model
        LAST_REQUEST["input_tokens_raw"] = input_raw
        LAST_REQUEST["input_tokens_sent"] = input_sent
        LAST_REQUEST["tokens_saved"] = tokens_saved
        LAST_REQUEST["percent_saved"] = (
            round(tokens_saved / input_raw * 100, 1) if input_raw > 0 else 0.0
        )
        LAST_REQUEST["cost_saved"] = round(cost_saved, 6)
        LAST_REQUEST["output_tokens"] = output_tokens


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------
MODEL_COSTS = {
    "claude-opus-4-5": {"input": 15.0, "output": 75.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-3-5": {"input": 0.8, "output": 4.0},
    "claude-haiku-4-5": {"input": 0.8, "output": 4.0},
    "gpt-4o": {"input": 5.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-5.2-codex": {"input": 2.0, "output": 8.0},
    "gpt-5.3-codex": {"input": 2.0, "output": 8.0},
    "gpt-5.3-codex-spark": {"input": 0.5, "output": 2.0},
    "gpt-5.1-codex-mini": {"input": 0.5, "output": 2.0},
    "gemini-2-flash": {"input": 0.1, "output": 0.4},
    "gemini-3-pro-preview": {"input": 1.25, "output": 5.0},
    "gemini-3-flash-preview": {"input": 0.1, "output": 0.4},
}


def estimate_cost(model, input_tokens, output_tokens, cache_read=0, cache_creation=0):
    for key, costs in MODEL_COSTS.items():
        if key in model.lower():
            regular_input = max(0, input_tokens - cache_read - cache_creation)
            return (
                regular_input * costs["input"]
                + cache_read * costs["input"] * 0.1
                + cache_creation * costs["input"] * 1.25
                + output_tokens * costs["output"]
            ) / 1_000_000
    regular_input = max(0, input_tokens - cache_read - cache_creation)
    return (
        regular_input * 3.0
        + cache_read * 3.0 * 0.1
        + cache_creation * 3.0 * 1.25
        + output_tokens * 15.0
    ) / 1_000_000


def _header_mapping(headers: Any) -> Dict[str, str]:
    """
    Build a plain dict from BaseHTTPRequestHandler headers.
    """
    result: Dict[str, str] = {}
    try:
        for key in headers:
            result[str(key)] = str(headers[key])
    except Exception:
        pass
    return result


def _detect_adapter(
    path: str, headers: Mapping[str, str], body_bytes: Optional[bytes] = None
) -> FormatAdapter:
    return ADAPTER_REGISTRY.detect(path=path, headers=headers, body=body_bytes)


def extract_request_tokens(
    body_bytes: bytes, adapter: Optional[FormatAdapter] = None
) -> Tuple[str, int]:
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_request_tokens(body_bytes, token_counter=count_tokens)
    except Exception:
        return "unknown", 0


def extract_response_tokens(
    body_bytes: bytes, adapter: Optional[FormatAdapter] = None, is_sse: bool = False
) -> int:
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_response_tokens(body_bytes, is_sse=is_sse)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Context Injection: Extract query signal from request
# ---------------------------------------------------------------------------
def extract_query_signal(body_bytes: bytes, adapter: Optional[FormatAdapter] = None) -> str:
    """
    Extract a search query from the request to find relevant vault context.
    Uses the last user message + any recent assistant context as signal.
    """
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_query_signal(body_bytes)
    except Exception:
        return ""


def inject_vault_context(
    body_bytes: bytes, adapter: Optional[FormatAdapter] = None
) -> Tuple[bytes, int, List[str]]:
    """
    Search vault index for relevant context and inject into the system prompt.
    Optionally resolves glossary terms and injects term cards.
    Returns (new_body_bytes, injected_tokens, source_refs).
    """
    if not VAULT_INDEX.available:
        return body_bytes, 0, []

    active_adapter = adapter or _detect_adapter("", {}, body_bytes)

    # --- Sub-step timing (surfaced in vault_stage.details via SESSION) ---
    _t = time.perf_counter()

    query = extract_query_signal(body_bytes, adapter=active_adapter)
    _t_query_ms = (time.perf_counter() - _t) * 1000

    if not query:
        return body_bytes, 0, []

    # Resolve glossary terms (optional, feature-flagged)
    glossary_injection = ""
    glossary_tokens = 0
    _t2 = time.perf_counter()
    if TERM_RESOLVER is not None and TERM_RESOLVER_ENABLED:
        try:
            resolution = TERM_RESOLVER.resolve_terms(query)
            if resolution.injection_text and resolution.canonical_ids:
                glossary_injection = resolution.injection_text
                glossary_tokens = resolution.tokens_estimate
                # Adjust vault budget to account for glossary tokens
                remaining_budget = max(1000, INJECT_BUDGET - glossary_tokens)
            else:
                remaining_budget = INJECT_BUDGET
        except Exception:
            remaining_budget = INJECT_BUDGET
    else:
        remaining_budget = INJECT_BUDGET
    _t_resolver_ms = (time.perf_counter() - _t2) * 1000

    _t3 = time.perf_counter()
    injection_text, tokens_used, source_refs = VAULT_INDEX.compile_injection(
        query, budget=remaining_budget, top_k=INJECT_TOP_K, min_score=INJECT_MIN_SCORE
    )
    _t_bm25_ms = (time.perf_counter() - _t3) * 1000

    # Combine glossary + vault injection if both present
    combined_injection = ""
    combined_tokens = 0
    if glossary_injection and injection_text:
        combined_injection = glossary_injection + "\n\n" + injection_text
        combined_tokens = glossary_tokens + tokens_used
    elif glossary_injection:
        combined_injection = glossary_injection
        combined_tokens = glossary_tokens
    elif injection_text:
        combined_injection = injection_text
        combined_tokens = tokens_used

    if not combined_injection:
        return body_bytes, 0, []

    # Apply skeleton extraction to code blocks in injection text (70-90% reduction on code)
    _t4 = time.perf_counter()
    if SKELETON_ENABLED:
        combined_injection = _inject_skeleton_into_blocks(combined_injection)
        combined_tokens = count_tokens(combined_injection)
    _t_skeleton_ms = (time.perf_counter() - _t4) * 1000

    _t5 = time.perf_counter()
    try:
        new_body = active_adapter.inject_system_context(body_bytes, combined_injection)
    except Exception:
        return body_bytes, 0, []
    _t_inject_ms = (time.perf_counter() - _t5) * 1000

    _total_ms = (time.perf_counter() - _t) * 1000
    # Store sub-step breakdown in SESSION for /stats and trace enrichment
    SESSION["vault_last_timing_ms"] = {
        "query_signal": round(_t_query_ms, 1),
        "term_resolver": round(_t_resolver_ms, 1),
        "bm25_search": round(_t_bm25_ms, 1),
        "skeleton": round(_t_skeleton_ms, 1),
        "inject_body": round(_t_inject_ms, 1),
        "total": round(_total_ms, 1),
    }

    return new_body, combined_tokens, source_refs


# ---------------------------------------------------------------------------
# Compaction with style contracts
# ---------------------------------------------------------------------------
def compact_text(text: str) -> str:
    if not text:
        return text
    key = str(hash(text))
    if key in _COMPACT_CACHE:
        return _COMPACT_CACHE[key]
    t = " ".join(text.split())
    m = re.search(r"[.!?](?:\s|$)", t)
    if m:
        t = t[: m.end()].strip()
    if len(t) > COMPACT_MAX_CHARS:
        t = t[:COMPACT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    # Shadow reader guard: if compressed text fails coherence check, return original
    if SHADOW_ENABLED and COMPILATION_MODE == "aggressive" and not _shadow_validate(text, t):
        t = text  # fall back to original — coherence check failed
    _COMPACT_CACHE[key] = t
    _COMPACT_CACHE_ORDER.append(key)
    if len(_COMPACT_CACHE_ORDER) > COMPACT_CACHE_SIZE:
        old = _COMPACT_CACHE_ORDER.pop(0)
        _COMPACT_CACHE.pop(old, None)
    return t



def _build_cache_stats_payload() -> Dict[str, Any]:
    global _TOKEN_CACHE_HITS, _TOKEN_CACHE_MISSES
    
    # Sync module counters to SESSION
    SESSION["token_cache_hits"] = _TOKEN_CACHE_HITS
    SESSION["token_cache_misses"] = _TOKEN_CACHE_MISSES
    
    hits = int(SESSION.get("cache_hits", 0) or 0)
    misses = int(SESSION.get("cache_misses", 0) or 0)
    total = hits + misses
    hit_rate = (hits / total) if total > 0 else 0.0
    miss_reasons = dict(SESSION.get("cache_miss_reasons", {}))
    return {
        "hit_rate": round(hit_rate, 4),
        "cache_read_tokens": int(SESSION.get("cache_read_tokens", 0) or 0),
        "cache_creation_tokens": int(SESSION.get("cache_creation_tokens", 0) or 0),
        "cache_hits": hits,
        "cache_misses": misses,
        "total_cache_decisions": total,
        "miss_reasons": miss_reasons,
        "token_cache_hits": SESSION["token_cache_hits"],
        "token_cache_misses": SESSION["token_cache_misses"],
    }


def compact_request_body(body_bytes: bytes, adapter: Optional[FormatAdapter] = None):
    """
    Style-contract-aware compaction.
    Returns (new_body_bytes, sent_tokens, original_tokens, protected_token_count).
    """
    active_adapter = adapter or _detect_adapter("", {}, body_bytes)
    if active_adapter.source_format == "passthrough":
        model, tokens = extract_request_tokens(body_bytes, adapter=active_adapter)
        _ = model
        return body_bytes, tokens, tokens, 0

    try:
        canonical = active_adapter.normalize(body_bytes)
    except Exception:
        return body_bytes, 0, 0, 0

    _, original_tokens = extract_request_tokens(body_bytes, adapter=active_adapter)
    if original_tokens < COMPACT_THRESHOLD_TOKENS:
        return body_bytes, original_tokens, original_tokens, 0
    if COMPACT_MAX_TOKENS > 0 and original_tokens > COMPACT_MAX_TOKENS:
        # Skip compression for large payloads — latency cost exceeds token savings
        return body_bytes, original_tokens, original_tokens, 0

    mode = COMPILATION_MODE
    if mode == "strict":
        return body_bytes, original_tokens, original_tokens, original_tokens

    protected_tokens = 0

    if isinstance(canonical.system, str):
        protected_tokens += count_tokens(canonical.system)
    elif isinstance(canonical.system, list):
        for part in canonical.system:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                protected_tokens += count_tokens(part["text"])

    messages = canonical.messages
    keep_from = max(0, len(messages) - 2)
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            last_user_idx = i

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if i >= keep_from:
            risk = classify_message_risk(msg)
            if risk == "protected":
                content = msg.get("content", "")
                if isinstance(content, str):
                    protected_tokens += count_tokens(content)
                elif isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict) and "text" in p:
                            protected_tokens += count_tokens(p["text"])
            continue
        if msg.get("role") == "user" and i == last_user_idx:
            continue

        risk = classify_message_risk(msg)
        if not can_compress(risk, mode):
            content = msg.get("content", "")
            if isinstance(content, str):
                protected_tokens += count_tokens(content)
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and "text" in p:
                        protected_tokens += count_tokens(p["text"])
            continue

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = compact_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = compact_text(part["text"])

    try:
        new_body = active_adapter.denormalize(canonical)
    except Exception:
        return body_bytes, original_tokens, original_tokens, protected_tokens
    _, sent_tokens = extract_request_tokens(new_body, adapter=active_adapter)
    return new_body, sent_tokens, original_tokens, protected_tokens



# ---------------------------------------------------------------------------
# Ingest storage
# ---------------------------------------------------------------------------
INGEST_ENTRIES_DIR = Path.home() / "vault" / ".tokenpak" / "entries"


def _ingest_write_entry(entry: Dict[str, Any]) -> str:
    """Append a single entry to the JSONL file, return its id."""
    entry_id = entry.setdefault("id", str(uuid.uuid4()))
    date_str = None

    # Use timestamp date if provided, else today
    ts = entry.get("timestamp")
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Create entries directory
    INGEST_ENTRIES_DIR.mkdir(parents=True, exist_ok=True)

    # Append to JSONL file
    entries_file = INGEST_ENTRIES_DIR / f"{date_str}.jsonl"
    with open(entries_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())

    return entry_id


# ---------------------------------------------------------------------------
# Vault sync
# ---------------------------------------------------------------------------
def sync_to_vault():
    vault_path = Path.home() / "vault" / "System" / "tokenpak-stats.json"
    if vault_path.parent.exists():
        stats = MONITOR.get_stats()
        stats["by_model"] = MONITOR.get_by_model()
        stats["last_sync"] = datetime.now().isoformat()
        stats["compilation_mode"] = COMPILATION_MODE
        stats["active_profile"] = ACTIVE_PROFILE
        stats["swap_mb"] = check_swap_pressure()
        stats["session"] = {
            "requests": SESSION["requests"],
            "protected_tokens": SESSION["protected_tokens"],
            "injected_tokens": SESSION["injected_tokens"],
            "injection_hits": SESSION["injection_hits"],
            "uptime_hours": round((time.time() - SESSION["start_time"]) / 3600, 2),
        }
        vault_path.write_text(json.dumps(stats, indent=2))


def sync_loop():
    while True:
        time.sleep(VAULT_SYNC_INTERVAL)
        try:
            sync_to_vault()
        except Exception as e:
            print(f"  ⚠️ Vault sync failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
class ThreadedHTTPServer(HTTPServer):
    allow_reuse_address = True  # SO_REUSEADDR — prevents "Address already in use" on restart
    allow_reuse_port = True     # SO_REUSEPORT — allows immediate rebind after crash

    def server_bind(self):
        """Override to ensure SO_REUSEADDR is set before bind."""
        import socket
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass  # SO_REUSEPORT not available on all platforms
        super().server_bind()

    def process_request(self, request, client_address):
        global _active_request_count
        with _active_request_lock:
            _active_request_count += 1
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        global _active_request_count
        try:
            self.finish_request(request, client_address)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError) as e:
            # Client disconnected — normal during OpenClaw fallback/abort. Don't log traceback.
            pass
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            with _active_request_lock:
                _active_request_count -= 1
                if _active_request_count == 0 and _shutdown_event.is_set():
                    _active_requests_drained.set()


def validate_tokenpak_config():
    """Validate and auto-correct TokenPak config at startup."""
    expected = {
        "TOKENPAK_SEMANTIC_CACHE": "1",
        "TOKENPAK_PREFIX_REGISTRY": "1",
        "TOKENPAK_COMPRESSION_DICT": "1",
        "TOKENPAK_TRACE": "1",
        "TOKENPAK_BUDGET_CONTROLLER": "1",
        "TOKENPAK_REQUEST_LOGGER": "1",
        "TOKENPAK_ERROR_NORMALIZER": "1",
        "TOKENPAK_SALIENCE_ROUTER": "1",
        "TOKENPAK_CACHE_REGISTRY": "1",
        "TOKENPAK_RETRIEVAL_WATCHDOG": "1",
        "TOKENPAK_FAILURE_MEMORY": "1",
        "TOKENPAK_FIDELITY_TIERS": "1",
        "TOKENPAK_PRECONDITION_GATES": "1",
        "TOKENPAK_QUERY_REWRITER": "1",
        "TOKENPAK_SESSION_CAPSULES": "1",
        "TOKENPAK_STABILITY_SCORER": "1",
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_PORT": "8766",
    }

    drift_found = False
    for key, expected_val in expected.items():
        actual_val = os.getenv(key, "")
        if actual_val != expected_val:
            print(f"⚠️  CONFIG DRIFT: {key}={actual_val}, expected {expected_val}")
            os.environ[key] = expected_val
            drift_found = True

    if drift_found:
        print("🔧 TokenPak config auto-corrected")
    else:
        print("✅ TokenPak config validated - all settings correct")


# ---------------------------------------------------------------------------
# WebSocket proxy — extracted to tokenpak/proxy/websocket.py (TPK-RESTRUCTURE-008)
# ---------------------------------------------------------------------------
from tokenpak.proxy.websocket import (  # noqa: F401
    _ws_handler,
    _ws_active_connections,
    _ws_active_connections_lock,
    start_ws_server as _start_ws_server_impl,
)


def _start_ws_server() -> threading.Thread:
    """Start the asyncio WebSocket server in a daemon thread on WS_PORT.

    Delegates to tokenpak.proxy.websocket.start_ws_server().
    """
    return _start_ws_server_impl(compact_request_body)



@dataclass
class StartupPhase:
    """Timing record for a single proxy startup phase."""
    name: str
    duration_ms: float
    detail: str = ""


@dataclass
class StartupResult:
    """Aggregated result from _run_startup()."""
    phases: List[StartupPhase]
    total_ms: float
    vault_block_count: int
    vault_available: bool


def _run_startup() -> StartupResult:
    """Run all proxy initialization phases and return structured timing result.

    This function is extracted from main() so tests can call it directly
    without spawning a subprocess or hitting serve_forever().
    """
    phases: List[StartupPhase] = []
    _t0 = time.perf_counter()

    # Phase 1: Config validation
    _phase_start = time.perf_counter()
    validate_tokenpak_config()
    phases.append(StartupPhase(
        name="Config loaded",
        duration_ms=(time.perf_counter() - _phase_start) * 1000,
    ))

    # Phase 2: Vault index load
    _phase_start = time.perf_counter()
    VAULT_INDEX.maybe_reload()  # triggers _init_singletons() which starts reload timer
    vault_block_count = len(VAULT_INDEX.blocks) if VAULT_INDEX.available else 0
    phases.append(StartupPhase(
        name="Vault index loaded",
        duration_ms=(time.perf_counter() - _phase_start) * 1000,
        detail=f"{vault_block_count} blocks" if VAULT_INDEX.available else "not found",
    ))

    # Phase 3: Proxy workflow check
    _phase_start = time.perf_counter()
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tokenpak"))
        from tokenpak.agent.agentic.proxy_workflow import recover_proxy_workflows
        dangling = recover_proxy_workflows()
        _wf_detail = f"{len(dangling)} dangling" if dangling else "clean"
    except Exception as _e:
        dangling = []
        _wf_detail = f"skipped ({_e})"
    phases.append(StartupPhase(
        name="Workflow check",
        duration_ms=(time.perf_counter() - _phase_start) * 1000,
        detail=_wf_detail,
    ))

    total_ms = (time.perf_counter() - _t0) * 1000
    return StartupResult(
        phases=phases,
        total_ms=total_ms,
        vault_block_count=vault_block_count,
        vault_available=VAULT_INDEX.available,
    )


def main():
    port = PROXY_PORT
    mode_desc = {
        "strict": "100% lossless — no compression",
        "hybrid": "Protected/Code strict, Narrative compressed",
        "aggressive": "Everything except protected gets compressed",
    }

    # Run structured startup phases (config, vault, workflow check)
    _startup = _run_startup()
    vault_block_count = _startup.vault_block_count
    vault_status = f"{vault_block_count} blocks" if _startup.vault_available else "not found"

    # Print startup timing tree
    print("🚀 TokenPak proxy starting...", flush=True)
    for i, phase in enumerate(_startup.phases):
        connector = "└─" if i == len(_startup.phases) - 1 else "├─"
        detail = f" ({phase.detail})" if phase.detail else ""
        print(f"   {connector} {phase.name + ':' :<28} {phase.duration_ms:.0f}ms{detail}", flush=True)
    print(f"✅ Ready on {LISTEN_ADDRESS}:{port} (total: {_startup.total_ms:.0f}ms)", flush=True)

    # Handle dangling proxy workflows (already recovered in _run_startup, just print warnings)
    try:
        from tokenpak.agent.agentic.proxy_workflow import recover_proxy_workflows
        dangling = recover_proxy_workflows()
        if dangling:
            print(f"[proxy_workflow] ⚠️  {len(dangling)} incomplete proxy workflow(s) from prior run:")
            for wf in dangling[:5]:
                running_step = next(
                    (s["name"] for s in wf["steps"] if s["status"] == "running"), "—"
                )
                print(f"  • {wf['id'][:8]}… step={running_step}")
    except Exception as _e:
        print(f"[proxy_workflow] startup check skipped: {_e}")

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║             TokenPak Forward Proxy v4                            ║
║             Two-Tier Context Injection                           ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  Listening:    http://{LISTEN_ADDRESS}:{port:<5}                              ║
║  Profile:      {ACTIVE_PROFILE:<10}                                              ║
║  Mode:         {COMPILATION_MODE:<10} ({mode_desc.get(COMPILATION_MODE, '?')})
║  Compaction:   {'ON' if ENABLE_COMPACTION else 'OFF':<10}                                       ║
║  Threshold:    {COMPACT_THRESHOLD_TOKENS} tokens                               ║
║  DB:           {str(MONITOR_DB):<50}║
║                                                                  ║
║  Two-Tier Index:                                                 ║
║    📚 Vault:     {vault_status:<44}║
║    💉 Budget:    {INJECT_BUDGET} tokens/request                        ║
║    🎯 Min score: {INJECT_MIN_SCORE}                                          ║
║    📂 Path:      {str(VAULT_INDEX_PATH):<44}║
║                                                                  ║
║  Style Contracts:                                                ║
║    🔒 PROTECTED — system prompts, SOUL.md, tool schemas          ║
║    📝 NARRATIVE — docs, markdown (compressible in hybrid+)       ║
║    💻 CODE      — source code (strict in hybrid, compressible    ║
║                   in aggressive)                                 ║
║    ⚙️  CONFIG    — JSON/YAML/config (strict in hybrid)            ║
║                                                                  ║
║  Endpoints:  /health  /stats  /recent  /vault                    ║
║                                                                  ║
║  🔒 Security:                                                    ║
║    Bind:       {LISTEN_ADDRESS:<50}║
║    Auth:       {'ENABLED (X-TokenPak-Key required)' if PROXY_AUTH_KEY else 'disabled (no key set)':<50}║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    sync_thread = threading.Thread(target=sync_loop, daemon=True)
    sync_thread.start()

    # Schedule anonymous metrics daily batch sync (non-blocking, opt-in only)
    try:
        from tokenpak.telemetry.reporter import schedule_daily_sync

        schedule_daily_sync()
    except Exception:
        pass

    # Start WebSocket proxy server on WS_PORT (default 8767)
    _start_ws_server()

    server = ThreadedHTTPServer((LISTEN_ADDRESS, port), ForwardProxyHandler)

    # Pre-load compression pipeline to eliminate first-request penalty
    def _warmup():
        try:
            from tokenpak.agent.compression.pipeline import CompressionPipeline
            from tokenpak.agent.compression.recipes import RecipeEngine
            from tokenpak.agent.compression.slot_filler import SlotFiller
            print("  ✅ Compression pipeline pre-loaded", flush=True)
        except ImportError:
            print("  ℹ️  Compression pipeline not available — skipping warmup", flush=True)

    _warmup()

    # Write PID file for CLI stop/restart
    _pid_path = Path.home() / ".tokenpak" / "proxy.pid"
    _pid_path.parent.mkdir(parents=True, exist_ok=True)
    _pid_path.write_text(str(os.getpid()))

    def _handle_signal(signum, frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\n[shutdown] {sig_name} received — stopping gracefully…")
        _shutdown_event.set()
        _pid_path.unlink(missing_ok=True)
        # shutdown() must be called from a different thread than serve_forever()
        threading.Thread(target=server.shutdown, daemon=True).start()

    def _handle_sighup(signum, frame):
        """Hot-reload config on SIGHUP — no proxy restart needed."""
        _reload_config_from_env()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGHUP, _handle_sighup)

    # --- systemd watchdog ---
    def _systemd_watchdog():
        """Ping systemd watchdog every 10s to signal health."""
        notify_sock = os.environ.get("NOTIFY_SOCKET")
        if not notify_sock:
            return  # Not running under systemd with watchdog
        while not _shutdown_event.is_set():
            try:
                import socket as _sock
                addr = notify_sock.lstrip("@")
                if notify_sock.startswith("@"):
                    addr = "\0" + addr
                    s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_DGRAM)
                    s.sendto(b"WATCHDOG=1", addr)
                else:
                    s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_DGRAM)
                    s.sendto(b"WATCHDOG=1", addr)
                s.close()
            except Exception:
                pass
            _shutdown_event.wait(timeout=10)

    _watchdog_thread = threading.Thread(target=_systemd_watchdog, daemon=True, name="sd-watchdog")
    _watchdog_thread.start()

    # Signal systemd ready
    try:
        _notify_sock = os.environ.get("NOTIFY_SOCKET")
        if _notify_sock:
            import socket as _sock
            addr = _notify_sock.lstrip("@")
            if _notify_sock.startswith("@"):
                addr = "\0" + addr
            _s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_DGRAM)
            _s.sendto(b"READY=1", addr)
            _s.close()
    except Exception:
        pass

    server.serve_forever()

    # --- Drain in-flight requests (up to 10s) ---
    drain_timeout = 10
    with _active_request_lock:
        count = _active_request_count
    if count > 0:
        print(f"[shutdown] Draining {count} in-flight request(s) (up to {drain_timeout}s)…")
        _active_requests_drained.wait(timeout=drain_timeout)
        with _active_request_lock:
            remaining = _active_request_count
        if remaining:
            print(
                f"[shutdown] ⚠️  {remaining} request(s) still active after {drain_timeout}s — forcing exit"
            )
        else:
            print("[shutdown] ✅ All in-flight requests completed")
    else:
        print("[shutdown] ✅ No in-flight requests — clean exit")

    print("\n📊 Session Summary:")
    print(f"   Profile:         {ACTIVE_PROFILE}")
    print(f"   Mode:            {COMPILATION_MODE}")
    print(f"   Requests:        {SESSION['requests']}")
    print(f"   Input:           {SESSION['input_tokens']:,} tokens")
    print(f"   Sent:            {SESSION['sent_input_tokens']:,} tokens")
    print(f"   Protected:       {SESSION['protected_tokens']:,} tokens (never compressed)")
    print(f"   Saved:           {SESSION['saved_tokens']:,} tokens")
    print(
        f"   Injected:        {SESSION['injected_tokens']:,} tokens ({SESSION['injection_hits']} hits)"
    )
    print(f"   Output:          {SESSION['output_tokens']:,} tokens")
    print(f"   Est. cost:       ${SESSION['cost']:.4f}")
    print(f"   Errors:          {SESSION['errors']}")
    sync_to_vault()
    
    # Drain background DB write queue before exit
    if _monitor_mod._DB_WRITE_QUEUE is not None:
        print("[shutdown] Draining DB write queue…")
        try:
            # Wait for queue to drain (up to 5 seconds)
            _monitor_mod._DB_WRITE_QUEUE.join()
            print("[shutdown] ✅ DB write queue drained")
        except Exception as e:
            print(f"[shutdown] ⚠️  DB queue drain error: {e}")

        # Stop background worker
        _monitor_mod._DB_BACKGROUND_STOP.set()
        if _monitor_mod._DB_BACKGROUND_THREAD and _monitor_mod._DB_BACKGROUND_THREAD.is_alive():
            _monitor_mod._DB_BACKGROUND_THREAD.join(timeout=2)
            if _monitor_mod._DB_BACKGROUND_THREAD.is_alive():
                print("[shutdown] ⚠️  DB writer thread did not exit cleanly")
            else:
                print("[shutdown] ✅ DB writer thread stopped")
    
    print(
        "[shutdown] SQLite connections closed (per-request open/close pattern — no persistent handles)"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Backoff & Cache Counters (P0 TPK-RESTORE-BACKOFF-CACHE)
# ---------------------------------------------------------------------------

import random as _random_module

_BACKOFF_BASE = float(os.environ.get("TOKENPAK_BACKOFF_BASE", "1.0"))
_BACKOFF_CAP = float(os.environ.get("TOKENPAK_BACKOFF_CAP", "32.0"))
_MAX_RETRIES = int(os.environ.get("TOKENPAK_MAX_RETRIES", "3"))

def _backoff_wait(attempt: int, base: float = _BACKOFF_BASE, cap: float = _BACKOFF_CAP) -> None:
    """Exponential backoff: base * 2^attempt with 25% jitter, capped at cap seconds."""
    wait = min(base * (2 ** attempt), cap)
    wait *= (1.0 + _random_module.uniform(0, 0.25))
    logger.info("Rate limited — backoff %.1fs (attempt %d)", wait, attempt)
    time.sleep(wait)


# Token cache counters (module-level)
_TOKEN_CACHE_HITS: int = 0
_TOKEN_CACHE_MISSES: int = 0

def _inc_token_cache_hit() -> None:
    global _TOKEN_CACHE_HITS
    _TOKEN_CACHE_HITS += 1

def _inc_token_cache_miss() -> None:
    global _TOKEN_CACHE_MISSES
    _TOKEN_CACHE_MISSES += 1

