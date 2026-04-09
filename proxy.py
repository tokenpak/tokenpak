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

# Ensure the correct tokenpak package is importable even when proxy.py lives
# inside a directory containing a local 'tokenpak/' folder that shadows the
# vault-installed package. The vault editable install path takes priority.
import sys as _sys
_VAULT_TOKENPAK = os.path.expanduser("~/vault/01_PROJECTS/tokenpak/tokenpak")
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(_VAULT_TOKENPAK):
    # Remove script directory from sys.path if it contains a shadowing tokenpak/
    if _SCRIPT_DIR in _sys.path and os.path.isdir(os.path.join(_SCRIPT_DIR, "tokenpak")):
        _sys.path.remove(_SCRIPT_DIR)
    if _VAULT_TOKENPAK not in _sys.path:
        _sys.path.insert(0, _VAULT_TOKENPAK)
import signal
import socket
import subprocess
import ssl
import sys
import threading
import time
import uuid
from collections import deque, OrderedDict
from enum import Enum
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
from tokenpak.runtime.providers import Provider, detect_provider

# CACHE-P4-001: CacheSpec normalized config schema
try:
    from tokenpak.runtime.cache_spec import (
        CacheMode as _CacheSpecMode,
        CacheSpec as _CacheSpec,
        PROVIDER_CACHE_MODES as _PROVIDER_CACHE_MODES,
        resolve_cache_mode as _resolve_cache_mode,
        load_cache_spec_from_config as _load_cache_spec_from_config,
    )
    _CACHE_SPEC_AVAILABLE = True
except ImportError as _cse:
    _CACHE_SPEC_AVAILABLE = False
    _CacheSpecMode = None
    _CacheSpec = None
    _PROVIDER_CACHE_MODES = None
    _resolve_cache_mode = None
    _load_cache_spec_from_config = None
    print(f"  ⚠️ CacheSpec not available (cache_spec.py missing): {_cse}")

# CACHE-P4-002: CacheTelemetry — per-provider hit/miss/mode tracking
try:
    from tokenpak.runtime.cache_telemetry import CacheTelemetry as _CacheTelemetry
    _CACHE_TELEMETRY_AVAILABLE = True
except ImportError as _cte:
    _CACHE_TELEMETRY_AVAILABLE = False
    _CacheTelemetry = None
    print(f"  ⚠️ CacheTelemetry not available: {_cte}")

# Query expansion — stop words, stemming, synonym aliases for better BM25 recall
try:
    from tokenpak.agent.vault.query_expansion import tokenize as _qe_tokenize, expand_query as _qe_expand
    _QUERY_EXPANSION_AVAILABLE = True
except ImportError:
    _QUERY_EXPANSION_AVAILABLE = False

# Backend protocol — pluggable retrieval backends and semantic scorers
try:
    from tokenpak.agent.vault.backend_protocol import (
        load_custom_backend as _load_custom_backend,
        load_custom_scorer as _load_custom_scorer,
    )
    _BACKEND_PROTOCOL_AVAILABLE = True
except ImportError:
    _BACKEND_PROTOCOL_AVAILABLE = False

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
# Pipeline Trace — captures per-request pipeline execution details
# ---------------------------------------------------------------------------
class _CompressionTimeout(Exception):
    """Raised internally when the compression pipeline exceeds MAX_COMPRESSION_TIME_MS."""


@dataclass
class StageTrace:
    """Trace for a single pipeline stage."""

    name: str  # capsule, segmentizer, recipe_engine, compaction, vault_injection, validation_gate
    enabled: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_delta: int = 0
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineTrace:
    """Complete trace for a request through the pipeline."""

    request_id: str
    timestamp: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_saved: int = 0
    cost_saved: float = 0.0
    total_cost: float = 0.0
    duration_ms: float = 0.0
    stages: List[StageTrace] = field(default_factory=list)
    status: str = "pending"  # pending, complete, error

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.stages]
        return d


class TraceStorage:
    """Thread-safe storage for recent pipeline traces."""

    def __init__(self, max_traces: int = 10):
        self._traces: deque = deque(maxlen=max_traces)
        self._lock = threading.Lock()
        self._by_id: Dict[str, PipelineTrace] = {}

    def store(self, trace: PipelineTrace):
        """Store a completed trace."""
        with self._lock:
            self._traces.append(trace)
            self._by_id[trace.request_id] = trace
            # Clean up old entries from _by_id
            if len(self._by_id) > len(self._traces) * 2:
                valid_ids = {t.request_id for t in self._traces}
                self._by_id = {k: v for k, v in self._by_id.items() if k in valid_ids}

    def get_last(self) -> Optional[PipelineTrace]:
        """Get the most recent trace."""
        with self._lock:
            return self._traces[-1] if self._traces else None

    def get_by_id(self, request_id: str) -> Optional[PipelineTrace]:
        """Get a specific trace by ID."""
        with self._lock:
            return self._by_id.get(request_id)

    def get_all(self) -> List[PipelineTrace]:
        """Get all stored traces."""
        with self._lock:
            return list(self._traces)


# Global trace storage
TRACE_STORAGE = TraceStorage(max_traces=10)


# ---------------------------------------------------------------------------
# Config — reads ~/.tokenpak/config.yaml with env var overrides
# ---------------------------------------------------------------------------
try:
    from tokenpak._internal.config_loader import get as _cfg

    print("📄 Config: ~/.tokenpak/config.yaml (env vars override)")
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

    print("📄 Config: env vars only (config_loader not available)")

# ---------------------------------------------------------------------------
# Named Workflow Profiles — TOKENPAK_PROFILE sets sensible flag bundles
# Profile is a floor: explicit env vars always win (setdefault semantics)
# ---------------------------------------------------------------------------
_PROFILE_PRESETS: dict[str, dict[str, str]] = {
    "safe": {
        "TOKENPAK_MODE": "strict",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "8000",
        "TOKENPAK_SKELETON_ENABLED": "false",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "balanced": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
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
    # CCG-06: Claude Code / transparent mode — zero body mutations; telemetry still captured
    "claude-code": {
        "TOKENPAK_MODE": "transparent",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "99999999",
        "TOKENPAK_SKELETON_ENABLED": "false",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "false",
        "TOKENPAK_BUDGET_CONTROLLER": "false",
        "TOKENPAK_ROUTER_ENABLED": "false",
        "TOKENPAK_TRACE": "true",
    },
    "transparent": {  # alias for claude-code
        "TOKENPAK_MODE": "transparent",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "99999999",
        "TOKENPAK_SKELETON_ENABLED": "false",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "false",
        "TOKENPAK_BUDGET_CONTROLLER": "false",
        "TOKENPAK_ROUTER_ENABLED": "false",
        "TOKENPAK_TRACE": "true",
    },
    # --- Claude Code consumption-mode profiles (CCI-04) ---
    # Each maps to a specific Claude Code usage pattern detected at request time.
    # Features within each profile are gated in their respective CCI task packets.
    "claude-code-cli": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
        "TOKENPAK_VAULT_INJECT": "true",
        "TOKENPAK_INJECT_BUDGET": "4000",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_CHAT_FOOTER": "false",
        "TOKENPAK_INLINE_SAVINGS": "false",
        "TOKENPAK_TRACE": "true",
        "TOKENPAK_SEMANTIC_CACHE": "false",
    },
    "claude-code-tui": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "3000",
        "TOKENPAK_VAULT_INJECT": "true",
        "TOKENPAK_INJECT_BUDGET": "4000",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "true",
        "TOKENPAK_SESSION_CAPSULES": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_CHAT_FOOTER": "true",
        "TOKENPAK_INLINE_SAVINGS": "true",
        "TOKENPAK_TRACE": "true",
        "TOKENPAK_SEMANTIC_CACHE": "false",
    },
    "claude-code-tmux": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
        "TOKENPAK_VAULT_INJECT": "true",
        "TOKENPAK_INJECT_BUDGET": "2000",
        "TOKENPAK_INJECT_TOP_K": "3",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_PER_SESSION_BUDGET": "true",
        "TOKENPAK_CHAT_FOOTER": "false",
        "TOKENPAK_INLINE_SAVINGS": "false",
        "TOKENPAK_STABILITY_SCORER": "true",
        "TOKENPAK_TRACE": "true",
        "TOKENPAK_SEMANTIC_CACHE": "false",
    },
    "claude-code-sdk": {
        "TOKENPAK_MODE": "transparent",
        "TOKENPAK_VAULT_INJECT": "false",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_REQUEST_LOGGER": "true",
        "TOKENPAK_OTLP_EXPORT": "true",
        "TOKENPAK_MUTATION_AUDIT": "true",
        "TOKENPAK_INLINE_SAVINGS": "false",
        "TOKENPAK_TRACE": "true",
        "TOKENPAK_SEMANTIC_CACHE": "false",
    },
    "claude-code-ide": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
        "TOKENPAK_VAULT_INJECT": "true",
        "TOKENPAK_INJECT_BUDGET": "3000",
        "TOKENPAK_INJECT_WORKSPACE_SCOPED": "true",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_INLINE_SAVINGS_HEADER": "true",
        "TOKENPAK_UPSTREAM_TIMEOUT": "30",
        "TOKENPAK_TRACE": "true",
        "TOKENPAK_SEMANTIC_CACHE": "false",
    },
    "claude-code-cron": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
        "TOKENPAK_VAULT_INJECT": "true",
        "TOKENPAK_INJECT_BUDGET": "4000",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_BUDGET_HARD_FAIL": "true",
        "TOKENPAK_CHAT_FOOTER": "false",
        "TOKENPAK_INLINE_SAVINGS": "false",
        "TOKENPAK_REQUEST_LOGGER": "true",
        "TOKENPAK_TELEGRAM_ALERTS": "true",
        "TOKENPAK_TRACE": "true",
        "TOKENPAK_SEMANTIC_CACHE": "false",
    },
}

ACTIVE_PROFILE: str = os.environ.get("TOKENPAK_PROFILE", "balanced").lower()
if ACTIVE_PROFILE in _PROFILE_PRESETS:
    for _pk, _pv in _PROFILE_PRESETS[ACTIVE_PROFILE].items():
        os.environ.setdefault(_pk, _pv)
    print(f"🎛️  Profile: {ACTIVE_PROFILE} (use TOKENPAK_PROFILE=safe|balanced|aggressive|agentic|claude-code-cli|claude-code-tui|claude-code-tmux|claude-code-sdk|claude-code-ide|claude-code-cron|claude-code|transparent)")
else:
    print(f"⚠️  Unknown TOKENPAK_PROFILE={ACTIVE_PROFILE!r} — ignoring, using env vars as-is")
    ACTIVE_PROFILE = "custom"

# ---------------------------------------------------------------------------
# Claude Code profile auto-detection (CCI-04)
# ---------------------------------------------------------------------------
# In-memory ring buffer: (session_id: str, timestamp: float) tuples.
# Used by _recent_distinct_session_count() for tmux detection without a DB.
_RECENT_SESSIONS: deque = deque(maxlen=500)
_RECENT_SESSIONS_LOCK = threading.Lock()


def _record_session_id(session_id: str) -> None:
    """Record a session ID observation for tmux detection."""
    with _RECENT_SESSIONS_LOCK:
        _RECENT_SESSIONS.append((session_id, time.time()))


def _recent_distinct_session_count(window_seconds: int = 60) -> int:
    """Count distinct X-Claude-Code-Session-Id values seen in the last window_seconds."""
    cutoff = time.time() - window_seconds
    with _RECENT_SESSIONS_LOCK:
        seen = {sid for sid, ts in _RECENT_SESSIONS if ts >= cutoff}
    return len(seen)


def _detect_claude_code_profile(headers: dict, body: Optional[dict] = None) -> Optional[str]:
    """
    Detect the appropriate Claude Code consumption-mode profile for a request.

    Detection precedence:
      1. TOKENPAK_PROFILE env var (if it starts with 'claude-code-') — explicit override
      2. IDE host User-Agent  → claude-code-ide
      3. Anthropic SDK (non-Claude-Code) User-Agent  → claude-code-sdk
      4. X-Claude-Code-NonInteractive header  → claude-code-cron
      5. X-Claude-Code-Interactive header  → claude-code-tui
      6. Multiple distinct session IDs in last 60s  → claude-code-tmux
      7. Any Claude Code User-Agent (fallback)  → claude-code-cli
      8. Non-Claude-Code traffic  → None (no profile change)

    Profiles defined in _PROFILE_PRESETS; features gated in individual CCI tasks.
    """
    # Explicit env var override always wins (existing TOKENPAK_PROFILE pattern)
    env_profile = os.environ.get("TOKENPAK_PROFILE", "")
    if env_profile.startswith("claude-code-"):
        return env_profile

    # Normalise header keys to lowercase for case-insensitive lookup
    h = {k.lower(): v for k, v in headers.items()}
    ua = (h.get("user-agent") or "").lower()

    is_claude_code = "claude-code/" in ua
    is_anthropic_sdk = (
        "anthropic-python/" in ua
        or "anthropic-sdk-typescript/" in ua
        or "claude-agent-sdk/" in ua
    )
    is_ide_host = any(
        host in ua for host in ["vscode/", "cursor/", "windsurf/", "jetbrains/"]
    )

    has_session_id = bool(h.get("x-claude-code-session-id"))
    is_interactive = bool(h.get("x-claude-code-interactive"))
    is_noninteractive = bool(h.get("x-claude-code-noninteractive"))

    if not (is_claude_code or is_anthropic_sdk or is_ide_host):
        return None  # non-Claude-Code traffic — no profile change

    if is_ide_host:
        return "claude-code-ide"
    if is_anthropic_sdk and not is_claude_code:
        return "claude-code-sdk"
    if is_noninteractive:
        return "claude-code-cron"
    if is_interactive:
        return "claude-code-tui"

    # tmux: multiple Claude Code workers share the same proxy → multiple distinct
    # session IDs appear within the recent window. Cheap in-memory deque, no DB.
    if has_session_id:
        session_id = h["x-claude-code-session-id"]
        _record_session_id(session_id)
        if _recent_distinct_session_count(60) >= 2:
            return "claude-code-tmux"

    return "claude-code-cli"  # default Claude Code fallback


PROXY_PORT = _cfg("port", 8766, "TOKENPAK_PORT", int)
LISTEN_ADDRESS = _cfg("listen_address", "127.0.0.1", "TOKENPAK_BIND_ADDRESS", str)
PROXY_AUTH_KEY = os.environ.get("TOKENPAK_PROXY_KEY", "")
DASHBOARD_AUTH_ENABLED = _cfg("dashboard.require_token", True, "TOKENPAK_DASHBOARD_AUTH", bool)
MONITOR_DB = _cfg("db", str(Path(__file__).parent / "monitor.db"), "TOKENPAK_DB", str)
BUDGET_DAILY_LIMIT_USD = float(os.environ.get("TOKENPAK_BUDGET_DAILY_LIMIT_USD", "0"))
BUDGET_ALERT_THRESHOLD_PCT = float(os.environ.get("TOKENPAK_BUDGET_ALERT_PCT", "80"))
# CCG-02: mutation_audit TTL — prune rows older than this many days
MUTATION_AUDIT_TTL_DAYS: int = int(os.environ.get("TOKENPAK_MUTATION_AUDIT_TTL_DAYS", "30"))
# ── Swap Pressure Monitoring ──────────────────────────────────────────────────
SWAP_PRESSURE_THRESHOLD_MB: int = int(os.environ.get("TOKENPAK_SWAP_WARN_MB", "600"))
_SWAP_WARN_LAST_LOGGED: float = 0.0
_SWAP_WARN_COOLDOWN_SEC: int = 300  # max once per 5 min
# Telegram alert fires at higher threshold (system-wide swap, not just process)
SWAP_TELEGRAM_ALERT_MB: int = int(os.environ.get("TOKENPAK_SWAP_ALERT_MB", "1024"))
_SWAP_TELEGRAM_LAST_SENT: float = 0.0
_SWAP_TELEGRAM_COOLDOWN_S: int = int(os.environ.get("TOKENPAK_SWAP_ALERT_COOLDOWN_S", "1800"))
_SWAP_TELEGRAM_CHAT_ID: str = os.environ.get("TOKENPAK_ALERT_CHAT_ID", "461720084")
SWAP_SELF_HEAL_SCRIPT: str = os.environ.get("TOKENPAK_SWAP_SELF_HEAL_SCRIPT", os.path.expanduser("~/vault/06_RUNTIME/scripts/self-heal-memory.sh"))
_SWAP_SELF_HEAL_LAST_RUN: float = 0.0
_SWAP_SELF_HEAL_COOLDOWN_S: int = int(os.environ.get("TOKENPAK_SWAP_SELF_HEAL_COOLDOWN_S", "1800"))


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


def _get_system_swap_mb() -> int:
    """Read system-wide swap from /proc/meminfo (not just this process)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("SwapTotal", 0)
        free = info.get("SwapFree", 0)
        return (total - free) // 1024
    except Exception:
        return 0


def _run_swap_self_heal(swap_mb: int) -> bool:
    """Run the external self-heal script (rate-limited)."""
    global _SWAP_SELF_HEAL_LAST_RUN
    import time as _time
    now = _time.time()
    if now - _SWAP_SELF_HEAL_LAST_RUN < _SWAP_SELF_HEAL_COOLDOWN_S:
        return False
    try:
        if not os.path.exists(SWAP_SELF_HEAL_SCRIPT):
            logging.warning("[swap_alert] self-heal script missing: %s", SWAP_SELF_HEAL_SCRIPT)
            return False
        proc = subprocess.run(
            [SWAP_SELF_HEAL_SCRIPT, str(SWAP_TELEGRAM_ALERT_MB)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        _SWAP_SELF_HEAL_LAST_RUN = now
        if proc.stdout:
            logging.info("[swap_alert] self-heal stdout: %s", proc.stdout.strip().replace("\n", " | "))
        if proc.stderr:
            logging.warning("[swap_alert] self-heal stderr: %s", proc.stderr.strip().replace("\n", " | "))
        logging.info("[swap_alert] self-heal exit=%s for swap=%dMB", proc.returncode, swap_mb)
        return proc.returncode == 0
    except Exception as _e:
        logging.warning("[swap_alert] self-heal failed: %s", _e)
        return False


def _send_swap_telegram_alert(swap_mb: int) -> None:
    """Fire a Telegram alert for high swap pressure (rate-limited)."""
    global _SWAP_TELEGRAM_LAST_SENT
    import time as _time
    import json as _json
    import urllib.request as _req
    import urllib.error as _uerr
    now = _time.time()
    if now - _SWAP_TELEGRAM_LAST_SENT < _SWAP_TELEGRAM_COOLDOWN_S:
        return
    try:
        cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(cfg_path) as _f:
            _cfg_data = _json.load(_f)
        token = _cfg_data.get("channels", {}).get("telegram", {}).get("botToken")
        if not token:
            return
        hostname = os.uname().nodename
        msg = (
            f"⚠️ <b>Swap alert — {hostname}</b>\n"
            f"System swap: {swap_mb}MB "
            f"(threshold: {SWAP_TELEGRAM_ALERT_MB}MB)\n"
            "Investigate memory pressure"
        )
        payload = _json.dumps({"chat_id": _SWAP_TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        _r = _req.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with _req.urlopen(_r, timeout=8) as _resp:
            _resp.read()
        _SWAP_TELEGRAM_LAST_SENT = now
        logging.info("[swap_alert] Telegram alert sent (swap=%dMB)", swap_mb)
    except Exception as _e:
        logging.debug("[swap_alert] Telegram send failed: %s", _e)


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
    # Check system-wide swap for self-heal + alert escalation (separate threshold)
    sys_swap_mb = _get_system_swap_mb()
    if sys_swap_mb >= SWAP_TELEGRAM_ALERT_MB:
        healed = _run_swap_self_heal(sys_swap_mb)
        post_heal_swap_mb = _get_system_swap_mb() if healed else sys_swap_mb
        if post_heal_swap_mb >= SWAP_TELEGRAM_ALERT_MB:
            _send_swap_telegram_alert(post_heal_swap_mb)
        else:
            logging.info("[swap_alert] self-heal resolved swap pressure: %dMB -> %dMB", sys_swap_mb, post_heal_swap_mb)
    return swap_mb

VAULT_SYNC_INTERVAL = 60
ENABLE_COMPACTION = _cfg("compression.enabled", True, "TOKENPAK_COMPACT", bool)
COMPACT_MAX_CHARS = _cfg("compression.max_chars", 120, "TOKENPAK_COMPACT_MAX_CHARS", int)
COMPACT_THRESHOLD_TOKENS = _cfg(
    "compression.threshold_tokens", 4500, "TOKENPAK_COMPACT_THRESHOLD_TOKENS", int
)
# Skip compression for very large payloads — compression savings are marginal (<3%) but
# synchronous processing adds 10-25s of silence before first SSE chunk, causing OpenClaw timeouts.
# Default: skip compression above 50,000 tokens (~200KB). Set to 0 to disable this cap.
COMPACT_MAX_TOKENS = _cfg(
    "compression.max_tokens", 50000, "TOKENPAK_COMPACT_MAX_TOKENS", int
)
COMPACT_CACHE_SIZE = _cfg("compression.cache_size", 2000, "TOKENPAK_COMPACT_CACHE_SIZE", int)
COMPILATION_MODE = _cfg("mode", "hybrid", "TOKENPAK_MODE", str).lower()

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
# CCI-10: Inline savings reporting — SSE event + IDE response header
INLINE_SAVINGS_ENABLED: bool = _cfg("features.inline_savings", False, "TOKENPAK_INLINE_SAVINGS", bool)
INLINE_SAVINGS_HEADER_ENABLED: bool = _cfg("features.inline_savings_header", False, "TOKENPAK_INLINE_SAVINGS_HEADER", bool)
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
    "features.budget_controller", False, "TOKENPAK_BUDGET_CONTROLLER", bool
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
DLP_ENABLED: bool = _cfg(
    "features.dlp", True, "TOKENPAK_DLP_ENABLED", bool
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
        print(f"  🔌 Plugin system: {len(_loaded)} plugin(s) loaded: {[p.name for p in _loaded]}")
    else:
        print("  🔌 Plugin system: no plugins configured")
except Exception as _plugin_init_err:
    print(f"  ⚠️ Plugin system init failed (disabled): {_plugin_init_err}")
    _plugin_registry = None


# --- Tier 2B Cache Registry singleton (initialized at module load if enabled) ---
_cache_registry = None
if CACHE_REGISTRY_ENABLED:
    try:
        from tokenpak.cache.registry import CacheRegistry

        _cache_registry = CacheRegistry()
        print(f"  🗄️  Cache registry initialized: {_cache_registry.names()}")
    except Exception as _cr_init_err:
        print(f"  ⚠️ Cache registry init failed (disabled): {_cr_init_err}")
        CACHE_REGISTRY_ENABLED = False

# CACHE-P4-001: CacheSpec singleton — loaded once at module startup from config
CACHE_SPEC: Optional["_CacheSpec"] = None
if _CACHE_SPEC_AVAILABLE:
    try:
        CACHE_SPEC = _load_cache_spec_from_config(_cfg)
        print(
            f"  🗂️  CacheSpec initialized: enabled={CACHE_SPEC.enabled}, "
            f"fallback={CACHE_SPEC.fallback_policy.value}"
        )
    except Exception as _cs_init_err:
        print(f"  ⚠️ CacheSpec init failed (cache disabled): {_cs_init_err}")

# CACHE-P4-002: CacheTelemetry singleton — per-provider hit/miss/mode tracking
CACHE_TELEMETRY: Optional["_CacheTelemetry"] = None
if _CACHE_TELEMETRY_AVAILABLE:
    try:
        CACHE_TELEMETRY = _CacheTelemetry()
        print("  📊 CacheTelemetry initialized: per-provider tracking enabled")
    except Exception as _ct_init_err:
        print(f"  ⚠️ CacheTelemetry init failed: {_ct_init_err}")

# Upstream
UPSTREAM_TIMEOUT: int = _cfg("upstream.timeout", 90, "TOKENPAK_UPSTREAM_TIMEOUT", int)
STRICT_VALIDATION: bool = _cfg("features.strict_mode", False, "TOKENPAK_STRICT_MODE", bool)

# Connection pool manager — one pool per upstream host, reused across requests
# Replaces per-request http.client.HTTPSConnection in _proxy_to() for ~100-150ms savings
_POOL_MANAGER = urllib3.PoolManager(
    num_pools=10,
    maxsize=10,
    retries=False,  # We handle retries ourselves
    timeout=urllib3.Timeout(connect=10.0, read=UPSTREAM_TIMEOUT),
    cert_reqs="CERT_REQUIRED",
)

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
SEMANTIC_BACKEND = _cfg(
    "vault.semantic_backend", "", "TOKENPAK_SEMANTIC_BACKEND", str
)

# Term-Card Resolver
TERM_RESOLVER_ENABLED: bool = _cfg(
    "features.term_resolver", False, "TOKENPAK_TERM_RESOLVER_ENABLED", bool
)
TERM_RESOLVER_TOP_K: int = _cfg("term_resolver.top_k", 3, "TOKENPAK_TERM_RESOLVER_TOP_K", int)
TERM_RESOLVER_MAX_BYTES: int = _cfg(
    "term_resolver.max_bytes", 200, "TOKENPAK_TERM_RESOLVER_MAX_BYTES", int
)

_COMPACT_CACHE = {}
_COMPACT_CACHE_ORDER = []

ADAPTER_REGISTRY = build_default_registry()


def _load_openclaw_upstream_overrides() -> Dict[str, str]:
    """
    Auto-discover upstream routes from openclaw.json tokenpak-* provider mirrors.
    Supports current OpenClaw shape at `models.providers` and legacy root `providers`.
    """
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    if not cfg_path.exists():
        return {}

    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:
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
    routes.update(_load_openclaw_upstream_overrides())
    routes.update(_load_env_upstream_overrides())
    return routes


UPSTREAM_ROUTES = _build_upstream_routes()


# API Key Pool — multi-key rotation for 401/429 failover
# ---------------------------------------------------------------------------
# Env vars: ANTHROPIC_API_KEY, ANTHROPIC_OAUTH_TOKEN, ANTHROPIC_OAUTH_TOKEN2
# Config:
#   TOKENPAK_KEY_ROTATION     — failover (default) | roundrobin
#   TOKENPAK_KEY_COOLDOWN_429 — seconds for rate-limit cooldown (default 60)
#   TOKENPAK_KEY_COOLDOWN_401 — seconds for invalid-key cooldown (default 300)

_KEY_ROTATION_MODE: str = os.environ.get("TOKENPAK_KEY_ROTATION", "failover")
_KEY_COOLDOWN_429: float = float(os.environ.get("TOKENPAK_KEY_COOLDOWN_429", "60"))
_KEY_COOLDOWN_401: float = float(os.environ.get("TOKENPAK_KEY_COOLDOWN_401", "300"))

# Build pool from all ANTHROPIC_* vars at startup
def _build_key_pool() -> list:
    candidates = [
        os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        os.environ.get("ANTHROPIC_OAUTH_TOKEN", "").strip(),
        os.environ.get("ANTHROPIC_OAUTH_TOKEN2", "").strip(),
    ]
    pool = [k for k in candidates if k]
    # Log count but never the keys themselves
    print(f"[key-pool] Found {len(pool)} Anthropic API key(s)", flush=True)
    return pool

_ANTHROPIC_KEY_POOL: list = _build_key_pool()

# ---------------------------------------------------------------------------
# ChatGPT Codex OAuth credentials — read from ~/.codex/auth.json
# ---------------------------------------------------------------------------
_CODEX_AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
_CODEX_CREDS_CACHE: dict = {"mtime": 0.0, "access_token": "", "account_id": ""}
_CODEX_CREDS_LOCK = threading.Lock()

def _load_codex_credentials() -> Tuple[str, str]:
    try:
        st = os.stat(_CODEX_AUTH_PATH)
    except OSError:
        return "", ""
    with _CODEX_CREDS_LOCK:
        if st.st_mtime != _CODEX_CREDS_CACHE["mtime"]:
            try:
                with open(_CODEX_AUTH_PATH, "r") as f:
                    data = json.load(f)
                tokens = data.get("tokens", {}) if isinstance(data, dict) else {}
                _CODEX_CREDS_CACHE["access_token"] = tokens.get("access_token", "") or ""
                _CODEX_CREDS_CACHE["account_id"] = tokens.get("account_id", "") or ""
                _CODEX_CREDS_CACHE["mtime"] = st.st_mtime
                print(f"[codex-auth] Loaded ~/.codex/auth.json (token={'yes' if _CODEX_CREDS_CACHE['access_token'] else 'no'})", flush=True)
            except (OSError, ValueError) as exc:
                print(f"[codex-auth] Failed to read {_CODEX_AUTH_PATH}: {exc}", flush=True)
                return "", ""
        return _CODEX_CREDS_CACHE["access_token"], _CODEX_CREDS_CACHE["account_id"]

# ---------------------------------------------------------------------------
# Claude CLI OAuth credentials — read from ~/.claude/.credentials.json
# ---------------------------------------------------------------------------
_CLAUDE_CLI_CREDS_PATH = os.path.expanduser("~/.claude/.credentials.json")
_CLAUDE_CLI_CREDS_CACHE: dict = {"mtime": 0.0, "access_token": ""}
_CLAUDE_CLI_CREDS_LOCK = threading.Lock()

def _load_claude_cli_token() -> str:
    try:
        st = os.stat(_CLAUDE_CLI_CREDS_PATH)
    except OSError:
        return ""
    with _CLAUDE_CLI_CREDS_LOCK:
        if st.st_mtime != _CLAUDE_CLI_CREDS_CACHE["mtime"]:
            try:
                with open(_CLAUDE_CLI_CREDS_PATH, "r") as f:
                    data = json.load(f)
                oauth = data.get("claudeAiOauth", {}) if isinstance(data, dict) else {}
                _CLAUDE_CLI_CREDS_CACHE["access_token"] = oauth.get("accessToken", "") or ""
                _CLAUDE_CLI_CREDS_CACHE["mtime"] = st.st_mtime
                print(f"[claude-cli-auth] Loaded ~/.claude/.credentials.json (token={'yes' if _CLAUDE_CLI_CREDS_CACHE['access_token'] else 'no'})", flush=True)
            except (OSError, ValueError) as exc:
                print(f"[claude-cli-auth] Failed to read {_CLAUDE_CLI_CREDS_PATH}: {exc}", flush=True)
                return ""
        return _CLAUDE_CLI_CREDS_CACHE["access_token"]



def _reload_config_from_env() -> str:
    """Hot-reload env vars on SIGHUP or POST /config/reload.

    Updates the key pool and UPSTREAM_TIMEOUT from the current environment
    without restarting the proxy. In-flight requests are not interrupted.
    Returns a human-readable summary string.
    """
    global _ANTHROPIC_KEY_POOL, UPSTREAM_TIMEOUT
    old_pool_size = len(_ANTHROPIC_KEY_POOL)
    _ANTHROPIC_KEY_POOL = _build_key_pool()
    new_pool_size = len(_ANTHROPIC_KEY_POOL)

    old_timeout = UPSTREAM_TIMEOUT
    UPSTREAM_TIMEOUT = _cfg("upstream.timeout", 90, "TOKENPAK_UPSTREAM_TIMEOUT", int)

    msg = (
        f"SIGHUP: config reloaded — "
        f"keys: {old_pool_size} → {new_pool_size}, "
        f"timeout: {old_timeout}s → {UPSTREAM_TIMEOUT}s"
    )
    print(f"[config] {msg}", flush=True)
    return msg


# Per-key cooldown state: {key_index: cooldown_until_timestamp}
_KEY_COOLDOWN_STATE: dict = {}
_KEY_COOLDOWN_LOCK = threading.Lock()
# Round-robin counter (only used in roundrobin mode)
_KEY_RR_INDEX: int = 0
_KEY_RR_LOCK = threading.Lock()


def _key_is_available(idx: int) -> bool:
    """Return True if key at idx is not in cooldown."""
    with _KEY_COOLDOWN_LOCK:
        until = _KEY_COOLDOWN_STATE.get(idx, 0)
    return time.time() >= until


def _cool_down_key(idx: int, duration: float, reason: str) -> None:
    """Set cooldown on a key."""
    with _KEY_COOLDOWN_LOCK:
        _KEY_COOLDOWN_STATE[idx] = time.time() + duration
    key_hint = (_ANTHROPIC_KEY_POOL[idx][:8] + "...") if _ANTHROPIC_KEY_POOL else "?"
    print(f"[key-pool] Key #{idx} ({key_hint}) cooling down for {duration}s — reason: {reason}", flush=True)


def _get_next_key(exclude_idx: Optional[int] = None) -> tuple:
    """
    Return (key, index) for the next available key.
    In failover mode: always start from idx 0, skip cooled-down.
    In roundrobin mode: start from next round-robin index.
    Returns (None, -1) if no keys available.
    """
    global _KEY_RR_INDEX
    if not _ANTHROPIC_KEY_POOL:
        return None, -1

    if _KEY_ROTATION_MODE == "roundrobin":
        with _KEY_RR_LOCK:
            start = _KEY_RR_INDEX
            for i in range(len(_ANTHROPIC_KEY_POOL)):
                idx = (start + i) % len(_ANTHROPIC_KEY_POOL)
                if idx != exclude_idx and _key_is_available(idx):
                    _KEY_RR_INDEX = (idx + 1) % len(_ANTHROPIC_KEY_POOL)
                    return _ANTHROPIC_KEY_POOL[idx], idx
    else:
        # failover: try in order, skip excluded and cooled-down
        for idx, key in enumerate(_ANTHROPIC_KEY_POOL):
            if idx != exclude_idx and _key_is_available(idx):
                return key, idx

    return None, -1


def _strip_empty_text_blocks(body_bytes):
    """Remove empty text blocks from system/messages — Anthropic rejects them."""
    try:
        data = json.loads(body_bytes)
        changed = False
        # Clean system blocks
        system = data.get("system")
        if isinstance(system, list):
            cleaned = [b for b in system if not (isinstance(b, dict) and b.get("type") == "text" and not b.get("text", "").strip())]
            if len(cleaned) != len(system):
                data["system"] = cleaned
                changed = True
        # Clean message content blocks
        for msg in data.get("messages", []):
            content = msg.get("content")
            if isinstance(content, list):
                cleaned = [p for p in content if not (isinstance(p, dict) and p.get("type") == "text" and not p.get("text", "").strip())]
                if len(cleaned) != len(content):
                    # Ensure at least one content block remains
                    if cleaned:
                        msg["content"] = cleaned
                    else:
                        msg["content"] = [{"type": "text", "text": " "}]
                    changed = True
            elif isinstance(content, str) and not content.strip():
                msg["content"] = " "
                changed = True
        if changed:
            return json.dumps(data).encode()
        return body_bytes
    except Exception:
        return body_bytes


def _cap_cache_control_blocks(body_bytes, max_blocks=4):
    """Anthropic allows max 4 cache_control blocks. Strip extras (including tools)."""
    try:
        body = json.loads(body_bytes)
    except Exception:
        return body_bytes
    locations = []
    system = body.get("system", [])
    if isinstance(system, list):
        for i, block in enumerate(system):
            if isinstance(block, dict) and "cache_control" in block:
                locations.append(("system", i))
    tools = body.get("tools", [])
    if isinstance(tools, list):
        for i, tool in enumerate(tools):
            if isinstance(tool, dict) and "cache_control" in tool:
                locations.append(("tools", i))
    for mi, msg in enumerate(body.get("messages", [])):
        c = msg.get("content", [])
        if isinstance(c, list):
            for ci, block in enumerate(c):
                if isinstance(block, dict) and "cache_control" in block:
                    locations.append(("messages", mi, ci))
    if len(locations) <= max_blocks:
        return body_bytes
    to_remove = locations[:-max_blocks]
    for loc in to_remove:
        if loc[0] == "system":
            body["system"][loc[1]].pop("cache_control", None)
        elif loc[0] == "tools":
            body["tools"][loc[1]].pop("cache_control", None)
        else:
            body["messages"][loc[1]]["content"][loc[2]].pop("cache_control", None)
    print(
        f"  🔧 Capped cache_control: {len(locations)} -> {max_blocks} (removed from: {[l[0] for l in to_remove]})"
    )
    return json.dumps(body).encode()


# ---------------------------------------------------------------------------
# CACHE-P3-001: Anthropic top-level auto cache mode
# ---------------------------------------------------------------------------

class CacheMode(Enum):
    AUTO = "auto"       # Top-level request-level cache_control (Anthropic auto mode)
    EXPLICIT = "explicit"  # Per-block cache_control markers (existing behavior)


def _select_anthropic_cache_mode(headers: dict, body_dict: dict) -> CacheMode:
    """Select auto vs explicit cache mode for an Anthropic request.

    Pops 'tokenpak_cache_mode' from body_dict if present — it must not be
    forwarded upstream.  Header takes precedence over body field; both take
    precedence over the conversation-length heuristic.
    """
    mode_hint = headers.get("x-tokenpak-cache-mode") or body_dict.pop("tokenpak_cache_mode", None)
    if mode_hint == "explicit":
        return CacheMode.EXPLICIT
    if mode_hint == "auto":
        return CacheMode.AUTO
    # Default: auto for multi-turn (>2 messages), explicit for short/single-turn
    if len(body_dict.get("messages", [])) > 2:
        return CacheMode.AUTO
    return CacheMode.EXPLICIT


def _apply_anthropic_auto_cache(body_dict: dict) -> None:
    """Apply Anthropic top-level auto cache mode (in-place).

    Strips per-block cache_control markers injected by earlier pipeline stages
    and sets a single top-level cache_control field.  Anthropic automatically
    moves the cache breakpoint to the last cacheable block as the conversation
    grows, making this the preferred mode for multi-turn sessions.

    API reference: top-level ``cache_control: {"type": "ephemeral"}`` on the
    messages endpoint (same response fields as explicit mode —
    cache_creation_input_tokens and cache_read_input_tokens in usage).
    """
    for block in body_dict.get("system", []):
        if isinstance(block, dict):
            block.pop("cache_control", None)
    for tool in body_dict.get("tools", []):
        if isinstance(tool, dict):
            tool.pop("cache_control", None)
    for msg in body_dict.get("messages", []):
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    body_dict["cache_control"] = {"type": "ephemeral"}  # noqa: anthropic-only — caller guards Provider.ANTHROPIC


def _resolve_upstream(adapter: FormatAdapter) -> str:
    mapped = UPSTREAM_ROUTES.get(adapter.source_format)
    if mapped:
        return mapped

    # Hard fail for passthrough: unknown/undetected providers must be explicitly routed.
    if adapter.source_format == "passthrough":
        raise ValueError(
            "No upstream route mapping for passthrough requests. "
            "Configure models.providers tokenpak-* source providers or set "
            "TOKENPAK_UPSTREAM_PASSTHROUGH."
        )

    return adapter.get_default_upstream()


def _extract_host(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            return parsed.hostname
        return parsed.netloc.split(":")[0]
    except Exception:
        return ""


INTERCEPT_HOSTS = {
    host for host in (_extract_host(url) for url in UPSTREAM_ROUTES.values()) if host
}

# Ollama upstream routing — requests with /ollama-proxy/ prefix get forwarded here
OLLAMA_UPSTREAM = _cfg(
    "upstream.ollama", "http://100.80.241.118:11434", "TOKENPAK_OLLAMA_UPSTREAM", str
)
OLLAMA_CONNECT_TIMEOUT = _cfg("upstream.ollama_timeout", 20, "TOKENPAK_OLLAMA_TIMEOUT", int)

# Circuit breaker for ollama upstream -- avoids repeated 2-min TCP hangs
_ollama_circuit = {
    "open": False,  # True = upstream known-dead, skip attempts
    "last_failure": 0.0,  # timestamp of last failure
    "cooldown": 120,  # seconds before retrying after failure
}
_ollama_circuit_lock = threading.Lock()

# Fix #5: Per-provider circuit breakers (Anthropic, OpenAI, Google)
_provider_circuits: dict = {
    "anthropic": {
        "failures": 0,
        "open": False,
        "last_failure": 0.0,
        "threshold": 5,
        "cooldown": 60,
    },
    "openai": {"failures": 0, "open": False, "last_failure": 0.0, "threshold": 5, "cooldown": 60},
    "google": {"failures": 0, "open": False, "last_failure": 0.0, "threshold": 5, "cooldown": 60},
}
_provider_circuit_lock = threading.Lock()


def _provider_for_url(url: str) -> str:
    """Map *url* to a circuit-breaker key via ``detect_provider``.

    Returns a plain string (the Provider enum value) for backward
    compatibility with the ``_provider_circuits`` dict keys.  The special
    mapping ``GEMINI -> "google"`` preserves the existing circuit key.
    """
    prov = detect_provider(url)
    if prov is Provider.UNKNOWN:
        return ""
    # The circuit-breaker dict uses "google" for Gemini endpoints.
    if prov is Provider.GEMINI:
        return "google"
    return prov.value


def _circuit_check(provider: str) -> bool:
    """Return True if circuit is OPEN (requests should be rejected)."""
    if not provider:
        return False
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if not cb:
            return False
        if cb["open"]:
            if time.time() - cb["last_failure"] > cb["cooldown"]:
                cb["open"] = False
                cb["failures"] = 0
                print(f"  ✅ Circuit breaker CLOSED for {provider} (cooldown expired)")
                return False
            return True
        return False


def _circuit_record_failure(provider: str):
    if not provider:
        return
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if not cb:
            return
        cb["failures"] += 1
        cb["last_failure"] = time.time()
        if cb["failures"] >= cb["threshold"]:
            cb["open"] = True
            print(f"  ⚡ Circuit breaker OPEN for {provider} after {cb['failures']} failures")


def _circuit_record_success(provider: str):
    if not provider:
        return
    with _provider_circuit_lock:
        cb = _provider_circuits.get(provider)
        if cb:
            cb["failures"] = 0
            cb["open"] = False


# ---------------------------------------------------------------------------
# CCI-05: Provider Failover Chain (Anthropic 5xx/timeout → Bedrock → Vertex → queue)
# ---------------------------------------------------------------------------
# TOKENPAK_FALLBACK_CHAIN: comma-separated ordered provider list.
# Default is "anthropic" only — failover is opt-in.
# Example: TOKENPAK_FALLBACK_CHAIN=anthropic,bedrock,vertex,queue
_FALLBACK_CHAIN_RAW: str = os.environ.get("TOKENPAK_FALLBACK_CHAIN", "anthropic")
_FALLBACK_CHAIN: List[str] = [p.strip().lower() for p in _FALLBACK_CHAIN_RAW.split(",") if p.strip()]

# Bedrock base URL — can be overridden via TOKENPAK_BEDROCK_BASE_URL
_BEDROCK_BASE_URL: str = os.environ.get(
    "TOKENPAK_BEDROCK_BASE_URL",
    "https://bedrock-runtime.us-east-1.amazonaws.com",
)
# Vertex AI base URL — can be overridden via TOKENPAK_VERTEX_BASE_URL
_VERTEX_BASE_URL: str = os.environ.get(
    "TOKENPAK_VERTEX_BASE_URL",
    "https://us-east5-aiplatform.googleapis.com",
)
# Vertex project ID — required if vertex is in the chain
_VERTEX_PROJECT: str = os.environ.get("TOKENPAK_VERTEX_PROJECT", "")

# SQLite queue for the "queue" fallback provider
_FAILOVER_QUEUE_DB: str = os.environ.get(
    "TOKENPAK_FAILOVER_QUEUE_DB",
    str(Path(__file__).parent / "failover_queue.db"),
)

# In-memory failover event log (thread-safe, capped at 500 events)
_FAILOVER_EVENTS: deque = deque(maxlen=500)
_FAILOVER_EVENTS_LOCK = threading.Lock()


def _log_failover_event(
    from_provider: str,
    to_provider: str,
    reason: str,
    model: str,
    status_code: int = 0,
    profile: str = "",
) -> None:
    """Append a failover event to the in-memory log."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "from_provider": from_provider,
        "to_provider": to_provider,
        "reason": reason,
        "model": model,
        "status_code": status_code,
        "profile": profile,
    }
    with _FAILOVER_EVENTS_LOCK:
        _FAILOVER_EVENTS.append(event)
    print(
        f"[failover] {from_provider} → {to_provider} | reason={reason} | model={model} | profile={profile}",
        flush=True,
    )


# Model name translation table: Anthropic model id → provider-specific id.
# Same model, different API surface name.  No model substitution.
_MODEL_TRANSLATION: Dict[str, Dict[str, str]] = {
    # Claude 3.5 Sonnet
    "claude-3-5-sonnet-20241022": {
        "bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "vertex": "claude-3-5-sonnet@20241022",
    },
    "claude-3-5-sonnet-latest": {
        "bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "vertex": "claude-3-5-sonnet@20241022",
    },
    # Claude 3.5 Haiku
    "claude-3-5-haiku-20241022": {
        "bedrock": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "vertex": "claude-3-5-haiku@20241022",
    },
    "claude-3-5-haiku-latest": {
        "bedrock": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "vertex": "claude-3-5-haiku@20241022",
    },
    # Claude 3 Opus
    "claude-3-opus-20240229": {
        "bedrock": "anthropic.claude-3-opus-20240229-v1:0",
        "vertex": "claude-3-opus@20240229",
    },
    "claude-3-opus-latest": {
        "bedrock": "anthropic.claude-3-opus-20240229-v1:0",
        "vertex": "claude-3-opus@20240229",
    },
    # Claude 3 Sonnet
    "claude-3-sonnet-20240229": {
        "bedrock": "anthropic.claude-3-sonnet-20240229-v1:0",
        "vertex": "claude-3-sonnet@20240229",
    },
    # Claude 3 Haiku
    "claude-3-haiku-20240307": {
        "bedrock": "anthropic.claude-3-haiku-20240307-v1:0",
        "vertex": "claude-3-haiku@20240307",
    },
    # Claude 4 Sonnet (claude-sonnet-4-5 / claude-sonnet-4-6 shorthand)
    "claude-sonnet-4-5": {
        "bedrock": "anthropic.claude-sonnet-4-5-20251101-v1:0",
        "vertex": "claude-sonnet-4-5@20251101",
    },
    "claude-sonnet-4-6": {
        "bedrock": "anthropic.claude-sonnet-4-6-20260101-v1:0",
        "vertex": "claude-sonnet-4-6@20260101",
    },
    "claude-opus-4-6": {
        "bedrock": "anthropic.claude-opus-4-6-20260101-v1:0",
        "vertex": "claude-opus-4-6@20260101",
    },
    # Aliases / shorthand used by Claude Code CLI
    "sonnet": {
        "bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "vertex": "claude-3-5-sonnet@20241022",
    },
    "haiku": {
        "bedrock": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "vertex": "claude-3-5-haiku@20241022",
    },
    "opus": {
        "bedrock": "anthropic.claude-3-opus-20240229-v1:0",
        "vertex": "claude-3-opus@20240229",
    },
}


def _translate_model(model_id: str, provider: str) -> str:
    """
    Translate an Anthropic model id to the provider-specific model id.
    Returns the original model_id if no mapping exists (pass-through).
    Never substitutes a different model family — only maps same model to provider API name.
    """
    entry = _MODEL_TRANSLATION.get(model_id, {})
    return entry.get(provider, model_id)


def _build_failover_url(provider: str, original_url: str, model: str) -> str:
    """
    Build the target URL for a fallback provider.
    Returns "" if provider is not supported / credentials missing.
    """
    if provider == "bedrock":
        bedrock_model = _translate_model(model, "bedrock")
        return f"{_BEDROCK_BASE_URL}/model/{bedrock_model}/invoke"
    if provider == "vertex":
        if not _VERTEX_PROJECT:
            print("[failover] vertex: TOKENPAK_VERTEX_PROJECT not set — skipping vertex", flush=True)
            return ""
        vertex_model = _translate_model(model, "vertex")
        return (
            f"{_VERTEX_BASE_URL}/v1/projects/{_VERTEX_PROJECT}"
            f"/locations/us-east5/publishers/anthropic/models/{vertex_model}:streamRawPredict"
        )
    return ""


def _build_failover_headers(provider: str, original_headers: dict) -> dict:
    """
    Build provider-specific headers for the fallback request.
    Bedrock uses AWS SigV4 (injected by boto3 or env-level signing if available).
    Vertex uses Google OAuth Bearer.
    """
    headers = {k: v for k, v in original_headers.items()}
    # Strip Anthropic auth headers
    for key in list(headers.keys()):
        if key.lower() in ("x-api-key", "authorization"):
            del headers[key]

    if provider == "bedrock":
        # Bedrock uses AWS SigV4 — boto3 session signing is out of scope for proxy-layer.
        # We pass AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from env and let boto3 sign.
        # If boto3 not available, attempt unsigned (will 403 but caller can skip).
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        # Note: actual signing handled by boto3 session if available; otherwise unsigned stub
    elif provider == "vertex":
        gcp_token = os.environ.get("TOKENPAK_VERTEX_TOKEN", "")
        if gcp_token:
            headers["Authorization"] = f"Bearer {gcp_token}"
        headers["Content-Type"] = "application/json"
    return headers


def _write_failover_queue(body: Optional[bytes], model: str, profile: str) -> str:
    """
    Write a failed request to the local SQLite failover queue.
    Returns the row id as a string for the Retry-After header.
    Table is created on first write.
    """
    import sqlite3 as _sqlite3

    try:
        conn = _sqlite3.connect(_FAILOVER_QUEUE_DB, timeout=5)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS failover_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queued_at TEXT NOT NULL,
                model TEXT NOT NULL,
                profile TEXT NOT NULL,
                body BLOB,
                status TEXT NOT NULL DEFAULT 'pending'
            )"""
        )
        cur = conn.execute(
            "INSERT INTO failover_queue (queued_at, model, profile, body) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), model, profile, body),
        )
        conn.commit()
        row_id = str(cur.lastrowid)
        conn.close()
        return row_id
    except Exception as _qe:
        print(f"[failover] queue write failed: {_qe}", flush=True)
        return "0"


# Per-IP rate limiting — token bucket, 60 req/min per IP by default
_RATE_LIMIT_RPM = _cfg("rate_limit_rpm", 60, "TOKENPAK_RATE_LIMIT_RPM", int)
_rate_buckets: dict = {}
_rate_bucket_lock = threading.Lock()

# Request body size limit — configurable, default 10 MB
_MAX_REQUEST_BYTES: int = int(os.environ.get("TOKENPAK_MAX_REQUEST_SIZE", str(10 * 1024 * 1024)))

# Headers that must NEVER be forwarded upstream (security / hop-by-hop)
_BLOCKED_FORWARD_HEADERS: frozenset = frozenset(
    {
        "host",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "content-length",
        "accept-encoding",
        "x-forwarded-for",
        "x-real-ip",
        "x-forwarded-host",  # prevent IP spoofing upstream
        "x-tokenpak-bypass",  # internal header — never forward to upstream
        "x-tokenpak-cache-key",  # CACHE-P2-001: extracted and translated, not forwarded raw
        "x-tokenpak-cache-retention",  # CACHE-P2-001: extracted and translated, not forwarded raw
    }
)

# CCG-04: Per-route HTTP header forwarding allowlists.
# Mirrors the WS-path tuple (proxy.py line ~7303) onto the HTTP path.
# OPENCLAW_HEADER_ALLOWLIST must never gain new entries — OpenClaw traffic
# must produce exactly the same forwarded headers as before (bit-for-bit).
# CLAUDE_CODE_HEADER_ALLOWLIST extends it with Claude Code-specific headers.
OPENCLAW_HEADER_ALLOWLIST: tuple = (
    "x-api-key",
    "authorization",
    "anthropic-version",
    "anthropic-beta",
)
CLAUDE_CODE_HEADER_ALLOWLIST: tuple = (
    "x-api-key",
    "authorization",
    "anthropic-version",
    "anthropic-beta",
    "anthropic-dangerous-direct-browser-access",
    "x-claude-code-session-id",
    "user-agent",
)


def _sanitize_headers(raw_headers) -> dict:
    """Build a clean forwarding header dict, stripping hop-by-hop and dangerous headers."""
    result = {}
    for key in raw_headers:
        if key.lower() in _BLOCKED_FORWARD_HEADERS:
            continue
        result[key] = raw_headers[key]
    return result


def _classify_route(path: str, headers) -> str:
    """Classify an incoming HTTP request as 'claude-code' or 'openclaw'.

    Inspects headers only — no DB access, no network round-trips.
    Claude Code wins when both X-Claude-Code-Session-Id and X-OpenClaw-Session
    are present (matching CCG-03's resolver priority order).

    Returns:
        "claude-code"  if X-Claude-Code-Session-Id is present (case-insensitive)
        "openclaw"     otherwise
    """
    if hasattr(headers, "items"):
        for k, _ in headers.items():
            if k.lower() == "x-claude-code-session-id":
                return "claude-code"
    elif hasattr(headers, "get"):
        for variant in ("X-Claude-Code-Session-Id", "x-claude-code-session-id"):
            if headers.get(variant):
                return "claude-code"
    return "openclaw"


def _resolve_session_id(headers, model: str) -> str:
    """Resolve session id with Claude Code priority.

    Order: X-Claude-Code-Session-Id (Claude Code) -> X-OpenClaw-Session
    (OpenClaw) -> model name (last-resort fallback).
    """
    # Case-insensitive header lookup
    def _h(name):
        if hasattr(headers, "get"):
            # Try common cases first; many header collections are
            # case-insensitive but some test contexts use plain dicts.
            for variant in (name, name.lower(), name.title()):
                v = headers.get(variant)
                if v:
                    return v
        return None

    cc_id = _h("X-Claude-Code-Session-Id")
    if cc_id:
        return cc_id
    oc_id = _h("X-OpenClaw-Session")
    if oc_id:
        return oc_id
    return model


def _suggest_model(requested: str) -> Optional[str]:
    """Return the closest known model name for a given (possibly wrong) model string."""
    import sys as _sys

    _mod = _sys.modules[__name__]
    _known = list(getattr(_mod, "MODEL_COSTS", {}).keys())
    if not _known or not requested:
        return None
    req_l = requested.lower()
    # Exact partial match first
    for m in _known:
        if req_l in m or m in req_l:
            return m
    # Fallback: pick by prefix (provider family)
    for prefix in ("claude", "gpt", "gemini"):
        if req_l.startswith(prefix):
            candidates = [m for m in _known if m.startswith(prefix)]
            if candidates:
                return candidates[-1]  # newest in list
    return _known[0] if _known else None


def _make_structured_error(
    error_type: str, message: str, suggestion: str, status: int = 400, **extra
) -> dict:
    """Build a flat, user-facing structured error response.

    Returns a dict of the form::

        {"error": "<type>", "message": "<message>", "suggestion": "<suggestion>", ...extra}

    This is the canonical format for user-facing errors surfaced directly by the proxy
    (not forwarded from upstream).  Upstream errors go through _enrich_upstream_error instead.
    """
    payload: dict = {"error": error_type, "message": message, "suggestion": suggestion}
    payload.update(extra)
    return payload


def _enrich_upstream_error(
    normalized: dict, status: int, retry_after_header: Optional[str] = None
) -> dict:
    """Add actionable ``hint`` / ``suggestion`` and ``retry_after`` fields to a normalized error dict.

    Covers five key error paths:
      1. Invalid API key (401 / authentication_error)
      2. Model not found (404 / model_not_found / not_found_error)
      3. Rate limit exceeded (429 / rate_limit_error)
      4. Malformed request body (400 / validation_error / invalid_request_error)
      5. Provider unavailable (502 / 503 / provider_unavailable)
    """
    err = normalized.get("error", {})
    err_type = err.get("type", "")
    err_msg = err.get("message", "").lower()

    # 1. Invalid API key
    if status == 401 or err_type in ("authentication_error", "auth_error", "invalid_api_key"):
        suggestion = (
            "Your API key was rejected by the upstream provider. "
            "Check that the key is valid and has not expired. "
            "Anthropic keys: https://console.anthropic.com/settings/keys | "
            "OpenAI keys: https://platform.openai.com/api-keys"
        )
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 2. Model not found
    elif (
        status == 404
        or err_type in ("model_not_found", "not_found_error")
        or "model" in err_msg
        and "not found" in err_msg
    ):
        _req_model = err.get("model") or ""
        if not _req_model:
            # Try to extract model name from message: "model 'xyz' does not exist"
            import re as _re

            _m = _re.search(r"model[:\s]+['\"]?([^\s'\"]+)['\"]?", err.get("message", ""), _re.I)
            _req_model = _m.group(1) if _m else ""
        _suggested = _suggest_model(_req_model) if _req_model else None
        suggestion = "The requested model was not found on the upstream provider."
        if _suggested:
            suggestion += f" Did you mean: '{_suggested}'?"
        suggestion += " Check the model ID in your request matches a supported model."
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 3. Rate limit hit
    elif status == 429 or err_type in ("rate_limit_error", "rate_limit_exceeded"):
        _ra = retry_after_header or err.get("retry_after")
        suggestion = "Provider returned 429 — upstream rate limit exceeded."
        if _ra:
            try:
                err["retry_after"] = int(float(_ra))
                suggestion += f" Retry after {err['retry_after']} seconds."
            except (ValueError, TypeError):
                err["retry_after"] = _ra
        suggestion += (
            " Consider implementing exponential backoff or switching to a backup provider."
        )
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 4. Malformed request (400 / invalid_request_error / invalid_json)
    elif status == 400 or err_type in ("invalid_request_error", "validation_error", "invalid_json"):
        _msg = err.get("message", "")
        if err_type == "invalid_json":
            suggestion = "The request body must be valid JSON. Check for missing quotes, trailing commas, or unescaped characters."
        else:
            suggestion = "The request body is invalid."
            # Surface field name: try known field names first, then regex
            import re as _re

            _fld = None
            if "messages" in _msg.lower():
                _fld = "messages"
                suggestion += " The 'messages' field is required and must be a non-empty array."
            elif "model" in _msg.lower():
                _fld = "model"
                suggestion += " The 'model' field is required and must be a non-empty string."
            else:
                # Generic: try to extract a field name from "field 'xyz'" or "param xyz"
                _field_m = _re.search(
                    r"(?:field|param(?:eter)?)\s+['\"]?([a-zA-Z_]\w*)['\"]?", _msg, _re.I
                )
                if _field_m:
                    _fld = _field_m.group(1)
                    suggestion += f" Check the '{_fld}' field in your request."
            if _fld:
                err.setdefault("field", _fld)
        suggestion += " See: https://docs.anthropic.com/en/api/messages"
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)

    # 5. Provider unavailable (502 / 503)
    elif status in (502, 503) or err_type in (
        "provider_unavailable",
        "service_unavailable",
        "bad_gateway",
    ):
        suggestion = (
            "The upstream provider is temporarily unavailable. "
            "Retry after a short delay. If the issue persists, check the provider's status page "
            "or switch to an alternate provider."
        )
        err.setdefault("hint", suggestion)
        err.setdefault("suggestion", suggestion)
        if not err.get("type"):
            err["type"] = "provider_unavailable"

    normalized["error"] = err
    return normalized


def _rate_limit_check(client_ip: str) -> bool:
    """Return True if request is ALLOWED. False = throttle (429)."""
    if _RATE_LIMIT_RPM <= 0:
        return True  # disabled
    now = time.time()
    with _rate_bucket_lock:
        if client_ip not in _rate_buckets:
            _rate_buckets[client_ip] = {"tokens": float(_RATE_LIMIT_RPM), "last_refill": now}
        bucket = _rate_buckets[client_ip]
        elapsed = now - bucket["last_refill"]
        refill = elapsed * (_RATE_LIMIT_RPM / 60.0)
        bucket["tokens"] = min(float(_RATE_LIMIT_RPM), bucket["tokens"] + refill)
        bucket["last_refill"] = now
        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True
        return False


def _ollama_health_loop():
    """Background thread: ping ollama upstream every 30s.
    Pre-opens circuit if unreachable so requests fail instantly."""
    from urllib.parse import urlparse

    parsed = urlparse(OLLAMA_UPSTREAM)
    host = parsed.hostname
    port = parsed.port or 11434
    check_interval = 30  # seconds between checks

    # Initial check on startup
    time.sleep(0.5)  # let proxy finish starting

    while True:
        try:
            probe = socket.create_connection((host, port), timeout=5)
            probe.close()
            with _ollama_circuit_lock:
                was_open = _ollama_circuit["open"]
                _ollama_circuit["open"] = False
            if was_open:
                print(f"  \u2705 Ollama upstream {host}:{port} is back online")
        except (socket.timeout, OSError, ConnectionRefusedError):
            with _ollama_circuit_lock:
                was_open = _ollama_circuit["open"]
                _ollama_circuit["open"] = True
                _ollama_circuit["last_failure"] = time.time()
            if not was_open:
                print(f"  \u26a0\ufe0f Ollama upstream {host}:{port} unreachable — circuit opened")

        time.sleep(check_interval)


# Start health checker thread (skip in test mode to avoid pytest capture conflicts)
_ollama_health_thread = threading.Thread(target=_ollama_health_loop, daemon=True)
if not os.environ.get("TOKENPAK_NO_THREADS"):
    _ollama_health_thread.start()

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------
# LRU cache so repeated count_tokens calls on the same text (e.g. injection text
# counted before and after skeleton) are O(1) lookups instead of re-encoding.
_TOKEN_COUNT_CACHE: Dict[int, int] = {}  # hash(text) -> token_count
_TOKEN_COUNT_CACHE_MAX = 1024

# Token cache counters — must be defined before _token_count_cached calls them
_TOKEN_CACHE_HITS: int = 0
_TOKEN_CACHE_MISSES: int = 0

def _inc_token_cache_hit() -> None:
    global _TOKEN_CACHE_HITS
    _TOKEN_CACHE_HITS += 1

def _inc_token_cache_miss() -> None:
    global _TOKEN_CACHE_MISSES
    _TOKEN_CACHE_MISSES += 1

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
# Two-Tier Vault Index (Read-Only)
# ---------------------------------------------------------------------------
class VaultIndex:
    """
    Read-only BM25-searchable index loaded from .tokenpak/index.json + blocks/.
    Reloads periodically to pick up git-pulled changes.
    """

    def __init__(self, tokenpak_dir: str):
        self.tokenpak_dir = Path(tokenpak_dir)
        self.blocks: Dict[str, dict] = {}  # block_id -> {meta only, no content}
        self._last_loaded = 0
        self._last_mtime = 0
        self._lock = threading.Lock()
        # BM25 precomputed
        self._df: Dict[str, int] = {}
        self._block_tfs: Dict[str, Dict[str, int]] = {}
        self._block_dl: Dict[str, int] = {}  # precomputed doc lengths (sum of tf values)
        self._avg_dl: float = 0
        self._doc_count: int = 0
        self._inverted: Dict[str, set] = {}  # term -> set(block_ids)
        # Tiered memory — LRU content cache (Tier 2)
        self._content_cache: OrderedDict = OrderedDict()  # block_id -> content str
        self._cache_bytes: int = 0
        self._max_cache_bytes: int = VAULT_CACHE_MAX_BYTES
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._cache_evictions: int = 0

    @property
    def available(self) -> bool:
        return len(self.blocks) > 0

    def maybe_reload(self):
        """Reload if index file changed or enough time passed."""
        now = time.time()
        if now - self._last_loaded < VAULT_INDEX_RELOAD_INTERVAL:
            return

        index_path = self.tokenpak_dir / "index.json"
        if not index_path.exists():
            return

        try:
            mtime = index_path.stat().st_mtime
            if mtime == self._last_mtime and self.blocks:
                self._last_loaded = now
                return
        except OSError:
            return

        self._load(index_path, mtime)
        self._last_loaded = now

    def _load(self, index_path: Path, mtime: float):
        """Load index + block contents, precompute BM25 stats."""
        try:
            data = json.loads(index_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️ Vault index load error: {e}")
            return

        blocks_dir = self.tokenpak_dir / "blocks"
        new_blocks: Dict[str, dict] = {}

        raw_blocks = data.get("blocks", {})
        if isinstance(raw_blocks, dict):
            items = raw_blocks.items()
        else:
            return  # unexpected format

        # Collect mtime for preload scoring
        preload_candidates: list = []

        for bid, bdata in items:
            content_file = blocks_dir / f"{bid}.txt"
            if not content_file.exists():
                continue

            try:
                content = content_file.read_text(errors="replace")
            except OSError:
                continue

            new_blocks[bid] = {
                "block_id": bid,
                "source_path": bdata.get("source_path", bid),
                "risk_class": bdata.get("risk_class", "narrative"),
                "must_keep": bdata.get("must_keep", False),
                "raw_tokens": bdata.get("raw_tokens", 0),
                # NOTE: content NOT stored here — fetched on demand via _get_content()
                "_content_file": str(content_file),
            }

            # Collect for BM25 (content used here then discarded)
            preload_candidates.append((bid, content, content_file.stat().st_mtime if content_file.exists() else 0))

        # Precompute BM25 from content (content discarded after this pass)
        df: Dict[str, int] = {}
        block_tfs: Dict[str, Dict[str, int]] = {}
        block_dl: Dict[str, int] = {}  # precomputed doc lengths
        total_dl = 0

        for bid, content, _mtime in preload_candidates:
            terms = _bm25_tokenize(content)
            tf: Dict[str, int] = {}
            for t in terms:
                tf[t] = tf.get(t, 0) + 1
            dl = len(terms)
            block_tfs[bid] = tf
            block_dl[bid] = dl  # store precomputed length
            total_dl += dl
            for t in set(terms):
                df[t] = df.get(t, 0) + 1

        doc_count = len(new_blocks)
        avg_dl = total_dl / doc_count if doc_count > 0 else 0

        # Build inverted index: term -> set(block_ids)
        inverted: Dict[str, set] = {}
        for bid, tf in block_tfs.items():
            for term in tf:
                if term not in inverted:
                    inverted[term] = set()
                inverted[term].add(bid)

        # Build new LRU cache — preload top-N recently-modified blocks
        new_cache: OrderedDict = OrderedDict()
        new_cache_bytes = 0
        preload_n = VAULT_CACHE_PRELOAD
        if preload_n > 0:
            sorted_by_mtime = sorted(preload_candidates, key=lambda x: -x[2])[:preload_n]
            for bid, content, _mtime in sorted_by_mtime:
                content_size = len(content.encode("utf-8"))
                if new_cache_bytes + content_size <= self._max_cache_bytes:
                    new_cache[bid] = content
                    new_cache_bytes += content_size

        # Atomic swap — all heavy work done above, lock held briefly
        with self._lock:
            self.blocks = new_blocks
            self._df = df
            self._block_tfs = block_tfs
            self._block_dl = block_dl
            self._avg_dl = avg_dl
            self._doc_count = doc_count
            self._inverted = inverted
            self._last_mtime = mtime
            self._content_cache = new_cache
            self._cache_bytes = new_cache_bytes
            # Reset counters on reload
            self._cache_hits = 0
            self._cache_misses = 0
            self._cache_evictions = 0

        print(
            f"  📚 Vault index loaded: {doc_count} blocks, {sum(b['raw_tokens'] for b in new_blocks.values()):,} tokens"
            f" | cache preloaded: {len(new_cache)} blocks ({new_cache_bytes // 1024 // 1024}MB)"
        )

    def _enforce_cache_limit(self):
        """Evict LRU entries until cache is within byte limit. Must be called with lock held."""
        while self._cache_bytes > self._max_cache_bytes and self._content_cache:
            _bid, evicted = self._content_cache.popitem(last=False)
            self._cache_bytes -= len(evicted.encode("utf-8"))
            self._cache_evictions += 1

    def _get_content(self, block_id: str) -> str:
        """Fetch block content from LRU cache (Tier 2) or disk (Tier 3)."""
        with self._lock:
            if block_id in self._content_cache:
                # Cache hit — move to end (most recently used)
                content = self._content_cache.pop(block_id)
                self._content_cache[block_id] = content
                self._cache_hits += 1
                return content

            self._cache_misses += 1

        # Cache miss — read from disk (Tier 3), outside lock to avoid blocking search
        block = self.blocks.get(block_id)
        if not block:
            return ""
        content_file = Path(block.get("_content_file", ""))
        if not content_file.exists():
            return ""
        try:
            content = content_file.read_text(errors="replace")
        except OSError:
            return ""

        # Insert into cache
        content_size = len(content.encode("utf-8"))
        with self._lock:
            self._content_cache[block_id] = content
            self._cache_bytes += content_size
            self._enforce_cache_limit()

        return content

    @property
    def cache_stats(self) -> dict:
        """Return current cache statistics (thread-safe snapshot)."""
        with self._lock:
            return {
                "vault_cache_entries": len(self._content_cache),
                "vault_cache_memory_mb": round(self._cache_bytes / 1024 / 1024, 2),
                "vault_cache_hits": self._cache_hits,
                "vault_cache_misses": self._cache_misses,
                "vault_cache_evictions": self._cache_evictions,
                "vault_cache_hit_rate": round(
                    self._cache_hits / (self._cache_hits + self._cache_misses)
                    if (self._cache_hits + self._cache_misses) > 0
                    else 0.0,
                    3,
                ),
            }

    def search(
        self, query: str, top_k: int = 5, min_score: float = 2.0
    ) -> List[Tuple[dict, float]]:
        """BM25 search across vault blocks with query expansion.

        When query expansion is available, search terms are expanded with
        synonyms/aliases (weight 0.5) and stemmed forms (weight 0.8).
        Each expanded term's BM25 contribution is multiplied by its weight,
        improving recall on vocabulary-mismatch queries while preserving
        precision on exact matches.

        Returns [(block_dict, score), ...] sorted by score descending.
        """
        # Use weighted query expansion when available
        weighted_terms = _bm25_tokenize_query(query)
        query_terms = [t for t, _ in weighted_terms]
        term_weights = {t: w for t, w in weighted_terms}

        if not query_terms or not self.blocks:
            return []

        # Snapshot refs atomically under GIL — no lock held during scoring
        df = self._df
        block_tfs = self._block_tfs
        block_dl = self._block_dl  # precomputed doc lengths — avoids sum(tf.values()) per request
        avg_dl = self._avg_dl
        doc_count = self._doc_count
        blocks = self.blocks
        inverted = self._inverted

        k1 = 1.5
        b_param = 0.75
        scores: Dict[str, float] = {}

        # IDF-gated candidate expansion with MAX_CANDIDATES cap:
        # 1. Skip terms appearing in >40% of docs (too common to discriminate).
        # 2. Sort remaining terms by ascending frequency (most selective first).
        # 3. Add their posting lists until we hit MAX_CANDIDATES (prevents exploding to 6k+).
        # 4. Fall back to common terms only if no selective terms exist.
        # At cap=500, top-5 results are identical to full scan; scoring time drops from 67ms→8ms.
        _idf_gate = 0.40  # skip terms in >40% of corpus for candidate expansion
        _max_candidates = 500
        _selective: List[Tuple[str, int]] = []
        _fallback: List[str] = []
        for qt in query_terms:
            if qt not in inverted:
                continue
            term_freq = df.get(qt, 0)
            if doc_count > 0 and term_freq / doc_count > _idf_gate:
                _fallback.append(qt)
            else:
                _selective.append((qt, term_freq))
        _selective.sort(key=lambda x: x[1])  # most selective first

        candidates: set = set()
        if _selective:
            for qt, _ in _selective:
                candidates.update(inverted[qt])
                if len(candidates) >= _max_candidates:
                    break  # enough; less selective terms contribute diminishing returns
        if not candidates:
            # All terms are corpus-wide — fall back to common terms (fast path)
            for qt in _fallback:
                candidates.update(inverted[qt])
        if not candidates:
            return []

        for bid in candidates:
            tf = block_tfs.get(bid, {})
            dl = block_dl.get(bid, 0)  # O(1) lookup instead of O(terms) sum
            score = 0.0
            for qt in query_terms:
                if qt not in df:
                    continue
                idf = math.log((doc_count - df[qt] + 0.5) / (df[qt] + 0.5) + 1)
                term_freq = tf.get(qt, 0)
                if term_freq == 0:
                    continue
                numerator = term_freq * (k1 + 1)
                denominator = term_freq + k1 * (1 - b_param + b_param * dl / avg_dl)
                # Weight the BM25 contribution by term weight (1.0 for original,
                # 0.5 for aliases, 0.8 for stems) — expansion terms contribute
                # proportionally less than exact matches
                weight = term_weights.get(qt, 1.0)
                score += weight * idf * numerator / denominator
            if score >= min_score:
                scores[bid] = score

        # Sort deterministically: score desc, then path asc, then block_id asc
        # This ensures byte-identical ordering for cache stability even on score ties
        ranked = sorted(
            scores.items(),
            key=lambda x: (-x[1], blocks[x[0]].get("source_path", ""), x[0]),
        )[:top_k]
        return [(blocks[bid], score) for bid, score in ranked]

    def compile_injection(
        self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0
    ) -> Tuple[str, int, List[str]]:
        """
        Search vault and compile injection text within budget.
        Returns (injection_text, tokens_used, source_refs).
        """
        results = self.search(query, top_k=top_k, min_score=min_score)
        if not results:
            return "", 0, []

        injection_parts = []
        tokens_used = 0
        source_refs = []

        for block, score in results:
            # Fetch content from LRU cache (Tier 2) or disk (Tier 3) — never from block dict
            content = self._get_content(block["block_id"])
            block_tokens = block["raw_tokens"]

            # Budget check
            remaining = budget - tokens_used
            if remaining <= 100:
                break

            # Truncate if needed
            if block_tokens > remaining:
                # Rough char-to-token truncation
                char_limit = remaining * 4
                content = content[:char_limit].rsplit("\n", 1)[0]
                block_tokens = count_tokens(content)

            source_path = block["source_path"]
            injection_parts.append(f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}")
            tokens_used += block_tokens
            source_refs.append(source_path)

        if not injection_parts:
            return "", 0, []

        header = "\n\n## Retrieved Context\n"  # fixed header for cache stability
        injection_text = header + "\n\n".join(injection_parts)
        # Recount with header
        tokens_used = count_tokens(injection_text)

        return injection_text, tokens_used, source_refs


# BM25 tokenizer — lru_cache gives 50x speedup on repeated queries (search terms repeat often)
# Enhanced with query expansion (stop words, stemming, aliases) when available.
@lru_cache(maxsize=512)
def _bm25_tokenize(text: str) -> List[str]:
    """Tokenize text for BM25 indexing (includes stemmed forms, removes stop words)."""
    if _QUERY_EXPANSION_AVAILABLE:
        return list(_qe_tokenize(text, mode="index"))
    return re.findall(r"[a-z0-9_]+", text.lower())


def _bm25_tokenize_query(query: str) -> List[Tuple[str, float]]:
    """Tokenize a search query with expansion (aliases + stems + weights).

    Returns list of (term, weight) tuples. Original terms get weight 1.0,
    aliases get 0.5, stems get 0.8.
    """
    if _QUERY_EXPANSION_AVAILABLE:
        tokens = list(_qe_tokenize(query, mode="query"))
        return _qe_expand(tokens)
    # Fallback: all terms weighted equally at 1.0
    terms = re.findall(r"[a-z0-9_]+", query.lower())
    return [(t, 1.0) for t in terms]


# Global vault index instance — backend-aware
if RETRIEVAL_BACKEND == "sqlite":
    try:
        from tokenpak.agent.vault.sqlite_retrieval import SQLiteRetrievalBackend as _SQLiteBackend

        VAULT_INDEX = _SQLiteBackend(VAULT_INDEX_PATH)
        print(f"  📦 Vault retrieval backend: sqlite ({VAULT_INDEX_PATH})")
    except ImportError as _sqlite_err:
        print(
            f"  ⚠️  SQLite retrieval backend unavailable ({_sqlite_err}), falling back to json_blocks"
        )
        VAULT_INDEX = VaultIndex(VAULT_INDEX_PATH)
else:
    VAULT_INDEX = VaultIndex(VAULT_INDEX_PATH)
    print(f"  📦 Vault retrieval backend: json_blocks ({VAULT_INDEX_PATH})")

# Custom backend override (Replace mode): TOKENPAK_RETRIEVAL_BACKEND=custom:module.ClassName
if RETRIEVAL_BACKEND.startswith("custom:") and _BACKEND_PROTOCOL_AVAILABLE:
    try:
        VAULT_INDEX = _load_custom_backend(RETRIEVAL_BACKEND, VAULT_INDEX_PATH)
        print(f"  📦 Vault retrieval backend: custom ({RETRIEVAL_BACKEND})")
    except (ValueError, ImportError, AttributeError, TypeError) as _custom_err:
        print(f"  ⚠️  Custom backend failed ({_custom_err}), falling back to json_blocks")
        VAULT_INDEX = VaultIndex(VAULT_INDEX_PATH)

# HOTFIX 2026-03-27: Load vault index on startup (was previously only in bg timer)
VAULT_INDEX.maybe_reload()
print(f"  ✅ Vault index loaded: {len(VAULT_INDEX.blocks)} blocks")

# Semantic scorer (Augment mode): TOKENPAK_SEMANTIC_BACKEND=custom:module.ClassName
SEMANTIC_SCORER = None
if SEMANTIC_BACKEND and SEMANTIC_BACKEND.startswith("custom:") and _BACKEND_PROTOCOL_AVAILABLE:
    try:
        SEMANTIC_SCORER = _load_custom_scorer(SEMANTIC_BACKEND)
        print(f"  🧠 Semantic scorer loaded: {SEMANTIC_BACKEND}")
    except (ValueError, ImportError, AttributeError, TypeError) as _scorer_err:
        print(f"  ⚠️  Semantic scorer failed ({_scorer_err}), running without Augment mode")

# Log query expansion status
if _QUERY_EXPANSION_AVAILABLE:
    print("  🔍 Query expansion: enabled (stop words, stemming, aliases)")
else:
    print("  🔍 Query expansion: disabled (module not available)")


def _compile_from_results(
    results: List[Tuple[dict, float]], budget: int
) -> Tuple[str, int, List[str]]:
    """Build injection text from pre-scored results within a token budget.

    Used by Augment mode after multi-signal rescoring to format injection
    from score_and_sort() output without re-searching.
    """
    if not results:
        return "", 0, []

    injection_parts: List[str] = []
    tokens_used = 0
    source_refs: List[str] = []

    for block, score in results:
        content = VAULT_INDEX._get_content(block["block_id"])
        block_tokens = block.get("raw_tokens", 0) or count_tokens(content)

        remaining = budget - tokens_used
        if remaining <= 100:
            break

        if block_tokens > remaining:
            char_limit = remaining * 4
            content = content[:char_limit].rsplit("\n", 1)[0]
            block_tokens = count_tokens(content)
            if block_tokens > remaining:
                break

        source_path = block.get("source_path", block.get("block_id", "unknown"))
        injection_parts.append(f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}")
        tokens_used += block_tokens
        source_refs.append(source_path)

    if not injection_parts:
        return "", 0, []

    header = "\n\n## Retrieved Context\n"
    injection_text = header + "\n\n".join(injection_parts)
    tokens_used = count_tokens(injection_text)
    return injection_text, tokens_used, source_refs


def _vault_index_reload_timer() -> None:
    """Single background timer for periodic vault index reload — replaces per-request thread spawns."""
    VAULT_INDEX.maybe_reload()
    t = threading.Timer(VAULT_INDEX_RELOAD_INTERVAL, _vault_index_reload_timer)
    t.daemon = True
    t.start()


# Global term resolver instance
TERM_RESOLVER = None
if TERM_RESOLVER_AVAILABLE and TERM_RESOLVER_ENABLED:
    try:
        _config = TermResolverConfig(
            top_k=TERM_RESOLVER_TOP_K,
            max_bytes_per_card=TERM_RESOLVER_MAX_BYTES,
            enabled=True,
        )
        TERM_RESOLVER = TermResolver(config=_config)
        print(
            f"  🔤 Term resolver initialized (top_k={TERM_RESOLVER_TOP_K}, enabled={TERM_RESOLVER_ENABLED})"
        )
    except Exception as e:
        print(f"  ⚠️ Failed to initialize term resolver: {e}")

# Global capsule builder instance
try:
    from tokenpak.capsule.builder import CapsuleBuilder as _CapsuleBuilder

    CAPSULE_BUILDER = _CapsuleBuilder(
        enabled=ENABLE_CAPSULE_BUILDER,
        min_block_chars=CAPSULE_MIN_CHARS,
        hot_window=CAPSULE_HOT_WINDOW,
    )
    print(
        f"  💊 Capsule builder loaded (enabled={ENABLE_CAPSULE_BUILDER}, min_chars={CAPSULE_MIN_CHARS})"
    )
except ImportError as _cb_err:
    CAPSULE_BUILDER = None
    print(f"  ⚠️  Capsule builder unavailable: {_cb_err}")


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
        from tokenpak._internal.shadow_reader import ShadowReader

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
        from tokenpak._internal.budgeter import Budgeter

        b = Budgeter()
        return b.allocate(components, total_tokens=total)
    except Exception:
        return components  # fail-open


# ---------------------------------------------------------------------------
# Router wiring — DeterministicRouter integration (feature-flagged)
# ---------------------------------------------------------------------------
_ROUTER_INSTANCE = None
_ROUTER_LOCK = threading.Lock()


def _get_router():
    """Return the DeterministicRouter singleton, or None if unavailable/disabled."""
    global _ROUTER_INSTANCE
    if not ROUTER_ENABLED:
        return None
    with _ROUTER_LOCK:
        if _ROUTER_INSTANCE is None:
            try:
                sys.path.insert(
                    0,
                    str(Path.home() / "vault" / "01_PROJECTS" / "tokenpak" / "packages" / "pypi"),
                )
                from tokenpak.agent.compression.pipeline import CompressionPipeline
                from tokenpak.agent.compression.recipes import RecipeEngine
                from tokenpak.agent.compression.slot_filler import SlotFiller
                from tokenpak.agent.proxy.intent_policy import decide as _policy_decide

                try:
                    from tokenpak._internal.validation_gate import ValidationGate
                except ImportError:
                    ValidationGate = None  # type: ignore[assignment,misc]

                class _DeterministicRouter:
                    """Classifier-first router: intent → slots → deterministic recipe/action."""

                    def __init__(self):
                        self._pipeline = CompressionPipeline()
                        self._slot_filler = SlotFiller()
                        self._recipe_engine = RecipeEngine()
                        self._gate = (
                            ValidationGate(
                                enabled=VALIDATION_GATE_ENABLED,
                                token_budget_cap=VALIDATION_GATE_BUDGET_CAP,
                            )
                            if ValidationGate is not None
                            and _has_validation_gate()
                            and VALIDATION_GATE_ENABLED
                            else None
                        )

                    def route(self, user_text: str, session_id: str = "") -> "_RouterResult":
                        t0 = time.time()
                        try:
                            # Phase 0.5: Semantic metadata dict (populated by _classify_intent)
                            _sem_meta: dict = {}

                            # Phase 1: Classify intent (semantic resolver runs first internally)
                            intent = _classify_intent(user_text, _semantic_meta=_sem_meta)

                            # Phase 2: Fill slots for this intent
                            filled = self._slot_filler.fill(intent, user_text)

                            # Phase 3: Deterministic policy decision (intent + slots → recipe + action)
                            decision = _policy_decide(intent, filled.slots, filled.confidence)

                            # Phase 4: Compress via pipeline (skipped for low-cost intents)
                            compressed = user_text
                            if decision.action.compress:
                                msgs = [{"role": "user", "content": user_text}]
                                pipeline_result = self._pipeline.run(msgs)
                                if pipeline_result.messages:
                                    compressed = pipeline_result.messages[-1].get(
                                        "content", user_text
                                    )

                            elapsed = int((time.time() - t0) * 1000)
                            result = _RouterResult(
                                ok=True,
                                fallback=decision.fallback,
                                intent=decision.intent,
                                recipe_id=decision.recipe_id,
                                slots=decision.slots_used,
                                elapsed_ms=elapsed,
                                compressed_text=compressed,
                                capsule=None,
                                fallback_reason=decision.fallback_reason,
                            )
                            # Attach semantic resolution metadata for debug/tracing
                            result.semantic_meta = _sem_meta
                            return result
                        except Exception as e:
                            elapsed = int((time.time() - t0) * 1000)
                            return _RouterResult(
                                ok=False,
                                fallback=True,
                                intent="unknown",
                                recipe_id="pipeline-v1",
                                slots={},
                                elapsed_ms=elapsed,
                                compressed_text="",
                                capsule=None,
                                error=str(e),
                                fallback_reason=f"exception:{type(e).__name__}",
                            )

                _ROUTER_INSTANCE = _DeterministicRouter()
            except Exception as _router_init_err:
                print(f"  ⚠️ Router init failed: {_router_init_err}")
                return None
        return _ROUTER_INSTANCE


_VALIDATION_GATE_INSTANCE = None
_VALIDATION_GATE_LOCK = threading.Lock()


def _has_validation_gate() -> bool:
    try:
        from tokenpak._internal.validation_gate import ValidationGate  # noqa

        return True
    except Exception:
        return False


def _get_validation_gate():
    global _VALIDATION_GATE_INSTANCE
    if not VALIDATION_GATE_ENABLED:
        return None
    with _VALIDATION_GATE_LOCK:
        if _VALIDATION_GATE_INSTANCE is None:
            try:
                from tokenpak._internal.validation_gate import ValidationGate

                _VALIDATION_GATE_INSTANCE = ValidationGate(
                    enabled=True,
                    token_budget_cap=VALIDATION_GATE_BUDGET_CAP,
                )
            except Exception:
                return None
        return _VALIDATION_GATE_INSTANCE


class _RouterResult:
    """Lightweight result object from router.route()."""

    def __init__(
        self,
        ok,
        fallback,
        intent,
        recipe_id,
        slots,
        elapsed_ms,
        compressed_text="",
        capsule=None,
        error="",
        fallback_reason="",
    ):
        self.ok = ok
        self.fallback = fallback
        self.intent = intent
        self.recipe_id = recipe_id
        self.slots = slots
        self.elapsed_ms = elapsed_ms
        self.compressed_text = compressed_text
        self.capsule = capsule
        self.error = error
        self.fallback_reason = fallback_reason
        # Semantic resolution metadata (set by route() when SemanticResolver runs)
        # Keys: intent_alias, intent_canonical, match_type, entity_aliases, normalized
        self.semantic_meta: dict = {}


def _classify_intent(text: str, _semantic_meta: "dict | None" = None) -> str:
    """Keyword-based intent classification — canonical intent set.

    Phase 0: Semantic resolver preprocessing — maps alias variants to canonical
             intents deterministically before keyword matching (faster path +
             handles wording variants not in the keyword lists).
    Priority order matters: more specific checks run first.
    Returns one of: status, usage, execute, debug, summarize, plan,
                    explain, search, create, query (fallback).

    Args:
        text: Raw user input text.
        _semantic_meta: Optional dict populated with semantic resolution metadata
                        for router debug/tracing. Keys: intent_alias, intent_canonical,
                        entity_aliases, normalized.
    """
    # Phase 0: Semantic alias resolution (deterministic, no LLM)
    try:
        from tokenpak.semantic.resolver import get_default_resolver as _get_resolver

        _resolver = _get_resolver()
        _sem_result = _resolver.resolve_intent(text)
        if _sem_result is not None:
            # Populate metadata for caller inspection
            if _semantic_meta is not None:
                _semantic_meta["intent_alias"] = _sem_result.alias_matched
                _semantic_meta["intent_canonical"] = _sem_result.canonical
                _semantic_meta["match_type"] = _sem_result.match_type
            return _sem_result.canonical
    except Exception:
        pass  # Semantic layer is best-effort; fall through to keyword matching

    t = text.lower()
    # status — health/liveness checks (check before debug to avoid "error" overlap)
    if any(
        k in t
        for k in (
            "status",
            "health",
            "is it running",
            "is it up",
            "ping",
            "uptime",
            "alive",
            "reachable",
            "available",
        )
    ):
        return "status"
    # usage — cost/token analytics (check before search/query)
    if any(
        k in t
        for k in ("usage", "cost", "spend", "how much", "token count", "billing", "how many tokens")
    ):
        return "usage"
    # execute — imperative run/deploy/start commands
    if any(
        k in t
        for k in ("run ", "execute", "start ", "deploy", "launch", "trigger", "kick off", "fire")
    ):
        return "execute"
    # debug — error diagnosis
    if any(
        k in t
        for k in (
            "fix",
            "debug",
            "error",
            "bug",
            "broken",
            "failing",
            "exception",
            "traceback",
            "crash",
            "why is",
        )
    ):
        return "debug"
    # summarize — condensing content
    if any(
        k in t for k in ("summarize", "tldr", "brief", "recap", "summary", "condense", "digest")
    ):
        return "summarize"
    # plan — architecture / design / roadmap
    if any(
        k in t
        for k in (
            "plan",
            "design",
            "architect",
            "roadmap",
            "strategy",
            "approach",
            "what should i",
            "how should i",
        )
    ):
        return "plan"
    # explain — knowledge / conceptual questions
    if any(
        k in t
        for k in (
            "explain",
            "what is",
            "how does",
            "describe",
            "tell me about",
            "what does",
            "how do",
        )
    ):
        return "explain"
    # search — lookups and finding things
    if any(k in t for k in ("find", "search", "look up", "where", "locate", "which", "list all")):
        return "search"
    # create — code / artifact generation
    if any(
        k in t
        for k in ("write", "create", "generate", "build", "implement", "make a", "add a", "new ")
    ):
        return "create"
    # query — safe catch-all fallback
    return "query"


def _extract_user_text(body_bytes: bytes) -> str:
    """Extract the last user message text from a request body."""
    try:
        data = json.loads(body_bytes)
    except Exception:
        return ""
    messages = data.get("messages", [])
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return " ".join(parts)
    return ""


def _run_router(body_bytes: bytes, session_id: str = "") -> Tuple[bytes, Optional[dict]]:
    """
    Run the DeterministicRouter on the request body.
    Returns (possibly-modified body, meta dict or None).
    """
    user_text = _extract_user_text(body_bytes)
    if not user_text:
        return body_bytes, None

    router = _get_router()
    if router is None:
        return body_bytes, None

    try:
        result = router.route(user_text, session_id=session_id)
        meta: Dict[str, Any] = {
            "intent": result.intent,
            "recipe_used": result.recipe_id,
            "fallback": result.fallback,
            "total_ms": result.elapsed_ms,
        }
        # Surface slot extraction for debugging and downstream consumers
        if result.slots:
            meta["slots"] = result.slots
        if hasattr(result, "fallback_reason") and result.fallback_reason:
            meta["fallback_reason"] = result.fallback_reason
        if hasattr(result, "error") and result.error:
            meta["error"] = result.error
        return body_bytes, meta
    except Exception as e:
        return body_bytes, {
            "fallback": True,
            "error": str(e),
            "intent": "unknown",
            "recipe_used": "pipeline-v1",
            "total_ms": 0,
        }


def _router_health() -> dict:
    """Return router health/status dict for the /health endpoint."""
    components = {
        "slot_filler": False,
        "recipe_engine": False,
        "validation_gate": False,
    }
    if not ROUTER_ENABLED:
        return {"enabled": False, "components": components}

    router = _get_router()
    if router is None:
        return {"enabled": True, "components": components}

    return {
        "enabled": True,
        "components": {
            "slot_filler": hasattr(router, "_slot_filler") and router._slot_filler is not None,
            "recipe_engine": hasattr(router, "_recipe_engine")
            and router._recipe_engine is not None,
            "validation_gate": hasattr(router, "_gate") and router._gate is not None,
        },
    }


# ---------------------------------------------------------------------------
# Health endpoint response cache (1-second TTL to reduce per-request overhead)
# ---------------------------------------------------------------------------
import time as _time_module

_health_cache: dict = {"ts": 0.0, "data": None}
_HEALTH_CACHE_TTL = 1.0  # seconds

# ---------------------------------------------------------------------------
# Singleton for RouteEngine (PERF OPT #1 — avoid per-request construction + YAML I/O)
# RouteStore reads routes.yaml on every store.list() call — cache with mtime guard.
# ---------------------------------------------------------------------------
_ROUTE_ENGINE_INSTANCE = None
_ROUTE_ENGINE_LOCK = threading.Lock()
_ROUTE_RULES_CACHE: dict = {"rules": None, "mtime": 0.0, "ts": 0.0}
_ROUTE_RULES_CACHE_TTL = 5.0  # seconds — refresh rules at most every 5s


def _get_route_engine():
    """Return the RouteEngine singleton, creating it lazily."""
    global _ROUTE_ENGINE_INSTANCE
    if _ROUTE_ENGINE_INSTANCE is None:
        with _ROUTE_ENGINE_LOCK:
            if _ROUTE_ENGINE_INSTANCE is None:
                try:
                    from tokenpak.routing.rules import RouteEngine

                    _ROUTE_ENGINE_INSTANCE = RouteEngine()
                except Exception:
                    pass
    return _ROUTE_ENGINE_INSTANCE


def _get_cached_route_rules():
    """Return cached list of RouteRules, refreshing only when routes.yaml changes."""
    now = time.time()
    cache = _ROUTE_RULES_CACHE
    if cache["rules"] is not None and (now - cache["ts"]) < _ROUTE_RULES_CACHE_TTL:
        return cache["rules"]
    engine = _get_route_engine()
    if engine is None:
        return []
    try:
        routes_path = engine.store.path
        try:
            mtime = routes_path.stat().st_mtime if routes_path.exists() else 0.0
        except OSError:
            mtime = 0.0
        if cache["rules"] is not None and mtime == cache["mtime"]:
            cache["ts"] = now
            return cache["rules"]
        rules = engine.store.list()
        cache["rules"] = rules
        cache["mtime"] = mtime
        cache["ts"] = now
        return rules
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Singleton for PreconditionGates (PERF OPT #2 — avoid per-request import + init)
# ---------------------------------------------------------------------------
_PRECOND_GATES_INSTANCE = None
_PRECOND_GATES_LOCK = threading.Lock()


def _get_precond_gates():
    """Return the PreconditionGates singleton."""
    global _PRECOND_GATES_INSTANCE
    if _PRECOND_GATES_INSTANCE is None:
        with _PRECOND_GATES_LOCK:
            if _PRECOND_GATES_INSTANCE is None:
                try:
                    from tokenpak.agent.agentic.precondition_gates import PreconditionGates

                    _PRECOND_GATES_INSTANCE = PreconditionGates()
                except Exception:
                    pass
    return _PRECOND_GATES_INSTANCE


# ---------------------------------------------------------------------------
# Singleton for BudgetController (PERF OPT #3 — avoid per-request import + init)
# ---------------------------------------------------------------------------
_BUDGET_CTRL_INSTANCE = None
_BUDGET_CTRL_LOCK = threading.Lock()


def _get_budget_controller():
    """Return the BudgetController singleton."""
    global _BUDGET_CTRL_INSTANCE
    if _BUDGET_CTRL_INSTANCE is None:
        with _BUDGET_CTRL_LOCK:
            if _BUDGET_CTRL_INSTANCE is None:
                try:
                    from tokenpak._internal.budget_controller import BudgetController

                    _BUDGET_CTRL_INSTANCE = BudgetController()
                except Exception:
                    pass
    return _BUDGET_CTRL_INSTANCE


# ---------------------------------------------------------------------------
# Style Contract: Protected content detection
# ---------------------------------------------------------------------------
PROTECTED_MARKERS = [
    "SOUL.md",
    "AGENTS.md",
    "IDENTITY.md",
    "USER.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "MEMORY.md",
    "BOOTSTRAP.md",
    "You are",
    "Your role is",
    "## Core Truths",
    "## Boundaries",
    "## Response Mode",
    "## Safety",
    "## Vibe",
    '"type": "function"',
    '"parameters":',
    '"required":',
    "## Runtime",
    "## Workspace Files",
    "## Silent Replies",
    "## Heartbeats",
    "## Messaging",
]


def is_protected_content(text: str) -> bool:
    if not text or len(text) < 50:
        return False
    marker_hits = sum(1 for m in PROTECTED_MARKERS if m in text)
    return marker_hits >= 2


def classify_message_risk(msg: dict) -> str:
    role = msg.get("role", "")
    content = msg.get("content", "")

    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and "text" in p]
        content_text = "\n".join(text_parts)
    elif isinstance(content, str):
        content_text = content
    else:
        return "narrative"

    if role == "system":
        return "protected"
    if is_protected_content(content_text):
        return "protected"
    if role == "tool" or msg.get("type") == "tool_result":
        return "config"
    if "```" in content_text or content_text.count("    ") > 5:
        return "code"
    return "narrative"


def can_compress(risk_class: str, mode: str) -> bool:
    if mode in ("strict", "transparent"):
        return False
    if risk_class == "protected":
        return False
    if mode == "hybrid":
        return risk_class == "narrative"
    return True


# ---------------------------------------------------------------------------
# SQLite monitor
# ---------------------------------------------------------------------------
import sqlite3

_DB_CONNECTION = None
_DB_LOCK = threading.Lock()
_DB_WRITE_QUEUE = None
_DB_QUEUE_LOCK = threading.Lock()
_DB_QUEUE_MAX_SIZE = 1000
_DB_BACKGROUND_THREAD = None
_DB_BACKGROUND_STOP = threading.Event()

def _init_db_write_queue():
    """Initialize the database write queue and background thread."""
    global _DB_WRITE_QUEUE, _DB_BACKGROUND_THREAD
    with _DB_QUEUE_LOCK:
        if _DB_WRITE_QUEUE is None:
            _DB_WRITE_QUEUE = Queue(maxsize=_DB_QUEUE_MAX_SIZE)
            _DB_BACKGROUND_STOP.clear()
            _DB_BACKGROUND_THREAD = threading.Thread(
                target=_db_writer_worker,
                daemon=True,
                name="TokenPak-DB-Writer"
            )
            _DB_BACKGROUND_THREAD.start()

def _db_writer_worker():
    """Background worker thread that drains the DB write queue."""
    while not _DB_BACKGROUND_STOP.is_set():
        try:
            # Block for up to 1 second waiting for items
            work_item = _DB_WRITE_QUEUE.get(timeout=1.0)
            if work_item is None:  # Poison pill to stop
                break
            
            db_path, insert_params = work_item
            try:
                with _DB_LOCK:
                    conn = _get_db_connection(db_path)
                    # CACHE-P4-002: Extended schema with cache telemetry columns
                    conn.execute(
                        """INSERT INTO requests
                           (timestamp,model,request_type,input_tokens,output_tokens,estimated_cost,
                            latency_ms,status_code,endpoint,compilation_mode,protected_tokens,
                            compressed_tokens,injected_tokens,injected_sources,cache_read_tokens,cache_creation_tokens,
                            would_have_saved,cache_provider,cache_hit_inference,cache_estimated_savings,
                            session_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        insert_params,
                    )
                    conn.commit()
            except Exception as e:
                print(f"[TokenPak] DB write error: {e}", file=sys.stderr)
            finally:
                _DB_WRITE_QUEUE.task_done()
        except Empty:
            continue
        except Exception as e:
            print(f"[TokenPak] DB worker error: {e}", file=sys.stderr)

def _get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get or create persistent SQLite connection with WAL mode enabled."""
    global _DB_CONNECTION
    if _DB_CONNECTION is None:
        _DB_CONNECTION = sqlite3.connect(
            db_path,
            check_same_thread=False,  # Required for ThreadedHTTPServer
        )
        _DB_CONNECTION.execute("PRAGMA journal_mode=WAL")
        _DB_CONNECTION.execute("PRAGMA synchronous=NORMAL")
        _DB_CONNECTION.execute("PRAGMA busy_timeout=5000")
    return _DB_CONNECTION


def _prune_mutation_audit(conn: sqlite3.Connection, ttl_days: int) -> int:
    """Delete mutation_audit rows older than ttl_days. Returns number of rows deleted.

    CCG-02: No existing housekeeping cron path was found in proxy.py. CCG-06 should
    call this function from its request-handling path or wire it into the DB worker
    loop (_db_write_worker) on a periodic basis (e.g. once per N requests or on
    Monitor startup alongside _init_db).
    """
    cur = conn.execute(
        "DELETE FROM mutation_audit WHERE timestamp < datetime('now', '-' || ? || ' days')",
        (ttl_days,),
    )
    conn.commit()
    return cur.rowcount


def _write_mutation_audit(
    db_path: str,
    request_id,
    session_id: str,
    body_pre: bytes,
    body_post: bytes,
    rules_applied: list,
    cache_risk: str,
    mode: str,
) -> None:
    """CCG-06: Write one mutation_audit row per request.

    Hashes body_pre and body_post with SHA-256. In transparent mode,
    rules_applied must be [] and pre_hash must equal post_hash — the harness
    asserts this contract.  Writes are synchronous (direct sqlite3, no queue)
    so the row is durable even if the background DB worker is busy.
    """
    import hashlib as _hashlib
    import json as _json

    pre_hash = _hashlib.sha256(body_pre).hexdigest()
    post_hash = _hashlib.sha256(body_post).hexdigest()
    rollback_possible = 1  # always 1 in v1; may diverge in Phase 2
    try:
        _conn = sqlite3.connect(str(db_path))
        _conn.execute(
            "INSERT INTO mutation_audit "
            "(request_id, session_id, timestamp, pre_hash, post_hash, "
            "rules_applied, cache_risk, rollback_possible, mode) "
            "VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                session_id,
                pre_hash,
                post_hash,
                _json.dumps(rules_applied),
                cache_risk,
                rollback_possible,
                mode,
            ),
        )
        _conn.commit()
        _conn.close()
    except Exception:
        pass  # fail-open: never break a request over audit write


class Monitor:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        # Start background worker on first Monitor creation
        try:
            _init_db_write_queue()
        except NameError:
            pass

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                request_type TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                estimated_cost REAL,
                latency_ms INTEGER,
                status_code INTEGER,
                endpoint TEXT,
                compilation_mode TEXT,
                protected_tokens INTEGER,
                compressed_tokens INTEGER,
                injected_tokens INTEGER DEFAULT 0,
                injected_sources TEXT DEFAULT '',
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                would_have_saved INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON requests(timestamp)")
        # Add columns if upgrading from v3
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN injected_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN injected_sources TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_read_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN would_have_saved INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # CACHE-P4-002: Add cache telemetry columns
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_provider TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_hit_inference INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_estimated_savings REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass
        # CCG-02: Add session_id column for Claude Code session tracking
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN session_id TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_id)")
        conn.commit()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_id TEXT DEFAULT "",
                period TEXT DEFAULT "daily",
                budget_usd REAL,
                spent_usd REAL,
                pct_used REAL,
                triggered INTEGER DEFAULT 1
            )
        """)
        conn.commit()

        # CCG-02: mutation_audit table — per-request mutation telemetry
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mutation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                session_id TEXT,
                timestamp TEXT NOT NULL,
                pre_hash TEXT,
                post_hash TEXT,
                rules_applied TEXT,
                cache_risk TEXT,
                rollback_possible INTEGER,
                mode TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mutation_audit_session ON mutation_audit(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mutation_audit_ts ON mutation_audit(timestamp)")
        conn.commit()

        # Run migrations to bring DB schema up to current version
        try:
            if MIGRATION_AVAILABLE:
                try:
                    db_migrate(conn)
                    version = get_current_schema_version(conn)
                    print(f"✅ DB schema version: {version}")
                except Exception as e:
                    print(f"⚠️  Migration error (non-fatal): {e}")
        except NameError:
            pass

        conn.close()
        global _DB_CONNECTION
        _DB_CONNECTION = None  # reset so next call reopens fresh

    def log(
        self,
        model,
        input_tokens,
        output_tokens,
        cost,
        latency_ms,
        status_code,
        endpoint,
        compilation_mode="",
        protected_tokens=0,
        compressed_tokens=0,
        injected_tokens=0,
        injected_sources="",
        cache_read_tokens=0,
        cache_creation_tokens=0,
        would_have_saved=0,
        cache_provider="",
        cache_estimated_savings=0.0,
        session_id="",
    ):
        # CACHE-P4-002: Infer cache hit from cache_read_tokens
        cache_hit_inference = 1 if cache_read_tokens > 0 else 0
        
        # Enqueue write instead of writing directly (async, <0.1ms return)
        insert_params = (
            datetime.now().isoformat(),
            model,
            "chat",
            input_tokens,
            output_tokens,
            cost,
            latency_ms,
            status_code,
            endpoint,
            compilation_mode,
            protected_tokens,
            compressed_tokens,
            injected_tokens,
            injected_sources,
            cache_read_tokens,
            cache_creation_tokens,
            would_have_saved,
            cache_provider,
            cache_hit_inference,
            cache_estimated_savings,
            session_id,
        )
        _queued = False
        try:
            _DB_WRITE_QUEUE.put_nowait((self.db_path, insert_params))
            _queued = True
        except (NameError, Exception):
            _conn = sqlite3.connect(str(self.db_path))
            _conn.execute(
                "INSERT INTO requests (timestamp, model, request_type, input_tokens, output_tokens, "
                "estimated_cost, latency_ms, status_code, endpoint, compilation_mode, protected_tokens, "
                "compressed_tokens, injected_tokens, injected_sources, cache_read_tokens, cache_creation_tokens, "
                "would_have_saved, cache_provider, cache_hit_inference, cache_estimated_savings, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                insert_params
            )
            _conn.commit()
            _conn.close()
        try:
            # When queued async, cost not yet in DB — pass it as current_cost.
            # When written synchronously (fallback), cost already in DB — pass 0.
            self._check_budget_alert(current_cost=cost if (_queued and cost) else 0)
        except Exception:
            pass

    def get_stats(self, hours=24):
        conn = _get_db_connection(self.db_path)
        row = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
                   COALESCE(SUM(estimated_cost),0), COALESCE(AVG(latency_ms),0),
                   COALESCE(SUM(protected_tokens),0), COALESCE(SUM(compressed_tokens),0),
                   COALESCE(SUM(injected_tokens),0),
                   COALESCE(SUM(cache_read_tokens),0),
                   COALESCE(SUM(cache_creation_tokens),0)
            FROM requests WHERE timestamp >= datetime('now', ?)
        """,
            (f"-{hours} hours",),
        ).fetchone()
        return {
            "requests": row[0],
            "input_tokens": row[1],
            "output_tokens": row[2],
            "total_cost": round(row[3], 4),
            "avg_latency_ms": round(row[4], 0),
            "protected_tokens": row[5],
            "compressed_tokens": row[6],
            "injected_tokens": row[7],
            "cache_read_tokens": row[8],
            "cache_creation_tokens": row[9],
        }

    def get_by_model(self):
        conn = _get_db_connection(self.db_path)
        rows = conn.execute("""
            SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(estimated_cost),
                   SUM(cache_read_tokens), SUM(cache_creation_tokens), COALESCE(SUM(compressed_tokens),0)
            FROM requests GROUP BY model ORDER BY SUM(estimated_cost) DESC
        """).fetchall()
        result = {}
        for r in rows:
            input_tokens = r[2] or 0
            compressed_tokens = r[7] or 0
            compression_ratio = round(compressed_tokens / input_tokens, 4) if input_tokens > 0 else 0.0
            result[r[0]] = {
                "requests": r[1],
                "input_tokens": input_tokens,
                "output_tokens": r[3],
                "cost": round(r[4], 4),
                "cache_read_tokens": r[5] or 0,
                "cache_creation_tokens": r[6] or 0,
                "compressed_tokens": compressed_tokens,
                "compression_ratio": compression_ratio,
            }
        return result

    def _check_budget_alert(self, current_cost=0, _daily_limit=None, _threshold_pct=None):
        try:
            daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        except NameError:
            daily_limit = 0.0
        try:
            threshold_pct = _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
        except NameError:
            threshold_pct = 80.0
        if daily_limit <= 0:
            return
        conn = sqlite3.connect(str(self.db_path))
        try:
            spent = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) FROM requests WHERE date(timestamp) = date(\"now\")"
            ).fetchone()[0] or 0.0
            total_spent = float(spent) + float(current_cost)
            if total_spent >= daily_limit * threshold_pct / 100:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM budget_alerts WHERE date(timestamp) = date(\"now\") AND period=\"daily\""
                ).fetchone()[0]
                if existing == 0:
                    import datetime as _dt
                    conn.execute(
                        "INSERT INTO budget_alerts (timestamp, period, budget_usd, spent_usd, pct_used, triggered) VALUES (?, ?, ?, ?, ?, ?)",
                        (_dt.datetime.now().isoformat(), "daily", daily_limit, total_spent, round(total_spent / daily_limit * 100, 2), 1)
                    )
                    conn.commit()
        finally:
            conn.close()

    def get_budget_alert_status(self, _daily_limit=None, _threshold_pct=None):
        try:
            daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        except NameError:
            daily_limit = 0.0
        try:
            threshold_pct = _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
        except NameError:
            threshold_pct = 80.0
        conn = sqlite3.connect(str(self.db_path))
        try:
            spent = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) FROM requests WHERE date(timestamp) = date(\"now\")"
            ).fetchone()[0] or 0.0
            spent = float(spent)
            pct_used = round(spent / daily_limit * 100, 2) if daily_limit > 0 else 0.0
            remaining = max(0.0, daily_limit - spent)
            alert_triggered = (pct_used >= threshold_pct) if daily_limit > 0 else False
            last_row = conn.execute(
                "SELECT timestamp FROM budget_alerts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_alert_at = last_row[0] if last_row else None
        finally:
            conn.close()
        return {
            "spent_usd": round(spent, 4),
            "budget_usd": daily_limit,
            "pct_used": pct_used,
            "remaining_usd": round(remaining, 4),
            "alert_triggered": alert_triggered,
            "last_alert_at": last_alert_at,
        }

    def get_savings_report(self, since=None):
        conn = sqlite3.connect(str(self.db_path))
        try:
            where = ""
            params = []
            if since:
                where = "WHERE date(timestamp) >= ?"
                params = [since]
            row = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(compressed_tokens),0), COALESCE(SUM(cache_read_tokens),0) FROM requests {where}",
                params
            ).fetchone()
            total_requests = row[0] or 0
            total_compressed = row[1] or 0
            total_cache_read = row[2] or 0
            total_tokens_saved = int(total_compressed + total_cache_read)
            total_cost_saved = round(total_compressed * 3.00 / 1_000_000 + total_cache_read * 2.70 / 1_000_000, 4)

            # by model
            model_rows = conn.execute(
                f"SELECT model, COUNT(*), COALESCE(SUM(compressed_tokens),0), COALESCE(SUM(cache_read_tokens),0) FROM requests {where} GROUP BY model",
                params
            ).fetchall()
            savings_by_model = {}
            for r in model_rows:
                comp = r[2] or 0
                cr = r[3] or 0
                savings_by_model[r[0]] = {
                    "requests": r[1],
                    "tokens_saved": int(comp + cr),
                    "cost_saved_usd": round(comp * 3.00 / 1_000_000 + cr * 2.70 / 1_000_000, 4),
                }

            # by date (last 7 days)
            date_where = "WHERE date(timestamp) >= date(\"now\", \"-7 days\")"
            date_params = []
            if since:
                date_where = "WHERE date(timestamp) >= ? AND date(timestamp) >= date(\"now\", \"-7 days\")"
                date_params = [since]
            date_rows = conn.execute(
                f"SELECT date(timestamp), COALESCE(SUM(compressed_tokens),0), COALESCE(SUM(cache_read_tokens),0) FROM requests {date_where} GROUP BY date(timestamp) ORDER BY date(timestamp)",
                date_params
            ).fetchall()
            savings_by_date_7d = []
            for r in date_rows:
                comp = r[1] or 0
                cr = r[2] or 0
                savings_by_date_7d.append({
                    "date": r[0],
                    "tokens_saved": int(comp + cr),
                    "cost_saved_usd": round(comp * 3.00 / 1_000_000 + cr * 2.70 / 1_000_000, 4),
                })
        finally:
            conn.close()
        return {
            "total_requests": total_requests,
            "total_tokens_saved": total_tokens_saved,
            "total_cost_saved_usd": total_cost_saved,
            "savings_by_model": savings_by_model,
            "savings_by_date_7d": savings_by_date_7d,
        }

    def recent(self, limit=20):
        conn = _get_db_connection(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


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
    # Per-provider cache telemetry (CACHE-P4-002)
    "cache_by_provider": {},  # provider_name -> {hits, misses, read_tokens, creation_tokens, savings_usd}
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


# ---------------------------------------------------------------------------
# Cache Cost Multipliers — per-provider cache read/creation pricing
# Source: Provider pricing docs (see CACHE-P4-002 task)
# read = fraction of input cost for cached tokens
# creation = multiplier on input cost for cache write (Anthropic only has surcharge)
# ---------------------------------------------------------------------------
CACHE_COST_MULTIPLIERS: Dict[Provider, Dict[str, float]] = {
    Provider.ANTHROPIC: {"read": 0.10, "creation": 1.25},  # reads=10%, creation=125%
    Provider.OPENAI: {"read": 0.50, "creation": 1.0},       # reads=50%, no creation surcharge
    Provider.AZURE_OPENAI: {"read": 0.50, "creation": 1.0},
    Provider.XAI: {"read": 0.50, "creation": 1.0},
    Provider.GROQ: {"read": 0.0, "creation": 1.0},          # Free (volatile cache)
    Provider.FIREWORKS: {"read": 0.0, "creation": 1.0},     # No cache pricing surcharge
    Provider.TOGETHER: {"read": 0.0, "creation": 1.0},      # No cache pricing surcharge
    Provider.GEMINI: {"read": 0.25, "creation": 1.0},       # 25% of input cost
    Provider.BEDROCK: {"read": 0.10, "creation": 1.0},      # 10% of input cost
    Provider.CODEX: {"read": 0.50, "creation": 1.0},        # Follows OpenAI pricing
    Provider.UNKNOWN: {"read": 0.10, "creation": 1.25},     # Conservative default
}


def estimate_cache_savings(
    provider: Provider, cache_read_tokens: int, model: str = ""
) -> float:
    """Estimate USD saved from cache hits for a given provider.
    
    Formula: cache_read_tokens * input_cost * (1.0 - read_multiplier)
    Example: 1000 Anthropic cache reads at $3/MTok input → 1000 * 0.000003 * 0.90 = $0.0027 saved
    """
    if cache_read_tokens <= 0:
        return 0.0
    
    # Get input cost per token
    input_cost_per_mtok = 3.0  # default
    for key, costs in MODEL_COSTS.items():
        if key in model.lower():
            input_cost_per_mtok = costs["input"]
            break
    input_cost_per_tok = input_cost_per_mtok / 1_000_000
    
    # Get cache read multiplier
    multipliers = CACHE_COST_MULTIPLIERS.get(provider, CACHE_COST_MULTIPLIERS[Provider.UNKNOWN])
    read_mult = multipliers["read"]
    
    # Savings = tokens * cost * (1 - discount)
    return cache_read_tokens * input_cost_per_tok * (1.0 - read_mult)


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
    # Augment mode: if a semantic scorer is configured, fuse BM25 + semantic scores
    if SEMANTIC_SCORER is not None:
        try:
            bm25_results = VAULT_INDEX.search(
                query, top_k=INJECT_TOP_K * 2, min_score=INJECT_MIN_SCORE
            )
            if bm25_results:
                block_ids = [b["block_id"] for b, _ in bm25_results]
                semantic_scores = SEMANTIC_SCORER.score(query, block_ids)
                # Import score_and_sort for multi-signal fusion
                from tokenpak.agent.vault.search import score_and_sort
                rescored = score_and_sort(
                    bm25_results, query=query, semantic_scores=semantic_scores
                )[:INJECT_TOP_K]
                # Build injection from rescored results
                injection_text, tokens_used, source_refs = _compile_from_results(
                    rescored, remaining_budget
                )
            else:
                injection_text, tokens_used, source_refs = "", 0, []
        except Exception as _sem_err:
            logging.warning("Semantic scorer failed, falling back to BM25: %s", _sem_err)
            injection_text, tokens_used, source_refs = VAULT_INDEX.compile_injection(
                query, budget=remaining_budget, top_k=INJECT_TOP_K, min_score=INJECT_MIN_SCORE
            )
    else:
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


_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
_HEARTBEAT_COUNTER = re.compile(r"Heartbeat\s*#?\s*\d+", re.IGNORECASE)


def _strip_cache_poisons(body_bytes: bytes) -> bytes:
    """
    Strip dynamic content that breaks prompt cache hits:
    - ISO timestamps embedded in prompts (e.g. "Current time: 2026-03-09T17:00:00Z")
    - UUIDs embedded in prompts (e.g. "request_id: a1b2c3d4-...")
    - Heartbeat counters (e.g. "Heartbeat #1287")
    Only strips from message content strings, not from metadata fields.
    Fails open — returns original body if any error occurs.
    """
    try:
        data = json.loads(body_bytes)
        changed = False

        def _scrub(text: str) -> str:
            nonlocal changed
            original = text
            text = _UUID_PATTERN.sub("[id]", text)
            text = _TIMESTAMP_PATTERN.sub("[time]", text)
            text = _HEARTBEAT_COUNTER.sub("Heartbeat", text)
            if text != original:
                changed = True
            return text

        def _scrub_content(content):
            if isinstance(content, str):
                return _scrub(content)
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part["text"] = _scrub(part["text"])
            return content

        # Scrub message content
        for msg in data.get("messages", []):
            if isinstance(msg, dict):
                msg["content"] = _scrub_content(msg.get("content", ""))

        # Scrub system prompt (only text parts, not cache_control blocks)
        system = data.get("system")
        if isinstance(system, str):
            data["system"] = _scrub(system)
        elif isinstance(system, list):
            for part in system:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = _scrub(part["text"])

        if changed:
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
        return body_bytes
    except Exception:
        return body_bytes  # fail-open


def _classify_cache_miss_reason(
    raw_body: Optional[bytes],
    cache_poison_scrubbed: bool,
    tools_schema_changed: bool,
    final_body: Optional[bytes],
) -> str:
    """Best-effort classifier for cache misses."""
    if tools_schema_changed:
        return "schema_tool_change"

    raw_text = ""
    if raw_body:
        try:
            raw_text = raw_body.decode("utf-8", errors="ignore")
        except Exception:
            raw_text = ""

    if cache_poison_scrubbed:
        if _TIMESTAMP_PATTERN.search(raw_text):
            return "timestamp_poison"
        if _UUID_PATTERN.search(raw_text) or re.search(
            r"\brequest[_-]?id\b", raw_text, re.IGNORECASE
        ):
            return "uuid_request_id_poison"
        return "timestamp_poison"

    if raw_body and final_body and raw_body != final_body:
        return "retrieval_order_drift_or_unknown"

    return "retrieval_order_drift_or_unknown"


def _get_cache_stats_by_window(hours: int = 24) -> Dict[str, Any]:
    """Query DB for cache stats within a time window.
    
    Returns per-provider stats and overall totals for the given time window.
    """
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        
        conn = sqlite3.connect(str(MONITOR.db_path))
        cur = conn.cursor()
        
        # Get overall stats
        cur.execute("""
            SELECT 
                COUNT(*) as total_requests,
                SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) as cache_hits,
                COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation,
                COALESCE(SUM(CASE WHEN cache_provider IS NOT NULL THEN cache_estimated_savings ELSE 0 END), 0) as total_savings
            FROM requests 
            WHERE timestamp >= ?
        """, (cutoff,))
        overall = cur.fetchone()
        
        # Get per-provider stats
        cur.execute("""
            SELECT 
                cache_provider,
                COUNT(*) as requests,
                SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) as hits,
                COALESCE(SUM(cache_read_tokens), 0) as read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) as creation_tokens,
                COALESCE(SUM(cache_estimated_savings), 0) as savings
            FROM requests 
            WHERE timestamp >= ? AND cache_provider IS NOT NULL AND cache_provider != ''
            GROUP BY cache_provider
        """, (cutoff,))
        per_provider = cur.fetchall()
        
        conn.close()
        
        total_requests = overall[0] or 0
        cache_hits = overall[1] or 0
        hit_rate = (cache_hits / total_requests) if total_requests > 0 else 0.0
        
        provider_stats = {}
        for row in per_provider:
            provider_name = row[0] or "unknown"
            provider_requests = row[1] or 0
            provider_hits = row[2] or 0
            provider_stats[provider_name] = {
                "requests": provider_requests,
                "cache_hits": provider_hits,
                "hit_rate": round((provider_hits / provider_requests) if provider_requests > 0 else 0.0, 4),
                "cache_read_tokens": row[3] or 0,
                "cache_creation_tokens": row[4] or 0,
                "estimated_savings_usd": round(row[5] or 0.0, 6),
            }
        
        return {
            "total_requests": total_requests,
            "cache_hits": cache_hits,
            "hit_rate": round(hit_rate, 4),
            "cache_read_tokens": overall[2] or 0,
            "cache_creation_tokens": overall[3] or 0,
            "estimated_savings_usd": round(overall[4] or 0.0, 6),
            "per_provider": provider_stats,
        }
    except Exception as e:
        # Fail gracefully — return session stats if DB query fails
        return {
            "total_requests": 0,
            "cache_hits": 0,
            "hit_rate": 0.0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "estimated_savings_usd": 0.0,
            "per_provider": {},
            "error": str(e),
        }


def _build_cache_stats_payload() -> Dict[str, Any]:
    """Build comprehensive cache stats including per-provider breakdowns.
    
    Returns:
        - Session-level stats (since proxy start)
        - Per-provider session stats
        - DB-backed time-windowed stats (1h, 24h, 7d)
    """
    global _TOKEN_CACHE_HITS, _TOKEN_CACHE_MISSES
    
    # Sync module counters to SESSION
    SESSION["token_cache_hits"] = _TOKEN_CACHE_HITS
    SESSION["token_cache_misses"] = _TOKEN_CACHE_MISSES
    
    hits = int(SESSION.get("cache_hits", 0) or 0)
    misses = int(SESSION.get("cache_misses", 0) or 0)
    total = hits + misses
    hit_rate = (hits / total) if total > 0 else 0.0
    miss_reasons = dict(SESSION.get("cache_miss_reasons", {}))
    
    # Session per-provider stats
    session_by_provider = {}
    cache_by_provider = SESSION.get("cache_by_provider", {})
    for provider_name, stats in cache_by_provider.items():
        provider_hits = stats.get("hits", 0)
        provider_total = provider_hits + stats.get("misses", 0)
        session_by_provider[provider_name] = {
            "cache_hits": provider_hits,
            "cache_misses": stats.get("misses", 0),
            "hit_rate": round((provider_hits / provider_total) if provider_total > 0 else 0.0, 4),
            "cache_read_tokens": stats.get("read_tokens", 0),
            "cache_creation_tokens": stats.get("creation_tokens", 0),
            "estimated_savings_usd": round(stats.get("savings_usd", 0.0), 6),
        }
    
    # Time-windowed stats from DB
    stats_1h = _get_cache_stats_by_window(hours=1)
    stats_24h = _get_cache_stats_by_window(hours=24)
    stats_7d = _get_cache_stats_by_window(hours=168)
    
    return {
        # Session stats (backward compatible)
        "hit_rate": round(hit_rate, 4),
        "cache_read_tokens": int(SESSION.get("cache_read_tokens", 0) or 0),
        "cache_creation_tokens": int(SESSION.get("cache_creation_tokens", 0) or 0),
        "cache_hits": hits,
        "cache_misses": misses,
        "total_cache_decisions": total,
        "miss_reasons": miss_reasons,
        "token_cache_hits": SESSION["token_cache_hits"],
        "token_cache_misses": SESSION["token_cache_misses"],
        
        # Per-provider session stats
        "session_by_provider": session_by_provider,
        
        # Time-windowed stats
        "last_1h": stats_1h,
        "last_24h": stats_24h,
        "last_7d": stats_7d,
        
        # Active providers (for quick reference)
        "active_providers": list(cache_by_provider.keys()) if cache_by_provider else [],
        
        # Timestamp
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
# Gemini cachedContent Support (CACHE-P3-002)
# ---------------------------------------------------------------------------
# GEMINI CACHED CONTENT LIFECYCLE:
# 1. CREATE: Client calls Gemini's cachedContents.create() directly
#    - Provides system instructions, tools, and/or content to cache
#    - Gets back a resource name (e.g., "cachedContents/abc123")
#    - Cache has a TTL (default 1 hour, can be extended up to 7 days)
# 2. USE: Client passes resource name to TokenPak
#    - Via header: x-tokenpak-cache-ref
#    - Via body: tokenpak_cache_object_ref
#    - TokenPak injects as cachedContent field in generateContent request
# 3. RESPONSE: Gemini returns cachedContentTokenCount in usageMetadata
#    - TokenPak maps this to cache_read_tokens in DB
# 4. MANAGE: Client handles TTL extension, deletion, listing directly with Gemini
#    - TokenPak does not manage cache object lifecycle


def _inject_gemini_cache_ref(provider: Provider, headers: dict, body: bytes) -> bytes:
    """Inject cachedContent reference for Gemini requests.
    
    Accepts cache ref from:
    - Header: x-tokenpak-cache-ref
    - Body field: tokenpak_cache_object_ref (stripped before forwarding)
    
    Header takes precedence over body field.
    Only injects for Provider.GEMINI; returns body unchanged for other providers.
    
    Args:
        provider: The detected LLM provider
        headers: Request headers dict (case-sensitive keys)
        body: Request body bytes
        
    Returns:
        Modified body bytes with cachedContent injected (if applicable)
    """
    if provider != Provider.GEMINI:
        return body
    
    if not body:
        return body
    
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body
    
    # Get cache ref: header takes precedence over body field
    cache_ref = headers.get("x-tokenpak-cache-ref") or headers.get("X-TokenPak-Cache-Ref")
    body_ref = None
    
    if "tokenpak_cache_object_ref" in data:
        body_ref = data.pop("tokenpak_cache_object_ref")  # Strip from forwarded body
    
    final_ref = cache_ref or body_ref
    
    if not final_ref:
        # No cache ref provided — return body (possibly modified to strip field)
        if body_ref is not None:
            return json.dumps(data).encode()
        return body
    
    # Inject as Gemini's cachedContent field
    data["cachedContent"] = final_ref
    
    return json.dumps(data).encode()


def _parse_gemini_cached_tokens(response_data: dict) -> int:
    """Parse cachedContentTokenCount from Gemini responses.
    
    Gemini returns cache usage in usageMetadata:
    {
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 50,
            "cachedContentTokenCount": 800  // tokens served from cache
        }
    }
    
    Args:
        response_data: Parsed JSON response from Gemini
        
    Returns:
        Number of tokens served from cache (0 if not present)
    """
    usage = response_data.get("usageMetadata", {})
    if not isinstance(usage, dict):
        return 0
    return usage.get("cachedContentTokenCount", 0)


# ---------------------------------------------------------------------------
# Bedrock Cache Checkpoint Support (CACHE-P3-003)
# ---------------------------------------------------------------------------
# BEDROCK CACHE CHECKPOINT LIFECYCLE:
# 1. CLIENT specifies checkpoint positions via tokenpak_checkpoints field
# 2. PROXY inserts cachePoint blocks at those positions for Bedrock requests
# 3. RESPONSE includes CacheReadInputTokens/CacheWriteInputTokens in usage
# 4. Checkpoints mark boundaries — everything before a checkpoint is cached
#
# Key constraints:
# - Max 4 checkpoints per request (Bedrock limit)
# - Minimum tokens per checkpoint varies by model (1024-4096)
# - TTL: 5 minutes default, 1 hour optional for some models
# - Checkpoint indices are 0-based, insertion happens AFTER the index


def _extract_bedrock_checkpoints(body: dict) -> list:
    """Extract and validate checkpoint positions from TokenPak hints.
    
    Accepts tokenpak_checkpoints field containing array of insertion indices.
    Indices are 0-based and refer to positions in the messages array.
    A checkpoint at index N means: insert cachePoint AFTER message[N].
    
    Args:
        body: Request body dict (will be modified to strip tokenpak_checkpoints)
        
    Returns:
        List of valid checkpoint indices, sorted in reverse order (for safe insertion)
    """
    checkpoints = body.pop("tokenpak_checkpoints", [])
    if not isinstance(checkpoints, list):
        return []
    
    messages = body.get("messages", [])
    max_idx = len(messages) - 1
    
    if max_idx < 0:
        return []
    
    # Validate: must be integers, in range [0, max_idx], deduplicate
    valid = []
    for cp in checkpoints:
        if isinstance(cp, int) and 0 <= cp <= max_idx:
            valid.append(cp)
    
    # Sort in reverse order for safe insertion (higher indices first)
    # Deduplicate by converting to set
    return sorted(set(valid), reverse=True)


def _inject_bedrock_checkpoints(provider: Provider, body: bytes) -> bytes:
    """Insert cachePoint blocks at specified positions for Bedrock requests.
    
    Bedrock uses checkpoint blocks to mark cache boundaries. A cachePoint block
    inserted after message[N] means everything up to and including message[N]
    is eligible for caching.
    
    Format: {"cachePoint": {"type": "default"}}
    Optional TTL: {"cachePoint": {"type": "default", "ttl": "1h"}}
    
    Args:
        provider: Detected LLM provider
        body: Request body bytes
        
    Returns:
        Modified body bytes with cachePoint blocks inserted (if Bedrock)
    """
    if provider != Provider.BEDROCK:
        return body
    
    if not body:
        return body
    
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body
    
    # Extract checkpoints (also strips tokenpak_checkpoints from body)
    checkpoints = _extract_bedrock_checkpoints(data)
    
    if not checkpoints:
        # No checkpoints specified, but still need to return body without tokenpak_checkpoints
        return json.dumps(data).encode()
    
    messages = data.get("messages", [])
    if not messages:
        return json.dumps(data).encode()
    
    # Insert cachePoint blocks in reverse index order (preserves lower indices)
    for idx in checkpoints:
        # Insert AFTER the message at idx (so at position idx + 1)
        cache_point = {"cachePoint": {"type": "default"}}
        messages.insert(idx + 1, cache_point)
    
    data["messages"] = messages
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# OpenAI / Azure / Codex / xAI prompt_cache_key Passthrough (CACHE-P2-001)
# ---------------------------------------------------------------------------


def _extract_cache_hints(
    headers: dict, body: dict
) -> "tuple[str | None, str | None]":
    """Extract cache key and retention hints from request headers and body.

    Header takes precedence over body field when both are present.
    ``tokenpak_cache_hint`` and ``tokenpak_cache_retention`` are *popped* from
    *body* so they are never forwarded to the upstream provider.

    Args:
        headers: Request headers dict (case-sensitive).
        body: Parsed request body dict — modified in-place to strip fields.

    Returns:
        ``(cache_key, cache_retention)`` — either may be ``None``.
    """
    # Always pop body fields to ensure they are stripped from the forwarded body,
    # regardless of whether a header hint is also present.
    body_key = body.pop("tokenpak_cache_hint", None)
    body_retention = body.pop("tokenpak_cache_retention", None)
    cache_key = headers.get("x-tokenpak-cache-key") or body_key
    cache_retention = headers.get("x-tokenpak-cache-retention") or body_retention
    return cache_key, cache_retention


def _inject_prompt_cache_key(provider: Provider, headers: dict, body: bytes) -> bytes:
    """Inject ``prompt_cache_key`` for OpenAI / Azure OpenAI / Codex / xAI requests.

    Accepts cache hints from:
    - Header ``x-tokenpak-cache-key`` (takes precedence over body field)
    - Body field ``tokenpak_cache_hint`` (stripped before forwarding)

    Accepts retention hint from:
    - Header ``x-tokenpak-cache-retention``
    - Body field ``tokenpak_cache_retention`` (stripped before forwarding)

    The ``x-grok-conv-id`` header is forwarded naturally by ``_sanitize_headers``
    (it is not in ``_BLOCKED_FORWARD_HEADERS``) — no special handling required here.

    For providers that don't support ``prompt_cache_key``, ``tokenpak_*`` body
    fields are still stripped so they never reach the upstream provider.

    Args:
        provider: Detected LLM provider.
        headers: Request headers dict.
        body: Request body bytes.

    Returns:
        Modified body bytes with cache fields injected and ``tokenpak_*`` stripped.
    """
    if not body:
        return body

    # Fast path: skip JSON parse when no cache hints are present
    _has_header_hint = bool(
        headers.get("x-tokenpak-cache-key") or headers.get("x-tokenpak-cache-retention")
    )
    _has_body_hint = b"tokenpak_cache" in body
    if not _has_header_hint and not _has_body_hint:
        return body

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body

    if not isinstance(data, dict):
        return body

    # Extract hints (pops tokenpak_* fields from data)
    cache_key, cache_retention = _extract_cache_hints(headers, data)

    if not cache_key:
        # tokenpak_* fields may have been stripped; re-encode to apply removal
        return json.dumps(data).encode()

    _CACHE_KEY_PROVIDERS = (Provider.OPENAI, Provider.AZURE_OPENAI, Provider.CODEX, Provider.XAI)
    if provider in (Provider.OPENAI, Provider.AZURE_OPENAI, Provider.CODEX):
        # Codex uses same Responses API as OpenAI — identical cache key fields
        data["prompt_cache_key"] = cache_key
        if cache_retention:
            data["prompt_cache_retention"] = cache_retention
    elif provider == Provider.XAI:
        data["prompt_cache_key"] = cache_key
        # x-grok-conv-id forwarding: handled by _sanitize_headers (not in blocked list)
    # All other providers: silently ignore — tokenpak_* already stripped above

    return json.dumps(data).encode()


def _parse_bedrock_cached_tokens(response_data: dict) -> int:
    """Parse cached token count from Bedrock responses.
    
    Bedrock returns cache metrics in the usage object:
    {
        "usage": {
            "inputTokens": 1000,
            "outputTokens": 50,
            "cacheReadInputTokens": 800,    // tokens read from cache
            "cacheWriteInputTokens": 200    // tokens written to cache
        }
    }
    
    Note: Bedrock also uses CacheReadInputTokens (camelCase) in some response formats.
    We check both snake_case and camelCase variants.
    
    Args:
        response_data: Parsed JSON response from Bedrock
        
    Returns:
        Number of tokens read from cache (0 if not present)
    """
    usage = response_data.get("usage", {})
    if not isinstance(usage, dict):
        return 0
    
    # Check both potential field names (Bedrock uses camelCase in Converse API)
    cache_read = usage.get("cacheReadInputTokens", 0)
    if not cache_read:
        # Some responses might use this format
        cache_read = usage.get("cacheReadInputTokenCount", 0)
    
    return cache_read if isinstance(cache_read, int) else 0


def _parse_bedrock_cache_creation_tokens(response_data: dict) -> int:
    """Parse cache creation token count from Bedrock responses.
    
    Args:
        response_data: Parsed JSON response from Bedrock
        
    Returns:
        Number of tokens written to cache (0 if not present)
    """
    usage = response_data.get("usage", {})
    if not isinstance(usage, dict):
        return 0
    
    cache_write = usage.get("cacheWriteInputTokens", 0)
    if not cache_write:
        cache_write = usage.get("cacheWriteInputTokenCount", 0)
    
    return cache_write if isinstance(cache_write, int) else 0


# ---------------------------------------------------------------------------
# SSE stream parsing
# ---------------------------------------------------------------------------
def _extract_sse_tokens(sse_bytes):
    """Extract token usage from SSE stream responses.
    
    Supports Anthropic, OpenAI, and Gemini formats:
    - Anthropic: cache_read_input_tokens, cache_creation_input_tokens in message_start/message_delta
    - OpenAI: usage.prompt_tokens_details.cached_tokens in final chunk (with stream_options.include_usage)
    - Gemini: usageMetadata.cachedContentTokenCount in response chunks
    """
    result = {"output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    try:
        text = sse_bytes.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except Exception:
                continue
            
            # Anthropic format: message_start contains cache info in message.usage
            if event.get("type") == "message_start":
                usage = event.get("message", {}).get("usage", {})
                if "cache_read_input_tokens" in usage:
                    result["cache_read_input_tokens"] = usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in usage:
                    result["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]
            
            # Anthropic format: message_delta contains output_tokens
            if event.get("type") == "message_delta":
                usage = event.get("usage", {})
                if "output_tokens" in usage:
                    result["output_tokens"] = usage["output_tokens"]
            
            # OpenAI format: completion_tokens in usage object
            if "usage" in event and "completion_tokens" in event.get("usage", {}):
                result["output_tokens"] = event["usage"]["completion_tokens"]
            
            # OpenAI format: prompt_tokens_details.cached_tokens for cache hits
            # This appears in the final chunk when stream_options.include_usage is true
            if "usage" in event:
                usage = event["usage"]
                prompt_details = usage.get("prompt_tokens_details", {})
                if prompt_details:
                    cached_tokens = prompt_details.get("cached_tokens", 0)
                    if cached_tokens and cached_tokens > 0:
                        # Map OpenAI cached_tokens to cache_read_input_tokens
                        result["cache_read_input_tokens"] = cached_tokens
                    # Store audio_tokens for future use (OpenAI audio model support)
                    audio_tokens = prompt_details.get("audio_tokens", 0)
                    if audio_tokens and audio_tokens > 0:
                        result["audio_tokens"] = audio_tokens
            
            # Gemini format: usageMetadata.cachedContentTokenCount for cache hits
            # Gemini streaming can include usageMetadata in response chunks
            if "usageMetadata" in event:
                usage_meta = event["usageMetadata"]
                if isinstance(usage_meta, dict):
                    cached_tokens = usage_meta.get("cachedContentTokenCount", 0)
                    if cached_tokens and cached_tokens > 0:
                        result["cache_read_input_tokens"] = cached_tokens
                    # Also extract output tokens from Gemini format
                    candidates_tokens = usage_meta.get("candidatesTokenCount", 0)
                    if candidates_tokens and candidates_tokens > 0:
                        result["output_tokens"] = candidates_tokens
    except Exception as e:
        print(f"  ⚠️ SSE parse error: {e}")
    return result


# ---------------------------------------------------------------------------
# Forward Proxy Handler
# ---------------------------------------------------------------------------
class ForwardProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _check_auth(self):
        """Check if request is authorized. Localhost always trusted; remote requires auth key if configured."""
        client_ip = self.client_address[0]
        # Localhost (IPv4 and IPv6) always trusted
        if client_ip in ("127.0.0.1", "::1"):
            return True
        # No auth configured = allow (network access at user's risk)
        if not PROXY_AUTH_KEY:
            return True
        # Remote client with auth key configured — check header
        import hmac
        client_key = self.headers.get("X-TokenPak-Key", "")
        return hmac.compare_digest(client_key, PROXY_AUTH_KEY)

    def do_CONNECT(self):
        host, _, port = self.path.partition(":")
        port = int(port) if port else 443
        self._tunnel_connect(host, port)

    def _tunnel_connect(self, host, port):
        try:
            remote = socket.create_connection((host, port), timeout=30)
        except Exception as e:
            self.send_error(502, f"Cannot connect to {host}:{port}: {e}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.connection.setblocking(False)
        remote.setblocking(False)
        timeout = 120
        last_activity = time.time()
        while time.time() - last_activity < timeout:
            data_moved = False
            try:
                data = self.connection.recv(65536)
                if data:
                    remote.sendall(data)
                    last_activity = time.time()
                    data_moved = True
                elif data == b"":
                    break
            except BlockingIOError:
                pass
            except Exception:
                break
            try:
                data = remote.recv(65536)
                if data:
                    self.connection.sendall(data)
                    last_activity = time.time()
                    data_moved = True
                elif data == b"":
                    break
            except BlockingIOError:
                pass
            except Exception:
                break
            if not data_moved:
                time.sleep(0.01)
        remote.close()

    def do_GET(self):
        # Security check: verify auth for non-localhost clients
        if not self._check_auth():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized — missing or invalid X-TokenPak-Key header"}).encode())
            return
        
        if self.path == "/" or self.path == "":
            # Fix: Root path returns welcome JSON instead of 404
            try:
                from tokenpak import __version__ as _tpk_version
            except ImportError:
                _tpk_version = "unknown"
            welcome = {
                "name": "TokenPak",
                "version": _tpk_version,
                "status": "running",
                "endpoints": {
                    "health": "/health",
                    "stats": "/stats",
                    "session_stats": "/stats/session/<session_id>",
                    "docs": "/docs",
                    "proxy": "/v1/messages (POST), /v1/messages/count_tokens (POST), /v1/messages/* (POST passthrough), /v1/chat/completions (POST)",
                },
                "docs": "https://github.com/tokenpak/tokenpak",
            }
            self._send_json(welcome)
            return
        # Fix: POST-only paths return 405 instead of 404 on wrong method
        _POST_ONLY_PATHS = {"/v1/messages", "/v1/messages/count_tokens", "/v1/chat/completions", "/ingest"}
        if self.path.split("?")[0] in _POST_ONLY_PATHS:
            self.send_response(405)
            self.send_header("Allow", "POST, OPTIONS")
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            body = json.dumps({
                "error": {
                    "type": "method_not_allowed",
                    "message": f"Use POST for {self.path.split('?')[0]}",
                }
            }).encode()
            self.wfile.write(body)
            return
        if self.path == "/health":
            # Use 1-second response cache to reduce per-request overhead
            now = _time_module.monotonic()
            if (
                _health_cache["data"] is not None
                and (now - _health_cache["ts"]) < _HEALTH_CACHE_TTL
            ):
                self._send_json(_health_cache["data"])
                return
            vault_info = {
                "available": VAULT_INDEX.available,
                "ready": VAULT_INDEX.available,  # always ready if blocks loaded
                "blocks": len(VAULT_INDEX.blocks),
                "path": str(VAULT_INDEX.tokenpak_dir),
            }
            router_info = _router_health()
            # Strip full SESSION from /health — use /stats for detailed session data
            health_data = {
                "status": "ok",
                "compilation_mode": COMPILATION_MODE,
                "vault_index": vault_info,
                "router": {"enabled": ROUTER_ENABLED, **router_info},
                "capsule_available": CAPSULE_BUILDER is not None,
                "canon": {"enabled": CANON_AVAILABLE, "session_hits": SESSION.get("canon_hits", 0)},
                "skeleton": {"enabled": SKELETON_ENABLED},
                "shadow_reader": {"enabled": SHADOW_ENABLED},
                "budget": {"enabled": True, "total_tokens": BUDGET_TOTAL_TOKENS},
                "tool_schema_registry": {
                    "enabled": TOOL_REGISTRY_AVAILABLE,
                    **(
                        (_get_tool_registry().stats() if _get_tool_registry() else {})
                        if TOOL_REGISTRY_AVAILABLE
                        else {}
                    ),
                },
                "term_resolver": {
                    "enabled": TERM_RESOLVER_ENABLED,
                    "available": TERM_RESOLVER is not None,
                    "top_k": TERM_RESOLVER_TOP_K,
                    "max_bytes_per_card": TERM_RESOLVER_MAX_BYTES,
                },
                "query_expansion": {"enabled": _QUERY_EXPANSION_AVAILABLE},
                "cache_poison_removal": {"enabled": True},
                "strict_validation": {"enabled": STRICT_VALIDATION},
                "upstream_timeout_seconds": UPSTREAM_TIMEOUT,
                "circuit_breakers": {
                    p: {"open": cb["open"], "failures": cb["failures"]}
                    for p, cb in _provider_circuits.items()
                },
                "stats": {
                    "requests": SESSION.get("requests", 0),
                    "input_tokens": SESSION.get("input_tokens", 0),
                    "sent_input_tokens": SESSION.get("sent_input_tokens", 0),
                    "saved_tokens": SESSION.get("saved_tokens", 0),
                    "errors": SESSION.get("errors", 0),
                    "cache_hits": SESSION.get("cache_hits", 0),
                    "cache_misses": SESSION.get("cache_misses", 0),
                    "cost": SESSION.get("cost", 0),
                },
                "latency": (lambda lats: {
                    "p50_latency_ms": lats[int(len(lats) * 0.50)] if lats else 0,
                    "p99_latency_ms": lats[int(len(lats) * 0.99)] if lats else 0,
                    "samples": len(lats),
                })(sorted(_request_latencies)),
            }
            _health_cache["data"] = health_data
            _health_cache["ts"] = now
            self._send_json(health_data)
            return
        if self.path == "/stats":
            # CACHE-P4-002: Build cache summary for /stats
            _cache_hits = SESSION.get("cache_hits", 0)
            _cache_misses = SESSION.get("cache_misses", 0)
            _cache_total = _cache_hits + _cache_misses
            _cache_hit_rate = (_cache_hits / _cache_total) if _cache_total > 0 else 0.0
            _cache_by_provider = SESSION.get("cache_by_provider", {})
            _total_savings = sum(p.get("savings_usd", 0.0) for p in _cache_by_provider.values())
            _active_providers = [p for p in _cache_by_provider.keys() if _cache_by_provider[p].get("hits", 0) > 0]
            
            self._send_json(
                {
                    "session": SESSION,
                    "compilation_mode": COMPILATION_MODE,
                    "vault_index": {
                        "available": VAULT_INDEX.available,
                        "blocks": len(VAULT_INDEX.blocks),
                        "last_timing_ms": SESSION.get("vault_last_timing_ms", {}),
                    },
                    "router": {"enabled": ROUTER_ENABLED},
                    "capsule_available": CAPSULE_BUILDER is not None,
                    "compression_timeouts": SESSION.get("compression_timeouts", 0),
                    "max_compression_time_ms": MAX_COMPRESSION_TIME_MS,
                    "canon": {
                        "enabled": CANON_AVAILABLE,
                        "session_hits": SESSION.get("canon_hits", 0),
                        "tokens_saved": SESSION.get("canon_tokens_saved", 0),
                    },
                    # CACHE-P4-002: Cache summary in /stats response
                    "cache": {
                        "enabled": True,
                        "hit_rate_session": round(_cache_hit_rate, 4),
                        "hits": _cache_hits,
                        "misses": _cache_misses,
                        "read_tokens": SESSION.get("cache_read_tokens", 0),
                        "creation_tokens": SESSION.get("cache_creation_tokens", 0),
                        "savings_usd_session": round(_total_savings, 6),
                        "providers_active": _active_providers,
                    },
                    # CACHE-P4-002: Structured per-provider telemetry from CacheTelemetry
                    "cache_telemetry": (
                        CACHE_TELEMETRY.to_dict()
                        if CACHE_TELEMETRY is not None
                        else {"enabled": False}
                    ),
                    "skeleton": {"enabled": SKELETON_ENABLED},
                    "shadow_reader": {"enabled": SHADOW_ENABLED},
                    "budget": {"enabled": True, "total_tokens": BUDGET_TOTAL_TOKENS},
                    "swap_mb": check_swap_pressure(),
                    "today": MONITOR.get_stats(),
                    "by_model": MONITOR.get_by_model(),
                    "recent": MONITOR.recent(10),
                }
            )
            return
        if self.path == "/cache-stats":
            self._send_json(_build_cache_stats_payload())
            return
        if self.path == "/recent":
            self._send_json({"recent": MONITOR.recent(50)})
            return
        if self.path == "/stats/last":
            # Per-request stats for the most recent request
            with _LAST_REQUEST_LOCK:
                if LAST_REQUEST["request_id"] is None:
                    self._send_json(
                        {
                            "error": "no_requests",
                            "message": "No requests captured yet. Send a message to see stats.",
                        }
                    )
                else:
                    self._send_json(
                        {
                            "request_id": LAST_REQUEST["request_id"],
                            "timestamp": LAST_REQUEST["timestamp"],
                            "model": LAST_REQUEST["model"],
                            "tokens_saved": LAST_REQUEST["tokens_saved"],
                            "percent_saved": LAST_REQUEST["percent_saved"],
                            "cost_saved": LAST_REQUEST["cost_saved"],
                            "session_total_saved": round(SESSION["cost_saved"], 4),
                            "session_requests": SESSION["requests"],
                            "input_tokens_raw": LAST_REQUEST["input_tokens_raw"],
                            "input_tokens_sent": LAST_REQUEST["input_tokens_sent"],
                            "output_tokens": LAST_REQUEST["output_tokens"],
                        }
                    )
            return
        if self.path == "/stats/session":
            # Session aggregates
            uptime_hours = round((time.time() - SESSION["start_time"]) / 3600, 2)
            self._send_json(
                {
                    "session_requests": SESSION["requests"],
                    "session_total_saved": round(SESSION["cost_saved"], 4),
                    "tokens_saved": SESSION["saved_tokens"],
                    "tokens_sent": SESSION["sent_input_tokens"],
                    "tokens_raw": SESSION["input_tokens"],
                    "output_tokens": SESSION["output_tokens"],
                    "total_cost": round(SESSION["cost"], 4),
                    "uptime_hours": uptime_hours,
                    "errors": SESSION["errors"],
                    "avg_savings_pct": round(
                        SESSION["saved_tokens"] / SESSION["input_tokens"] * 100, 1
                    )
                    if SESSION["input_tokens"] > 0
                    else 0.0,
                }
            )
            return
        if self.path.startswith("/stats/session/"):
            # CCG-07: Per-session aggregate stats endpoint
            # Returns zeros (not 404) for unknown sessions so dashboards don't break.
            session_id = self.path[len("/stats/session/"):]
            db_path = MONITOR.db_path
            try:
                _conn = sqlite3.connect(str(db_path))
                _conn.row_factory = sqlite3.Row
                # Main token/cost/count aggregates
                agg_row = _conn.execute(
                    """
                    SELECT
                        COALESCE(SUM(input_tokens), 0)          AS input_tokens,
                        COALESCE(SUM(output_tokens), 0)         AS output_tokens,
                        COALESCE(SUM(cache_read_tokens), 0)     AS cache_read_input_tokens,
                        COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_input_tokens,
                        COALESCE(SUM(estimated_cost), 0.0)      AS cost,
                        COUNT(*)                                 AS request_count
                    FROM requests WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()
                # mutation_count: rows where rules_applied != '[]'
                try:
                    mut_row = _conn.execute(
                        "SELECT COUNT(*) FROM mutation_audit WHERE session_id = ? AND rules_applied != '[]'",
                        (session_id,),
                    ).fetchone()
                    mutation_count = mut_row[0] if mut_row else 0
                except sqlite3.OperationalError:
                    # mutation_audit table not yet present (pre-CCG-02)
                    mutation_count = 0
                # latency percentiles — fetch all latency_ms values for this session, compute in Python
                lat_rows = _conn.execute(
                    "SELECT latency_ms FROM requests WHERE session_id = ? AND latency_ms IS NOT NULL ORDER BY latency_ms",
                    (session_id,),
                ).fetchall()
                latencies = [r[0] for r in lat_rows]
                if latencies:
                    p50_idx = int(len(latencies) * 0.50)
                    p99_idx = min(int(len(latencies) * 0.99), len(latencies) - 1)
                    latency_p50 = latencies[p50_idx]
                    latency_p99 = latencies[p99_idx]
                else:
                    latency_p50 = 0
                    latency_p99 = 0
                _conn.close()
                self._send_json({
                    "session_id": session_id,
                    "input_tokens": agg_row["input_tokens"],
                    "output_tokens": agg_row["output_tokens"],
                    "cache_read_input_tokens": agg_row["cache_read_input_tokens"],
                    "cache_creation_input_tokens": agg_row["cache_creation_input_tokens"],
                    "cost": round(float(agg_row["cost"]), 6),
                    "request_count": agg_row["request_count"],
                    "mutation_count": mutation_count,
                    "latency_p50": latency_p50,
                    "latency_p99": latency_p99,
                })
            except Exception as _e:
                self._send_json({"error": str(_e)}, status=500)
            return
        if self.path == "/savings" or self.path.startswith("/savings?"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            qparams = parse_qs(parsed.query)
            since = qparams.get("since", [None])[0]
            self._send_json(MONITOR.get_savings_report(since=since))
            return
        if self.path == "/vault":
            # Debug endpoint: show vault index state
            blocks_info = []
            for bid, block in VAULT_INDEX.blocks.items():
                blocks_info.append(
                    {
                        "block_id": bid,
                        "source_path": block["source_path"],
                        "risk_class": block["risk_class"],
                        "raw_tokens": block["raw_tokens"],
                    }
                )
            self._send_json(
                {
                    "available": VAULT_INDEX.available,
                    "blocks": len(VAULT_INDEX.blocks),
                    "total_tokens": sum(b["raw_tokens"] for b in VAULT_INDEX.blocks.values()),
                    "path": str(VAULT_INDEX.tokenpak_dir),
                    "block_list": blocks_info,
                }
            )
            return
        if self.path == "/dashboard/failovers":
            # CCI-05: Failover event log panel
            with _FAILOVER_EVENTS_LOCK:
                events = list(_FAILOVER_EVENTS)
            self._send_json({
                "failovers": list(reversed(events)),  # newest first
                "total": len(events),
                "chain": _FALLBACK_CHAIN,
                "chain_enabled": len(_FALLBACK_CHAIN) > 1,
            })
            return
        if self.path == "/trace/last":
            trace = TRACE_STORAGE.get_last()
            if trace:
                self._send_json(trace.to_dict())
            else:
                self._send_json(
                    {
                        "error": "no traces",
                        "message": "No requests captured yet. Send a message to see the pipeline in action.",
                    }
                )
            return
        if self.path.startswith("/trace/"):
            # /trace/{request_id}
            request_id = self.path.split("/trace/")[1]
            trace = TRACE_STORAGE.get_by_id(request_id)
            if trace:
                self._send_json(trace.to_dict())
            else:
                self._send_json(
                    {
                        "error": "not found",
                        "message": f"No trace found for request_id: {request_id}",
                    }
                )
            return
        if self.path == "/traces":
            traces = TRACE_STORAGE.get_all()
            self._send_json({"traces": [t.to_dict() for t in traces], "count": len(traces)})
            return
        if self.path == "/metrics":
            # Prometheus text format metrics (labeled, with histogram)
            try:
                from tokenpak.metrics.prometheus import build_metrics_text

                vault_blocks = len(VAULT_INDEX.blocks) if VAULT_INDEX.available else 0
                body_out = build_metrics_text(SESSION, MONITOR, vault_blocks=vault_blocks).encode()
            except Exception:
                # Fallback: minimal unlabeled metrics if module unavailable
                s = SESSION
                uptime = int(time.time() - s.get("start_time", time.time()))
                lines = [
                    f"tokenpak_requests_total {s.get('requests', 0)}",
                    f"tokenpak_tokens_input_total {s.get('input_tokens', 0)}",
                    f"tokenpak_tokens_saved_total {s.get('saved_tokens', 0)}",
                    f"tokenpak_errors_total {s.get('errors', 0)}",
                    f"tokenpak_uptime_seconds {uptime}",
                ]
                body_out = "\n".join(lines).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", len(body_out))
            self.end_headers()
            self.wfile.write(body_out)
            return
        if self.path == "/metrics/dashboard":
            # New: Comprehensive dashboard metrics endpoint with 8 key metrics
            # 1. Request count + throughput (req/sec)
            # 2. Latency histogram (p50, p95, p99)
            # 3. Model provider distribution
            # 4. Routing decisions (smart routing hit rate)
            # 5. Cache hit ratio
            # 6. Error rate + top failure types
            # 7. Live streaming request count
            # 8. 24-hour rolling window

            today_stats = MONITOR.get_stats(hours=24)
            recent_reqs = MONITOR.recent(limit=100)
            by_model = MONITOR.get_by_model()
            uptime_secs = int(time.time() - SESSION["start_time"])
            uptime_hours = max(0.01, uptime_secs / 3600.0)

            # Calculate throughput (req/sec over last hour or since start)
            if len(recent_reqs) > 1:
                first_ts = datetime.fromisoformat(recent_reqs[-1]["timestamp"])
                last_ts = datetime.fromisoformat(recent_reqs[0]["timestamp"])
                time_diff_secs = max(1, (last_ts - first_ts).total_seconds())
                throughput = len(recent_reqs) / time_diff_secs
            else:
                throughput = today_stats["requests"] / uptime_hours / 3600.0

            # Latency percentiles from recent requests
            latencies = [r.get("latency_ms", 0) for r in recent_reqs if r.get("latency_ms")]
            latencies.sort()
            p50 = latencies[len(latencies) // 2] if latencies else 0
            p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
            p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
            avg_latency = today_stats.get("avg_latency_ms", 0)

            # Error rate and top failure types
            error_count = sum(1 for r in recent_reqs if r.get("status_code", 200) >= 400)
            error_rate = error_count / len(recent_reqs) if recent_reqs else 0

            # Top failure types (group by status code)
            failure_types = {}
            for r in recent_reqs:
                sc = r.get("status_code", 200)
                if sc >= 400:
                    failure_types[str(sc)] = failure_types.get(str(sc), 0) + 1

            # Cache metrics
            total_cache_read = today_stats.get("cache_read_tokens", 0)
            total_cache_creation = today_stats.get("cache_creation_tokens", 0)
            cache_hit_ratio = 0.0
            if total_cache_read > 0 or total_cache_creation > 0:
                cache_hit_ratio = (
                    total_cache_read / (total_cache_read + total_cache_creation)
                    if (total_cache_read + total_cache_creation) > 0
                    else 0.0
                )

            # Model distribution
            model_dist = {}
            for model, data in by_model.items():
                model_dist[model] = {
                    "requests": data.get("requests", 0),
                    "input_tokens": data.get("input_tokens", 0),
                    "cost": data.get("cost", 0.0),
                }

            # Routing decisions (smart routing hit rate) — placeholder
            routing_hit_rate = 0.0  # Placeholder: implement when routing stats available

            # Streaming request count — placeholder
            streaming_count = 0  # Placeholder: implement when streaming detection available

            dashboard_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": uptime_secs,
                "uptime_hours": round(uptime_hours, 2),
                # Key Metric 1: Request count + throughput
                "requests": {
                    "total": today_stats.get("requests", 0),
                    "throughput_req_per_sec": round(throughput, 3),
                    "24h_window": True,
                },
                # Key Metric 2: Latency histogram
                "latency": {
                    "p50_ms": round(p50, 1),
                    "p95_ms": round(p95, 1),
                    "p99_ms": round(p99, 1),
                    "avg_ms": round(avg_latency, 1),
                    "samples": len(latencies),
                },
                # Key Metric 3: Model provider distribution
                "models": model_dist,
                "model_count": len(model_dist),
                # Key Metric 4: Routing decisions
                "routing": {
                    "smart_routing_hit_rate": round(routing_hit_rate, 3),
                    "fallback_chain_usage": 0,  # Placeholder: implement
                },
                # Key Metric 5: Cache hit ratio
                "cache": {
                    "hit_ratio": round(cache_hit_ratio, 3),
                    "read_tokens": total_cache_read,
                    "creation_tokens": total_cache_creation,
                },
                # Key Metric 6: Error rate + top failure types
                "errors": {
                    "error_rate": round(error_rate, 4),
                    "error_count": error_count,
                    "top_failures": dict(
                        sorted(failure_types.items(), key=lambda x: x[1], reverse=True)[:5]
                    ),
                },
                # Key Metric 7: Streaming request count
                "streaming": {
                    "count": streaming_count,
                    "percentage": 0.0,
                },
                # Key Metric 8: 24-hour rolling window stats
                "window_24h": {
                    "input_tokens": today_stats.get("input_tokens", 0),
                    "output_tokens": today_stats.get("output_tokens", 0),
                    "protected_tokens": today_stats.get("protected_tokens", 0),
                    "compressed_tokens": today_stats.get("compressed_tokens", 0),
                    "injected_tokens": today_stats.get("injected_tokens", 0),
                    "total_cost": today_stats.get("total_cost", 0.0),
                },
            }

            self._send_json(dashboard_data)
            return
        if self.path == "/metrics/dashboard/tools":
            # CCI-02: Tool schema registry telemetry panel
            # Exposes: tools_normalized_count, bytes_saved_total,
            #          cache_hit_rate_for_tools_block, schema_changes
            tool_reg_data = {
                "enabled": TOOL_REGISTRY_AVAILABLE,
                "tools_normalized_count": 0,
                "bytes_saved_total": 0,
                "frozen_tools": 0,
                "frozen_bytes": 0,
                "frozen_tokens_approx": 0,
                "cache_hit_rate_for_tools_block": 0.0,
                "schema_changes": 0,
                "frozen_hash": None,
                "session_bytes_saved": SESSION.get("tool_schema_bytes_saved", 0),
                "session_frozen_tools": SESSION.get("tool_schema_frozen_tools", 0),
            }
            if TOOL_REGISTRY_AVAILABLE:
                try:
                    _treg = _get_tool_registry()
                    if _treg:
                        _ts = _treg.stats()
                        _total_req = _ts.get("total_requests", 0)
                        _schema_changes = _ts.get("schema_changes", 0)
                        _cache_hit_rate = (
                            round((_total_req - _schema_changes) / max(1, _total_req), 3)
                            if _total_req > 0
                            else 0.0
                        )
                        tool_reg_data.update({
                            "tools_normalized_count": _total_req,
                            "bytes_saved_total": _ts.get("bytes_saved", 0),
                            "frozen_tools": _ts.get("frozen_tools", 0),
                            "frozen_bytes": _ts.get("frozen_bytes", 0),
                            "frozen_tokens_approx": _ts.get("frozen_tokens_approx", 0),
                            "cache_hit_rate_for_tools_block": _cache_hit_rate,
                            "schema_changes": _schema_changes,
                            "frozen_hash": _ts.get("frozen_hash"),
                        })
                except Exception:
                    pass
            self._send_json(tool_reg_data)
            return
        if self.path.startswith("http"):
            self._forward_request("GET")
        elif self.path.split("?")[0] in ("/dashboard/tools", "/dashboard/tools/"):
            self._serve_tools_panel()
        elif self.path.split("?")[0] == "/dashboard" or self.path.split("?")[0].startswith(
            "/dashboard/"
        ):
            self._serve_dashboard()
        elif self.path == "/docs" or self.path == "/docs/":
            self._serve_api_docs()
        elif self.path == "/openapi.yaml":
            self._serve_openapi_yaml()
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("GET")
        else:
            # Fix #2: JSON 404 instead of HTML
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    def do_HEAD(self):
        """Handle HEAD requests — same as GET but suppress response body.

        Needed by K8s liveness probes, uptime monitors, and load balancers
        that use HEAD /health instead of GET /health.
        """
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        else:
            self.send_response(405)
            self.send_header("Allow", "GET, POST, OPTIONS")
            self.send_header("Content-Type", "application/json")
            self.end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight requests.

        Browser frontends send OPTIONS before POST /v1/messages. Without this,
        all browser-based clients are blocked by CORS.
        """
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, x-api-key, anthropic-version",
        )
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        # Security check: verify auth for non-localhost clients
        if not self._check_auth():
            self._send_json({"error": "Unauthorized — missing or invalid X-TokenPak-Key header"}, status=401)
            return
        
        # Fix #7: Per-IP rate limiting
        client_ip = self.client_address[0]
        if not _rate_limit_check(client_ip):
            self._send_json(
                {
                    "error": {
                        "type": "rate_limit_exceeded",
                        "message": f"Too many requests. Limit: {_RATE_LIMIT_RPM} req/min per IP.",
                    }
                },
                status=429,
            )
            return
        if self.path == "/config/reload":
            # Localhost-only hot config reload (same effect as SIGHUP)
            if client_ip not in ("127.0.0.1", "::1"):
                self._send_json(
                    {"error": {"type": "forbidden", "message": "Config reload only allowed from localhost"}},
                    status=403,
                )
                return
            msg = _reload_config_from_env()
            self._send_json({"status": "ok", "message": msg}, status=200)
            return
        elif self.path.startswith("http"):
            self._forward_request("POST")
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("POST")
        elif self.path.split("?")[0] == "/v1/messages/count_tokens":
            self._handle_count_tokens()
        elif self.path.startswith("/v1/messages/"):
            # CCG-05: Default passthrough for unrecognised /v1/messages/* subpaths.
            # Forwards body + headers to upstream untouched (guards future Anthropic API additions).
            self._reverse_proxy("POST")
        elif self.path.startswith("/v1/") or self.path.startswith("/v1beta/"):
            self._reverse_proxy("POST")
        elif self.path.startswith("/codex/"):
            self._reverse_proxy("POST")
        elif self.path == "/ingest" or self.path == "/ingest/batch":
            self._ingest(self.path)
        else:
            # Fix #2: JSON 404 instead of HTML
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    def do_PUT(self):
        if self.path.startswith("http"):
            self._forward_request("PUT")
        else:
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    def do_DELETE(self):
        if self.path.startswith("http"):
            self._forward_request("DELETE")
        else:
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    def _forward_request(self, method):
        self._proxy_to(self.path, method)

    def _ollama_proxy(self, method):
        """Route /ollama-proxy/... to the real ollama server with compaction pipeline.

        Circuit breaker: if upstream was unreachable within the last 120s,
        return 503 immediately instead of hanging for minutes.
        Connect timeout: 20s (configurable via TOKENPAK_OLLAMA_TIMEOUT).
        """
        from urllib.parse import urlparse

        # Check circuit breaker -- fail fast if upstream recently unreachable
        with _ollama_circuit_lock:
            if _ollama_circuit["open"]:
                elapsed = time.time() - _ollama_circuit["last_failure"]
                if elapsed < _ollama_circuit["cooldown"]:
                    err_msg = f"Ollama upstream {OLLAMA_UPSTREAM} unreachable (circuit open, retry in {int(_ollama_circuit['cooldown'] - elapsed)}s)"
                    print(f"  \u26a1 {err_msg}")
                    try:
                        err = json.dumps(
                            {"error": {"type": "circuit_open", "message": err_msg}}
                        ).encode()
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", len(err))
                        self.end_headers()
                        self.wfile.write(err)
                    except Exception:
                        pass
                    return
                else:
                    _ollama_circuit["open"] = False
                    print("  \U0001f504 Ollama circuit breaker reset -- retrying upstream")

        # Probe upstream connectivity with short timeout before committing
        parsed = urlparse(OLLAMA_UPSTREAM)
        host = parsed.hostname
        port = parsed.port or 11434
        try:
            probe = socket.create_connection((host, port), timeout=OLLAMA_CONNECT_TIMEOUT)
            probe.close()
        except (socket.timeout, OSError, ConnectionRefusedError) as e:
            with _ollama_circuit_lock:
                _ollama_circuit["open"] = True
                _ollama_circuit["last_failure"] = time.time()
            err_msg = (
                f"Ollama upstream {host}:{port} unreachable after {OLLAMA_CONNECT_TIMEOUT}s: {e}"
            )
            print(f"  \u274c {err_msg}")
            SESSION["errors"] += 1
            try:
                err = json.dumps(
                    {"error": {"type": "upstream_unreachable", "message": err_msg}}
                ).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(err))
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass
            return

        # Upstream reachable -- forward normally
        real_path = self.path[len("/ollama-proxy") :]
        target = OLLAMA_UPSTREAM + real_path
        self._proxy_to(target, method, force_intercept=True)

    def _handle_count_tokens(self):
        """CCG-05: Handle POST /v1/messages/count_tokens — compute token count locally.

        Parses the Anthropic Messages body, sums token counts across system/messages/tools
        via the local count_tokens() helper, and returns {"input_tokens": N}.
        No upstream round-trip. Honors anthropic-version and anthropic-beta headers
        (they do not affect local computation).
        """
        content_length = int(self.headers.get("Content-Length", 0))
        try:
            body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
            payload = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(
                {"error": {"type": "invalid_request_error", "message": f"Request body is not valid JSON: {exc}"}},
                status=400,
            )
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            self._send_json(
                {"error": {"type": "invalid_request_error", "message": "Request body must include a 'messages' array"}},
                status=400,
            )
            return

        total = 0

        # system — string or list of content blocks
        system = payload.get("system", "")
        if isinstance(system, str):
            total += count_tokens(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        total += count_tokens(text)
                elif isinstance(block, str):
                    total += count_tokens(block)

        # messages[].content — string or list of content blocks
        for msg in payload["messages"]:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                total += count_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "")
                        if isinstance(text, str):
                            total += count_tokens(text)
                    elif isinstance(block, str):
                        total += count_tokens(block)

        # tools[] — name, description, and input_schema
        for tool in payload.get("tools", []):
            if not isinstance(tool, dict):
                continue
            total += count_tokens(tool.get("name", ""))
            total += count_tokens(tool.get("description", ""))
            schema = tool.get("input_schema", {})
            if isinstance(schema, dict):
                total += count_tokens(json.dumps(schema, separators=(",", ":")))

        self._send_json({"input_tokens": total})

    def _reverse_proxy(self, method):
        # Pre-flight: check for missing API credentials before touching upstream.
        # If the client sent no auth header AND the environment has no key set,
        # surface a clear auth_missing error immediately rather than forwarding a
        # bare request that will just fail with a cryptic 401 from the provider.
        _req_headers_lower = {k.lower(): v for k, v in self.headers.items()}
        _has_client_auth = bool(
            _req_headers_lower.get("x-api-key", "").strip()
            or _req_headers_lower.get("authorization", "").strip()
        )
        _is_codex_path = (
            self.path.startswith("/codex/")
            or self.path.startswith("/v1/codex/")
        )
        if not _has_client_auth and not _is_codex_path:
            _env_key = (
                os.environ.get("ANTHROPIC_API_KEY", "").strip()
                or os.environ.get("OPENAI_API_KEY", "").strip()
                or os.environ.get("GOOGLE_API_KEY", "").strip()
                or os.environ.get("GEMINI_API_KEY", "").strip()
            )
            if not _env_key:
                self._send_json(
                    _make_structured_error(
                        "auth_missing",
                        "No API key provided and no key found in environment.",
                        "Set your API key via the x-api-key header or environment variable. "
                        "Example: export ANTHROPIC_API_KEY=<your-api-key>",
                    ),
                    status=401,
                )
                return

        headers = _header_mapping(self.headers)
        adapter = _detect_adapter(path=self.path, headers=headers, body_bytes=None)
        try:
            base = _resolve_upstream(adapter)
        except ValueError as exc:
            self._send_json(
                {
                    "error": {
                        "type": "upstream_route_missing",
                        "message": str(exc),
                    }
                },
                status=502,
            )
            return
        self._proxy_to(base + self.path, method, adapter=adapter)

    def _proxy_to(
        self, target_url, method, force_intercept=False, adapter: Optional[FormatAdapter] = None
    ):
        t0 = time.time()
        parsed = urlparse(target_url)
        content_length = int(self.headers.get("Content-Length", 0))
        # Body size cap — configurable via TOKENPAK_MAX_REQUEST_SIZE (default 10 MB)
        if content_length > _MAX_REQUEST_BYTES:
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "error": {
                            "type": "request_too_large",
                            "message": f"Request body exceeds limit ({content_length} bytes > {_MAX_REQUEST_BYTES} bytes). "
                            "Set TOKENPAK_MAX_REQUEST_SIZE to raise the limit.",
                        }
                    }
                ).encode()
            )
            return
        body = self.rfile.read(content_length) if content_length > 0 else None
        active_adapter = adapter
        if active_adapter is None and body is not None:
            active_adapter = _detect_adapter(self.path, _header_mapping(self.headers), body)

        if active_adapter is None:
            active_adapter = _detect_adapter(self.path, _header_mapping(self.headers), None)

        # CCI-04: per-request Claude Code profile detection — overrides ACTIVE_PROFILE for this
        # request when a Claude-Code-aware UA or header is present (env var still wins per
        # _detect_claude_code_profile() precedence rules).
        _req_hdrs_for_detect = {k: v for k, v in self.headers.items()}
        _detected = _detect_claude_code_profile(_req_hdrs_for_detect)
        if _detected is not None:
            SESSION["active_profile"] = _detected
            print(f"[tokenpak] active_profile: {_detected}")

        # X-TokenPak-Bypass: skip compression pipeline for this request
        _bypass_header_val = self.headers.get("x-tokenpak-bypass", "").strip().lower()
        _bypass_request: bool = _bypass_header_val in ("true", "1", "yes")

        should_log = (
            force_intercept
            or active_adapter.source_format != "passthrough"
            or any(h in target_url for h in INTERCEPT_HOSTS)
        )
        is_messages = True
        pipeline_enabled = active_adapter.source_format != "passthrough" and not _bypass_request

        model = "unknown"
        input_tokens = 0
        sent_input_tokens = 0
        protected_tokens = 0
        injected_tokens = 0
        injected_sources: List[str] = []
        is_streaming = False
        cache_read_tokens = 0
        cache_creation_tokens = 0
        cache_poison_scrubbed = False
        tools_schema_changed = False
        raw_request_body_for_cache_reason = body
        final_request_body_for_cache_reason = body
        router_meta: Optional[dict] = None

        # CCG-06: Mutation audit tracking — transparent mode byte-equality contract
        _transparent_mode: bool = COMPILATION_MODE == "transparent"
        _body_pre_audit: bytes = body if isinstance(body, bytes) else b""
        _body_post_audit: bytes = _body_pre_audit  # updated after pipeline
        _audit_rules: list = []
        _audit_cache_risk: str = "none"

        # Pipeline trace
        trace: Optional[PipelineTrace] = None
        _wf_id = None  # proxy workflow tracking (TOKENPAK_WORKFLOW_TRACKING=1)
        if should_log and is_messages:
            trace = PipelineTrace(
                request_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )
            # Start workflow tracking (no-op when feature flag is OFF)
            try:
                from tokenpak.agent.agentic.proxy_workflow import start_proxy_workflow

                _wf_id = start_proxy_workflow(
                    trace.request_id,
                    metadata={"path": self.path, "method": method},
                )
            except Exception:
                pass

        if _bypass_request and body:
            # Bypass mode: skip entire compression pipeline, pass through unmodified
            print(f"  ⏩ X-TokenPak-Bypass: passthrough (bypass header set)")

        if should_log and is_messages and body and not _bypass_request:
            # Fix #5: Strict validation mode — reject malformed requests early
            if STRICT_VALIDATION:
                try:
                    _val_data = json.loads(body)
                    _val_errors = []
                    if "messages" not in _val_data:
                        _val_errors.append("missing required field: messages")
                    if "model" not in _val_data:
                        _val_errors.append("missing required field: model")
                    msgs = _val_data.get("messages", [])
                    if not isinstance(msgs, list) or len(msgs) == 0:
                        _val_errors.append("messages must be a non-empty array")
                    if _val_errors:
                        _first_err = _val_errors[0]
                        # Extract first missing field name for the hint
                        _fld = None
                        if "messages" in _first_err:
                            _fld = "messages"
                        elif "model" in _first_err:
                            _fld = "model"
                        _val_hint = "Fix the request body before retrying. See: https://docs.anthropic.com/en/api/messages"
                        _val_payload: dict = {
                            "error": {
                                "type": "validation_error",
                                "message": "; ".join(_val_errors),
                                "hint": _val_hint,
                            }
                        }
                        if _fld:
                            _val_payload["error"]["field"] = _fld
                        self._send_json(_val_payload, status=400)
                        return
                except json.JSONDecodeError as _je:
                    self._send_json(
                        {
                            "error": {
                                "type": "invalid_json",
                                "message": str(_je),
                                "hint": "The request body must be valid JSON. Check for missing quotes, trailing commas, or unescaped characters.",
                            }
                        },
                        status=400,
                    )
                    return

            _original_body = body  # save for fallback
            # CCG-06: capture raw bytes before any mutation stage
            _body_pre_audit = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else b"")
            _t0_compression = time.monotonic()  # compression pipeline start time

            def _compression_budget_exceeded() -> bool:
                """Return True if we've blown the MAX_COMPRESSION_TIME_MS budget."""
                if MAX_COMPRESSION_TIME_MS <= 0:
                    return False
                return (time.monotonic() - _t0_compression) * 1000 > MAX_COMPRESSION_TIME_MS

            try:
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                # PERF OPT: parse body JSON once here, reuse throughout pipeline
                req_data = None
                try:
                    req_data = json.loads(body)
                    is_streaming = req_data.get("stream", False)
                except Exception:
                    pass

                # Phase -3: Request Logger — generate request ID and start logging
                _request_log_id = None
                if REQUEST_LOGGER_ENABLED:
                    try:
                        from tokenpak.monitoring.request_logger import RequestLogger

                        _req_logger = RequestLogger.get_instance()
                        _request_log_id = _req_logger.new_request_id(
                            dict(self.headers) if self.headers else None
                        )
                        SESSION["request_logger_id"] = _request_log_id
                    except Exception as _rl_err:
                        SESSION["request_logger_error"] = str(_rl_err)
                        pass  # fail-open

                if pipeline_enabled:
                    # Phase -2: Semantic Cache — short-circuit duplicate/similar queries
                    if SEMANTIC_CACHE_ENABLED and body:
                        try:
                            _sem_cache = _get_sem_cache()
                            if _sem_cache is None:
                                raise ImportError("SemanticCache unavailable")
                            _sem_query = body.decode("utf-8") if isinstance(body, bytes) else body
                            _cache_result = _sem_cache.lookup(_sem_query)
                            if (
                                _cache_result is not None
                                and _cache_result.hit
                                and _cache_result.entry
                            ):
                                SESSION["semantic_cache_hit"] = True
                                SESSION["phase_semantic_cache"] = "hit"
                                # Return cached response — skip all processing
                                _cached_resp = _cache_result.entry.response
                                if isinstance(_cached_resp, dict):
                                    self._send_json(_cached_resp)
                                elif isinstance(_cached_resp, bytes):
                                    self.wfile.write(_cached_resp)
                                else:
                                    self._send_json(json.loads(_cached_resp))
                                return
                            SESSION["phase_semantic_cache"] = "miss"
                        except Exception as _sc_err:
                            SESSION["phase_semantic_cache"] = f"error:{_sc_err}"
                            pass  # fail-open: never break a request over semantic cache

                    # Phase -1: Tool Schema Registry — normalize tools to byte-identical JSON
                    # Enables Anthropic cache hits on repeated tool schemas
                    if TOOL_REGISTRY_AVAILABLE and body and not _transparent_mode:
                        try:
                            _tool_reg = _get_tool_registry()
                            if _tool_reg:
                                body, _tools_changed = _tool_reg.normalize_request(body)
                                tools_schema_changed = bool(_tools_changed)
                                _tstats = _tool_reg.stats()
                                SESSION["tool_schema_frozen_tools"] = _tstats.get("frozen_tools", 0)
                                SESSION["tool_schema_bytes_saved"] = _tool_reg.bytes_saved
                                if _tools_changed:
                                    _audit_rules.append("tool_schema_normalize")
                        except Exception as _treg_err:
                            pass  # fail-open: never break a request over tool registry

                    # Phase 0: Manual routing rules — rewrite model before any processing
                    # PERF OPT: use singleton RouteEngine + cached rules + reuse req_data
                    if not _transparent_mode:
                        try:
                            from tokenpak.routing.rules import (
                                _count_tokens_approx,
                                _extract_prompt_text,
                            )

                            _route_engine = _get_route_engine()
                            if _route_engine is not None:
                                # Reuse already-parsed req_data if available, else fallback
                                _route_payload = (
                                    req_data
                                    if req_data is not None
                                    else (json.loads(body) if body else {})
                                )
                                _route_prompt = _extract_prompt_text(_route_payload)
                                _route_tokens = _count_tokens_approx(_route_prompt)
                                _cached_rules = _get_cached_route_rules()
                                _matched_rule = _route_engine.match(
                                    model=model,
                                    prompt=_route_prompt,
                                    token_count=_route_tokens,
                                    rules=_cached_rules,
                                )
                                if _matched_rule:
                                    _route_payload = dict(_route_payload)  # copy before mutate
                                    _route_payload["model"] = _matched_rule.target
                                    body = json.dumps(_route_payload).encode()
                                    req_data = _route_payload  # keep req_data in sync
                                    model = _matched_rule.target
                                    _audit_rules.append("model_route_rewrite")
                                    print(
                                        f"  🔀 Route rule [{_matched_rule.id}]: → {_matched_rule.target}"
                                    )
                        except Exception as _route_err:
                            print(f"  ⚠️ Routing rule error (skipping): {_route_err}")

                    # Phase 0.1: Precondition Gates — reject requests likely to fail
                    # PERF OPT: use singleton PreconditionGates (avoids per-request import + init)
                    if PRECONDITION_GATES_ENABLED and body:
                        try:
                            _pg = _get_precond_gates()
                            if _pg is not None:
                                _pg_pass, _pg_reason = _pg.check(model)
                                SESSION["precondition_gates_pass"] = _pg_pass
                                if not _pg_pass:
                                    SESSION["precondition_gates_blocked"] = _pg_reason
                                    self._send_json(
                                        {
                                            "error": {
                                                "type": "precondition_failed",
                                                "message": f"Request blocked by precondition gate: {_pg_reason}",
                                            }
                                        },
                                        status=422,
                                    )
                                    return
                        except Exception as _pg_err:
                            SESSION["precondition_gates_error"] = str(_pg_err)
                            pass  # fail-open

                    # Phase 0.2: Budget Controller — enforce token budget limits before processing
                    # PERF OPT: use singleton BudgetController (avoids per-request import + init)
                    if BUDGET_CONTROLLER_ENABLED and body:
                        try:
                            from tokenpak._internal.budget_controller import ClassificationResult, IntentClass

                            _bc = _get_budget_controller()
                            _bc_tokens = input_tokens or 0
                            _bc_class = ClassificationResult(
                                intent=IntentClass.GEN_Q,
                                complexity_score=min(_bc_tokens / 10000.0, 1.0),
                            )
                            _bc_decision = _bc.decide(_bc_class)
                            SESSION["budget_controller_tier"] = str(_bc_class.intent.name)
                            SESSION["budget_controller_action"] = (
                                _bc_decision.action
                                if hasattr(_bc_decision, "action")
                                else str(_bc_decision)
                            )
                            if hasattr(_bc_decision, "reject") and _bc_decision.reject:
                                self._send_json(
                                    {
                                        "error": {
                                            "type": "budget_exceeded",
                                            "message": f"Request exceeds token budget: {_bc_tokens} tokens",
                                        }
                                    },
                                    status=429,
                                )
                                return
                        except Exception as _bc_err:
                            SESSION["budget_controller_error"] = str(_bc_err)
                            pass  # fail-open

                    # Phase 0.25: DLP Scanner — scan outbound prompt for secrets/PII before forwarding
                    if DLP_ENABLED and body:
                        try:
                            from tokenpak.security.dlp import DLPBlockError, DLPScanner

                            _dlp = DLPScanner()
                            _dlp_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
                            if _dlp.mode == "block":
                                if not _dlp.block_check(_dlp_text):
                                    _dlp_findings = _dlp.scan(_dlp_text)
                                    SESSION["dlp_blocked"] = [(f.rule_id, f.severity) for f in _dlp_findings]
                                    print(f"  🚫 DLP block: {len(_dlp_findings)} secret(s) detected")
                                    self._send_json(
                                        {
                                            "error": {
                                                "type": "dlp_blocked",
                                                "message": f"Request blocked by DLP scanner: {len(_dlp_findings)} secret(s) detected",
                                            }
                                        },
                                        status=400,
                                    )
                                    return
                            elif _dlp.mode == "redact":
                                _dlp_redacted = _dlp.redact(_dlp_text)
                                if _dlp_redacted != _dlp_text:
                                    body = _dlp_redacted.encode("utf-8") if isinstance(body, bytes) else _dlp_redacted
                                    SESSION["dlp_redacted"] = True
                                    print("  🔒 DLP redact: secrets replaced in outbound body")
                            else:  # warn (default)
                                _dlp_findings = _dlp.scan(_dlp_text)
                                if _dlp_findings:
                                    SESSION["dlp_findings"] = [(f.rule_id, f.severity) for f in _dlp_findings]
                                    print(f"  ⚠️ DLP warn: {[(f.rule_id, f.severity) for f in _dlp_findings]}")
                        except Exception as _dlp_err:
                            SESSION["dlp_error"] = str(_dlp_err)
                            pass  # fail-open

                    # Phase 0.3: DeterministicRouter — intent classification + compression pipeline
                    _intent_for_contract: str = "query"
                    if ROUTER_ENABLED and not _transparent_mode:
                        try:
                            _session_id_router = _resolve_session_id(self.headers, model)
                            body, _router_meta = _run_router(body, session_id=_session_id_router)
                            router_meta = _router_meta
                            if _router_meta and not _router_meta.get("fallback"):
                                _intent_for_contract = _router_meta.get("intent", "query")
                                _audit_rules.append("router")
                                print(
                                    f"  🔀 Router: intent={_router_meta.get('intent','?')} recipe={_router_meta.get('recipe_used','?')} ({_router_meta.get('total_ms',0)}ms)"
                                )
                        except Exception as _router_err:
                            print(f"  ⚠️ Router stage error (skipping): {_router_err}")

                    # Phase 0.4: Context contract enforcement — quota + scope + omission
                    try:
                        from tokenpak.agent.proxy.intent_policy import (
                            resolve_policy as _resolve_policy,
                        )

                        _contract_policy = _resolve_policy(_intent_for_contract, {}, 1.0)
                        _, _pre_contract_tokens = extract_request_tokens(
                            body, adapter=active_adapter
                        )
                        if _pre_contract_tokens > _contract_policy.context_quota:
                            # Soft-cap: log quota violation; hard truncation handled by compaction
                            print(
                                f"  📋 Contract: intent={_intent_for_contract} quota={_contract_policy.context_quota} tokens={_pre_contract_tokens} ceiling={_contract_policy.reasoning_ceiling}"
                            )
                    except Exception as _contract_err:
                        pass  # fail-open: contract enforcement is advisory

                    # Phase 0.5: Capsule builder — compress historical context blocks
                    if CAPSULE_BUILDER is not None and ENABLE_CAPSULE_BUILDER and not _transparent_mode:
                        t_capsule = time.time()
                        capsule_stage = StageTrace(
                            name="capsule",
                            enabled=True,
                            input_tokens=input_tokens,
                        )
                        try:
                            body, _cap_stats = CAPSULE_BUILDER.process(body)
                            _cap_blocks = _cap_stats.get("blocks_capsulized", 0)
                            _cap_ratio = _cap_stats.get("ratio", 1.0)
                            _cap_chars_in = _cap_stats.get("chars_in", 0)
                            _cap_chars_out = _cap_stats.get("chars_out", 0)
                            capsule_stage.details["blocks_capsulized"] = _cap_blocks
                            capsule_stage.details["compression_ratio"] = _cap_ratio
                            capsule_stage.details["chars_in"] = _cap_chars_in
                            capsule_stage.details["chars_out"] = _cap_chars_out
                            capsule_stage.details["skip_reason"] = _cap_stats.get("skip_reason")
                            if _cap_blocks > 0:
                                # Recount tokens after capsulisation
                                _, input_tokens = extract_request_tokens(
                                    body, adapter=active_adapter
                                )
                                _audit_rules.append("capsule_compress")
                                _audit_cache_risk = "high"
                                print(
                                    f"  💊 Capsule: {_cap_blocks} block(s) compressed "
                                    f"({_cap_chars_in}→{_cap_chars_out} chars, ratio={_cap_ratio})"
                                )
                            capsule_stage.output_tokens = input_tokens
                            capsule_stage.tokens_delta = (
                                capsule_stage.output_tokens - capsule_stage.input_tokens
                            )
                        except Exception as _cap_err:
                            print(f"  ⚠️  Capsule builder error (skipping): {_cap_err}")
                            capsule_stage.details["error"] = str(_cap_err)
                            capsule_stage.output_tokens = input_tokens
                        capsule_stage.duration_ms = (time.time() - t_capsule) * 1000
                        if trace:
                            trace.stages.append(capsule_stage)

                    # Phase 0.6: Prefix Registry — track stable system message prefixes
                    if PREFIX_REGISTRY_ENABLED and body:
                        try:
                            from tokenpak.cache.prefix_registry import StablePrefixRegistry

                            _prefix_reg = StablePrefixRegistry()
                            # PERF OPT: reuse req_data parsed earlier instead of re-parsing body
                            _prefix_body = req_data if req_data is not None else json.loads(body)
                            _sys_msgs = [
                                m
                                for m in _prefix_body.get("messages", [])
                                if m.get("role") == "system"
                            ]
                            if _sys_msgs:
                                _prefix_text = _sys_msgs[0].get("content", "")[
                                    :200
                                ]  # first 200 chars
                                _prefix_hash = hash(_prefix_text)
                                _prefix_meta = _prefix_reg.get_or_create(_prefix_hash, _prefix_text)
                                SESSION["prefix_registry_registered"] = True
                                SESSION["prefix_registry_hash"] = _prefix_hash
                        except Exception as _pr_err:
                            SESSION["prefix_registry_error"] = str(_pr_err)
                            pass  # fail-open

                    # CANONICAL PROMPT ORDER (do not reorder — prefix caching depends on stability):
                    # 1. System policy blocks   (stable — never changes between requests)
                    # 2. Tool/function schemas  (stable — normalized to byte-identical JSON by Phase -1)
                    # 3. Injected vault context (stable during session; appended AFTER stable prefix)
                    # 4. Conversation summary   (if trimming applied; chronological)
                    # 5. Conversation history   (user/assistant turns, chronological insertion order)
                    # 6. Current user turn      (most recent user message)
                    # 7. Volatile metadata      (timestamps, session IDs — stripped below by Phase 0.9)
                    #
                    # All pipeline stages that modify content preserve insertion order (verified):
                    #   Phase -1  : Tool Schema Registry — normalizes tool JSON, no message reorder
                    #   Phase 0.5 : CapsuleBuilder — in-place content mod, iterates by index
                    #   Phase 0.9 : Cache Poison Removal — scrubs dynamic tokens in-place (below)
                    #   Phase 1   : Vault injection — appends AFTER existing stable system blocks
                    #   Phase 1.5 : CANON dedup — block dedup preserves list insertion order
                    #   Phase 1.7 : QueryRewriter — rewrites user content in-place, same list order
                    #   Phase 1.8 : Salience Router — modifies content in-place, same list order
                    #   Phase 2   : Compaction — compact_request_body iterates by index, in-place
                    #   Phase 2.1 : Compression Dictionary — apply() returns new list in original order
                    #
                    # Phase 0.9: Cache Poison Removal — strip dynamic UUIDs, timestamps, heartbeat counters
                    # Must run BEFORE stable cache control so the stable prefix stays bit-identical
                    if body and not _transparent_mode:
                        _pre_poison_body = body
                        body = _strip_cache_poisons(body)
                        cache_poison_scrubbed = body != _pre_poison_body
                        if cache_poison_scrubbed:
                            _audit_rules.append("cache_poison_scrub")

                    # Compression budget check — if capsule took too long, skip remaining pipeline
                    if _compression_budget_exceeded():
                        print(
                            f"  ⏱️  Compression budget exceeded ({MAX_COMPRESSION_TIME_MS}ms) after capsule stage — "
                            f"skipping vault+compaction, forwarding original body"
                        )
                        SESSION["compression_timeouts"] += 1
                        body = _original_body
                        raise _CompressionTimeout()

                    # Phase 1: Vault context injection (BEFORE compaction)
                    t_inject = time.time()
                    # Vault index reload is handled by _vault_index_reload_timer (background timer)
                    # No per-request thread spawn needed
                    vault_stage = StageTrace(
                        name="vault_injection",
                        enabled=VAULT_INDEX.available,
                        input_tokens=input_tokens,
                    )
                    if VAULT_INDEX.available and not _transparent_mode:
                        skip_injection = False
                        if INJECT_SKIP_MODELS.strip():
                            if any(
                                skip.strip() and skip.strip().lower() in model.lower()
                                for skip in INJECT_SKIP_MODELS.split(",")
                            ):
                                skip_injection = True
                        if input_tokens < INJECT_MIN_PROMPT:
                            skip_injection = True
                        if skip_injection:
                            SESSION["injection_skips"] += 1
                            vault_stage.details["skipped"] = True
                            vault_stage.details["reason"] = (
                                "model_skip"
                                if INJECT_SKIP_MODELS.strip()
                                and any(
                                    s.lower() in model.lower()
                                    for s in INJECT_SKIP_MODELS.split(",")
                                )
                                else "prompt_too_short"
                            )
                            # Even when skipping vault injection, apply cache_control to stable prefix
                            # Guard: only Anthropic supports cache_control markers
                            if PROMPT_BUILDER_AVAILABLE and detect_provider(target_url) is Provider.ANTHROPIC:
                                body = _apply_stable_cache_control(body)
                                _audit_rules.append("cache_control_stamp")
                                if _audit_cache_risk == "none":
                                    _audit_cache_risk = "low"
                        else:
                            body, injected_tokens, injected_sources = inject_vault_context(
                                body, adapter=active_adapter
                            )
                            if injected_tokens > 0:
                                # Recount tokens after injection
                                _, input_tokens = extract_request_tokens(
                                    body, adapter=active_adapter
                                )
                                _audit_rules.append("vault_inject")
                                if _audit_cache_risk in ("none", "low"):
                                    _audit_cache_risk = "medium"
                                vault_stage.tokens_delta = injected_tokens
                                vault_stage.details["blocks_matched"] = len(injected_sources)
                                vault_stage.details["block_names"] = injected_sources[:5]  # Top 5
                                vault_stage.details["tokens_injected"] = injected_tokens
                                # Enrich with sub-step timing from inject_vault_context
                                vault_stage.details["sub_timing_ms"] = SESSION.get(
                                    "vault_last_timing_ms", {}
                                )
                    vault_stage.output_tokens = input_tokens
                    vault_stage.duration_ms = (time.time() - t_inject) * 1000
                    if trace:
                        trace.stages.append(vault_stage)

                    # Phase 1.2: Retrieval Watchdog — monitor vault injection quality
                    if RETRIEVAL_WATCHDOG_ENABLED and injected_tokens > 0:
                        try:
                            from tokenpak.agent.regression.retrieval_watchdog import (
                                QueryRetrievalRecord,
                                RetrievalQualityWatchdog,
                            )

                            _rw = RetrievalQualityWatchdog()
                            _rw_chunk_count = len(injected_sources) if injected_sources else 0
                            _rw_record = QueryRetrievalRecord(
                                query_id=model or "unknown",
                                query_text=_extract_user_text(
                                    body
                                    if isinstance(body, bytes)
                                    else body.encode("utf-8")
                                    if isinstance(body, str)
                                    else b""
                                )[:200],
                                chunk_count=_rw_chunk_count,
                                unique_chunk_count=_rw_chunk_count,
                                relevance_scores=[1.0] * _rw_chunk_count,
                                source_ids=injected_sources if injected_sources else [],
                                chunk_ids_ordered=[f"chunk_{i}" for i in range(_rw_chunk_count)],
                            )
                            _rw_alert = _rw.observe(_rw_record)
                            if _rw_alert:
                                SESSION["retrieval_watchdog_alert"] = str(_rw_alert)
                        except Exception as _rw_err:
                            SESSION["retrieval_watchdog_error"] = str(_rw_err)
                            pass  # fail-open

                    # Phase 1.5: CANON dedup (AFTER injection, BEFORE compaction)
                    if CANON_AVAILABLE and injected_tokens > 0:
                        t_canon = time.time()
                        canon_stage = StageTrace(
                            name="canon_dedup",
                            enabled=True,
                            input_tokens=input_tokens,
                        )
                        try:
                            session_id = _resolve_session_id(self.headers, model)
                            body, canon_refs, canon_saved = apply_canon_refs(body, session_id)
                            if canon_refs > 0:
                                SESSION["canon_hits"] += canon_refs
                                SESSION["canon_tokens_saved"] += canon_saved
                                canon_stage.tokens_delta = -canon_saved
                                canon_stage.details["blocks_referenced"] = canon_refs
                                canon_stage.details["tokens_saved"] = canon_saved
                                _, input_tokens = extract_request_tokens(
                                    body, adapter=active_adapter
                                )
                        except Exception as _canon_err:
                            canon_stage.details["error"] = str(_canon_err)
                        canon_stage.output_tokens = input_tokens
                        canon_stage.duration_ms = (time.time() - t_canon) * 1000
                        if trace:
                            trace.stages.append(canon_stage)

                    # Phase 1.8: Salience Router — content-type-aware extraction before compaction
                    if SALIENCE_ROUTER_ENABLED and body and not _transparent_mode:
                        try:
                            from tokenpak.agent.compression.salience.router import (
                                detect_content_type,
                            )
                            from tokenpak.agent.compression.salience.router import (
                                extract as salience_extract,
                            )

                            _req_data = json.loads(body)
                            _salience_applied = 0
                            for _msg in _req_data.get("messages", []):
                                _content = _msg.get("content", "")
                                if isinstance(_content, str) and len(_content) > 500:
                                    _ctype = detect_content_type(_content)
                                    if _ctype.value != "unknown":
                                        _result = salience_extract(_content, content_type=_ctype)
                                        if _result.compressed and len(_result.compressed) < len(
                                            _content
                                        ):
                                            _msg["content"] = _result.compressed
                                            _salience_applied += 1
                            if _salience_applied > 0:
                                body = json.dumps(_req_data, separators=(",", ":"))
                                SESSION["salience_router_applied"] = _salience_applied
                                _audit_rules.append("salience_route")
                        except Exception as _sr_err:
                            SESSION["salience_router_error"] = str(_sr_err)
                            pass  # fail-open

                    # Phase 1.7: Query Rewriter — optimize messages for compression/clarity
                    if QUERY_REWRITER_ENABLED and body and not _transparent_mode:
                        try:
                            from tokenpak.agent.compression.query_rewriter import QueryRewriter

                            _qr = QueryRewriter()
                            _req_data = json.loads(body)
                            _rewritten = _qr.rewrite_messages(_req_data.get("messages", []))
                            if _rewritten and _rewritten != _req_data.get("messages", []):
                                _req_data["messages"] = _rewritten
                                body = json.dumps(_req_data, separators=(",", ":"))
                                SESSION["query_rewriter_applied"] = len(_rewritten)
                                _audit_rules.append("query_rewrite")
                        except Exception as _qr_err:
                            SESSION["query_rewriter_error"] = str(_qr_err)
                            pass  # fail-open

                    # Phase 1.9: Fidelity Tiers — select compression level based on budget/complexity
                    if FIDELITY_TIERS_ENABLED and body:
                        try:
                            from tokenpak.agent.compression.fidelity_tiers import (
                                TierSelector,
                            )

                            _ts = TierSelector()
                            _complexity = min(
                                1.0, (input_tokens or 0) / 10000.0
                            )  # simple heuristic
                            _budget_remaining = max(0.0, 1.0 - _complexity)
                            _selected_tier = _ts.select(_complexity, _budget_remaining)
                            SESSION["fidelity_tier"] = (
                                _selected_tier.name
                                if hasattr(_selected_tier, "name")
                                else str(_selected_tier)
                            )
                        except Exception as _ft_err:
                            SESSION["fidelity_tier_error"] = str(_ft_err)
                            pass  # fail-open

                    # Plugin system — run custom compressors first
                    if _plugin_registry is not None and body and not _transparent_mode:
                        _plugin_context = {
                            "mode": COMPILATION_MODE,
                            "input_tokens": input_tokens,
                            "request_id": SESSION.get("request_id", ""),
                        }
                        for _plugin in _plugin_registry.get_plugins():
                            try:
                                _req_data = json.loads(body)
                                for _msg in _req_data.get("messages", []):
                                    _content = _msg.get("content", "")
                                    if isinstance(_content, str):
                                        _plugin_result = _plugin.compress(_content, _plugin_context)
                                        _msg["content"] = _plugin_result["text"]
                                body = json.dumps(_req_data, separators=(",", ":"))
                                _audit_rules.append("plugin_compress")
                            except Exception as _plugin_run_err:
                                import logging as _logging

                                _logging.getLogger(__name__).warning(
                                    "Plugin '%s' raised an error: %s — skipping",
                                    getattr(_plugin, "name", repr(_plugin)),
                                    _plugin_run_err,
                                )

                    # Compression budget check — if vault injection took too long, skip compaction
                    if _compression_budget_exceeded():
                        print(
                            f"  ⏱️  Compression budget exceeded ({MAX_COMPRESSION_TIME_MS}ms) after vault injection — "
                            f"skipping compaction, forwarding as-is"
                        )
                        SESSION["compression_timeouts"] += 1
                        raise _CompressionTimeout()

                    # Phase 2: Compaction (AFTER injection)
                    t_compact = time.time()
                    compaction_stage = StageTrace(
                        name="compaction",
                        enabled=ENABLE_COMPACTION,
                        input_tokens=input_tokens,
                    )
                    if ENABLE_COMPACTION and not _transparent_mode:
                        body, sent_input_tokens, original_tokens, protected_tokens = (
                            compact_request_body(
                                body,
                                adapter=active_adapter,
                            )
                        )
                        if original_tokens > 0:
                            input_tokens = original_tokens
                        if original_tokens > sent_input_tokens:
                            _audit_rules.append("compact")
                            _audit_cache_risk = "high"
                        compaction_stage.output_tokens = sent_input_tokens
                        compaction_stage.tokens_delta = (
                            -(original_tokens - sent_input_tokens) if original_tokens else 0
                        )
                        compaction_stage.details["mode"] = COMPILATION_MODE
                        compaction_stage.details["protected_tokens"] = protected_tokens
                        compaction_stage.details["tokens_removed"] = (
                            max(0, original_tokens - sent_input_tokens) if original_tokens else 0
                        )
                    else:
                        sent_input_tokens = input_tokens
                        compaction_stage.output_tokens = sent_input_tokens
                    compaction_stage.duration_ms = (time.time() - t_compact) * 1000
                    if trace:
                        trace.stages.append(compaction_stage)
                    # Phase 2.1: Compression Dictionary — apply learned compression terms post-standard-compaction
                    if COMPRESSION_DICT_ENABLED and body and not _transparent_mode:
                        try:
                            from tokenpak.agent.compression.dictionary import CompressionDictionary

                            _dict = CompressionDictionary()
                            _req_data = json.loads(body)
                            if "messages" in _req_data:
                                _dict_result = _dict.apply(_req_data["messages"])
                                _req_data["messages"] = _dict_result.messages
                                body = json.dumps(_req_data, separators=(",", ":"))
                                SESSION["compression_dict_applied"] = True
                                _audit_rules.append("dict_compress")
                        except Exception as _cd_err:
                            SESSION["compression_dict_error"] = str(_cd_err)
                            pass  # fail-open

                    # Workflow: vault_inject done → compress done → begin forward
                    if _wf_id:
                        try:
                            from tokenpak.agent.agentic.proxy_workflow import advance_step

                            advance_step(_wf_id, "vault_inject", "compress")
                            advance_step(_wf_id, "compress", "forward")
                        except Exception:
                            pass
                else:
                    sent_input_tokens = input_tokens
            except _CompressionTimeout:
                # Budget exceeded — body already set to best available state; just re-sync tokens
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                sent_input_tokens = input_tokens
            except Exception as _pipeline_err:
                print(f"  ⚠️ Pre-pipeline error (falling back to original body): {_pipeline_err}")
                body = _original_body  # restore original body so request still forwards
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                sent_input_tokens = input_tokens

        final_request_body_for_cache_reason = body
        # CCG-06: capture body bytes after all mutation stages are complete
        _body_post_audit = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else b"")

        # Final validation gate (pre-forward): budget, deterministic context, fingerprint, dry-run
        if should_log and is_messages and body and active_adapter.source_format != "passthrough":
            gate = _get_validation_gate()
            if gate is not None:
                try:
                    gate_result = gate.validate_request(
                        request_body=body,
                        model=model,
                        input_tokens=sent_input_tokens or input_tokens,
                        router_meta=router_meta or {},
                    )
                    if gate_result.fingerprint:
                        print(f"  🧾 Determinism fingerprint: {gate_result.fingerprint}")
                    if not gate_result.valid:
                        if VALIDATION_GATE_SOFT:
                            # Soft mode: log warning but forward request anyway
                            print(
                                f"  ⚠️ Validation gate SOFT-BLOCK (forwarding): {gate_result.errors}"
                            )
                            SESSION["validation_gate_soft_block"] = gate_result.errors
                        else:
                            self._send_json(
                                {
                                    "error": {
                                        "type": "validation_gate_failed",
                                        "message": "Request blocked by validation gate",
                                        "reasons": gate_result.errors,
                                    },
                                    "warnings": gate_result.warnings,
                                    "fingerprint": gate_result.fingerprint,
                                },
                                status=422,
                            )
                            return
                    if gate_result.dry_run:
                        self._send_json(
                            {
                                "status": "dry_run",
                                "message": "Validation gate accepted request; upstream forward skipped",
                                "plan": gate_result.plan,
                                "fingerprint": gate_result.fingerprint,
                                "warnings": gate_result.warnings,
                            },
                            status=200,
                        )
                        return
                except Exception as _gate_err:
                    print(f"  ⚠️ Validation gate error (fail-open): {_gate_err}")

        # CCG-04: Per-route header allowlist on the HTTP path.
        # Anthropic routes use an explicit allowlist (mirroring the WS-path at
        # proxy.py:~7345).  All other providers keep the existing blocklist path
        # (_sanitize_headers) — their forwarding behavior is unchanged.
        if detect_provider(target_url) is Provider.ANTHROPIC:
            _route = _classify_route(self.path, self.headers)
            _allowlist = (
                CLAUDE_CODE_HEADER_ALLOWLIST
                if _route == "claude-code"
                else OPENCLAW_HEADER_ALLOWLIST
            )
            fwd_headers = {}
            for _hk, _hv in self.headers.items():
                if _hk.lower() in _allowlist:
                    fwd_headers[_hk.lower()] = _hv
        else:
            fwd_headers = _sanitize_headers(self.headers)
        fwd_headers["Host"] = parsed.netloc
        if sent_input_tokens == 0:
            sent_input_tokens = input_tokens
        if body is not None:
            fwd_headers["Content-Length"] = str(len(body))

        _req_headers_lower = {k.lower(): v for k, v in self.headers.items()}
        _client_has_auth = bool(
            _req_headers_lower.get("x-api-key", "").strip()
            or _req_headers_lower.get("authorization", "").strip()
        )
        _current_key_idx: int = -1  # tracks which key is injected (for failover)
        # Anthropic auth injection — single-tenant proxy: always override
        # client auth for Anthropic targets. Source priority:
        # 1. Env key pool  2. Claude CLI token (~/.claude/.credentials.json)
        if detect_provider(target_url) is Provider.ANTHROPIC:
            _pool_key = ""
            if _ANTHROPIC_KEY_POOL:
                _pool_key, _current_key_idx = _get_next_key()
            if not _pool_key:
                _pool_key = _load_claude_cli_token()
            if _pool_key:
                fwd_headers["x-api-key"] = _pool_key
                for _k in ("Authorization", "authorization"):
                    fwd_headers.pop(_k, None)

        # ChatGPT Codex OAuth injection
        if detect_provider(target_url) is Provider.CODEX:
            _codex_token, _codex_account = _load_codex_credentials()
            if _codex_token:
                fwd_headers["Authorization"] = f"Bearer {_codex_token}"
                for _k in ("x-api-key", "X-Api-Key", "X-API-Key"):
                    fwd_headers.pop(_k, None)
                if _codex_account:
                    fwd_headers["chatgpt-account-id"] = _codex_account
                fwd_headers.setdefault("OpenAI-Beta", "responses=experimental")
                fwd_headers.setdefault("originator", "codex_cli_rs")
                fwd_headers.setdefault("Accept", "text/event-stream")

        # Fix #5: Check per-provider circuit breaker before attempting upstream
        _cb_provider = _provider_for_url(target_url)
        if _circuit_check(_cb_provider):
            self._send_json(
                {
                    "error": {
                        "type": "circuit_open",
                        "message": f"Provider {_cb_provider} circuit is open — too many recent failures. Retry in 60s.",
                    }
                },
                status=503,
            )
            return

        try:
            path = parsed.path
            if parsed.query:
                path += "?" + parsed.query
            # DEBUG: count cache_control blocks before cap
            try:
                _dbg_body = (
                    json.loads(body)
                    if isinstance(body, bytes)
                    else json.loads(body.encode() if isinstance(body, str) else body)
                )
                _cc_locs = []
                for _si, _sb in enumerate(_dbg_body.get("system", [])):
                    if isinstance(_sb, dict) and "cache_control" in _sb:
                        _cc_locs.append(f"system[{_si}]")
                for _mi, _mm in enumerate(_dbg_body.get("messages", [])):
                    _mc = _mm.get("content", [])
                    if isinstance(_mc, list):
                        for _ci, _cb in enumerate(_mc):
                            if isinstance(_cb, dict) and "cache_control" in _cb:
                                _cc_locs.append(f"msg[{_mi}].content[{_ci}]")
                if _cc_locs:
                    print(f"  🔍 cache_control blocks BEFORE cap: {len(_cc_locs)} at {_cc_locs}")
            except Exception as _e:
                print(f"  🔍 debug error: {_e}")
            body = _strip_empty_text_blocks(body)
            # CACHE-P4-001: CacheSpec unified mode resolution (replaces ad-hoc provider guards)
            _detected_provider = detect_provider(target_url)
            _cache_hint: Optional[str] = None

            # Extract Anthropic-specific request hint and translate to CacheSpec mode names.
            # _select_anthropic_cache_mode pops tokenpak_cache_mode from body (side-effect).
            if _detected_provider is Provider.ANTHROPIC:
                try:
                    _ac_body = json.loads(body)
                    _ac_headers = {k.lower(): v for k, v in self.headers.items()}
                    _raw_ac_mode = _select_anthropic_cache_mode(_ac_headers, _ac_body)
                    _cache_hint = (
                        "prefix_auto" if _raw_ac_mode is CacheMode.AUTO else "block_explicit"
                    )
                    body = json.dumps(_ac_body).encode()
                except Exception as _hint_err:
                    print(f"  ⚠️ Cache hint extraction error: {_hint_err}", flush=True)
                    _cache_hint = "block_explicit"

            # Resolve effective mode via CacheSpec (request hint > config override > provider default)
            _effective_mode = (
                _resolve_cache_mode(CACHE_SPEC, _detected_provider, _cache_hint)
                if _CACHE_SPEC_AVAILABLE and CACHE_SPEC is not None
                else None
            )

            # Dispatch based on resolved mode
            if _CACHE_SPEC_AVAILABLE and _effective_mode is _CacheSpecMode.BLOCK_EXPLICIT:
                if _detected_provider is Provider.ANTHROPIC:
                    try:
                        _ac_body = json.loads(body)
                        body = json.dumps(_ac_body).encode()
                        body = _cap_cache_control_blocks(body)  # Provider.ANTHROPIC guard at block entry
                        print("  📦 Anthropic cache mode: EXPLICIT (per-block, capped)", flush=True)
                    except Exception as _ac_err:
                        print(f"  ⚠️ Cache mode error (falling back to cap): {_ac_err}", flush=True)
                        body = _cap_cache_control_blocks(body)  # Provider.ANTHROPIC guard at block entry
            elif _CACHE_SPEC_AVAILABLE and _effective_mode is _CacheSpecMode.PREFIX_AUTO:
                if _detected_provider is Provider.ANTHROPIC:
                    try:
                        _ac_body = json.loads(body)
                        _apply_anthropic_auto_cache(_ac_body)
                        body = json.dumps(_ac_body).encode()
                        print("  🔄 Anthropic cache mode: AUTO (top-level ephemeral)", flush=True)
                    except Exception as _ac_err:
                        print(f"  ⚠️ Auto cache mode error: {_ac_err}", flush=True)
            elif _CACHE_SPEC_AVAILABLE and _effective_mode is _CacheSpecMode.CACHE_OBJECT:
                body = _inject_gemini_cache_ref(_detected_provider, dict(self.headers), body)
            elif _CACHE_SPEC_AVAILABLE and _effective_mode is _CacheSpecMode.CHECKPOINT:
                body = _inject_bedrock_checkpoints(_detected_provider, body)
            else:
                # None: disabled, provider-default, or CacheSpec unavailable.
                # Preserve original per-provider behavior for backward compatibility.
                if _detected_provider is Provider.ANTHROPIC:
                    try:
                        _ac_body = json.loads(body)
                        _ac_headers = {k.lower(): v for k, v in self.headers.items()}
                        _fb_mode = _select_anthropic_cache_mode(_ac_headers, _ac_body)
                        if _fb_mode is CacheMode.AUTO:
                            _apply_anthropic_auto_cache(_ac_body)
                            body = json.dumps(_ac_body).encode()
                        else:
                            body = json.dumps(_ac_body).encode()
                            body = _cap_cache_control_blocks(body)  # Provider.ANTHROPIC guard at block entry
                    except Exception as _fb_err:
                        body = _cap_cache_control_blocks(body)  # Provider.ANTHROPIC guard at block entry
                elif _detected_provider is Provider.GEMINI:
                    body = _inject_gemini_cache_ref(_detected_provider, dict(self.headers), body)
                elif _detected_provider is Provider.BEDROCK:
                    body = _inject_bedrock_checkpoints(_detected_provider, body)

            # Always run prompt_cache_key: injects for OpenAI/Azure/Codex/xAI,
            # strips tokenpak_* fields from all providers (CACHE-P2-001).
            body = _inject_prompt_cache_key(_detected_provider, dict(self.headers), body)

            # Fix Content-Length after cache processing may have changed body size
            if isinstance(body, str):
                body = body.encode("utf-8")
            if body is not None:
                fwd_headers["Content-Length"] = str(len(body))
            # DEBUG: count cache_control blocks
            try:
                _dbody = json.loads(body) if isinstance(body, (bytes, str)) else body
                _cc = 0
                for _s in _dbody.get("system") or []:
                    if isinstance(_s, dict) and "cache_control" in _s:
                        _cc += 1
                for _m in _dbody.get("messages") or []:
                    for _c in (
                        (_m.get("content") or []) if isinstance(_m.get("content"), list) else []
                    ):
                        if isinstance(_c, dict) and "cache_control" in _c:
                            _cc += 1
                if _cc > 0:
                    print(f"  📦 cache_control blocks in request: {_cc}", flush=True)
                if _cc > 4:
                    print(
                        f"  ⚠️ OVER LIMIT! Stripping {_cc - 4} earliest cache_control blocks",
                        flush=True,
                    )
                    _locs = []
                    for _i, _s in enumerate((_dbody.get("system") or [])):
                        if isinstance(_s, dict) and "cache_control" in _s:
                            _locs.append(("s", _i))
                    for _mi, _m in enumerate((_dbody.get("messages") or [])):
                        for _ci, _c in enumerate(
                            (_m.get("content") or []) if isinstance(_m.get("content"), list) else []
                        ):
                            if isinstance(_c, dict) and "cache_control" in _c:
                                _locs.append(("m", _mi, _ci))
                    for _loc in _locs[: (_cc - 4)]:
                        if _loc[0] == "s":
                            _dbody["system"][_loc[1]].pop("cache_control", None)
                        else:
                            _dbody["messages"][_loc[1]]["content"][_loc[2]].pop(
                                "cache_control", None
                            )
                    body = json.dumps(_dbody).encode()
                    print(
                        f'  ✅ Stripped. Now {sum(1 for s in (_dbody.get("system") or []) if isinstance(s,dict) and "cache_control" in s) + sum(1 for m in (_dbody.get("messages") or []) for c in (m.get("content") or []) if isinstance(c,dict) and "cache_control" in c)} blocks',
                        flush=True,
                    )
            except Exception as _e:
                print(f"  ⚠️ cache_control debug error: {_e}", flush=True)
            body = _strip_empty_text_blocks(body)
            # Safety net: second cap for EXPLICIT mode in case injection re-added blocks
            if _detected_provider is Provider.ANTHROPIC and (
                not _CACHE_SPEC_AVAILABLE
                or _effective_mode is _CacheSpecMode.BLOCK_EXPLICIT
            ):
                body = _cap_cache_control_blocks(body)  # Provider.ANTHROPIC guard at block entry

            # 🧹 TTL ordering hotfix v2 (baked into canonical 2026-04-09)
            # Anthropic rejects when a cache_control block with ttl="1h" appears AFTER
            # a default-ttl (5m) block in document order. Strip all default-ttl blocks
            # that appear BEFORE the LAST explicit-ttl block.
            try:
                _hf_dbody = json.loads(body) if isinstance(body, (bytes, str)) else body
                _hf_locs = []
                for _hs in (_hf_dbody.get("system") or []):
                    if isinstance(_hs, dict):
                        _hf_locs.append(_hs)
                for _ht in (_hf_dbody.get("tools") or []):
                    if isinstance(_ht, dict):
                        _hf_locs.append(_ht)
                for _hm in (_hf_dbody.get("messages") or []):
                    _hcontent = _hm.get("content") if isinstance(_hm, dict) else None
                    if isinstance(_hcontent, list):
                        for _hc in _hcontent:
                            if isinstance(_hc, dict):
                                _hf_locs.append(_hc)
                _last_ext_ttl = None
                for _hi, _hb in enumerate(_hf_locs):
                    _hcc = _hb.get("cache_control") if isinstance(_hb, dict) else None
                    if isinstance(_hcc, dict) and _hcc.get("ttl") is not None:
                        _last_ext_ttl = _hi
                if _last_ext_ttl is not None:
                    _hf_stripped = 0
                    for _hi in range(_last_ext_ttl):
                        _hb = _hf_locs[_hi]
                        _hcc = _hb.get("cache_control") if isinstance(_hb, dict) else None
                        if isinstance(_hcc, dict) and _hcc.get("ttl") is None:
                            _hb.pop("cache_control", None)
                            _hf_stripped += 1
                    if _hf_stripped > 0:
                        print(f"  🧹 TTL ordering hotfix v2: stripped {_hf_stripped} default-ttl blocks before last explicit-ttl block", flush=True)
                        body = json.dumps(_hf_dbody).encode()
            except Exception as _e_hf:
                print(f"  ⚠️ ttl ordering hotfix error: {_e_hf}", flush=True)
            if isinstance(body, str):
                body = body.encode("utf-8")
            if body is not None:
                fwd_headers["Content-Length"] = str(len(body))
            # TEMP DEBUG: dump final body to file
            try:
                import json as _j2

                _fb = _j2.loads(body) if isinstance(body, (bytes, str)) else body
                _all_cc = 0
                for _sk in ["system", "tools", "messages"]:
                    items = _fb.get(_sk, [])
                    if isinstance(items, list):
                        for _it in items:
                            if isinstance(_it, dict):
                                if "cache_control" in _it:
                                    _all_cc += 1
                                for _cv in (
                                    _it.get("content", [])
                                    if isinstance(_it.get("content"), list)
                                    else []
                                ):
                                    if isinstance(_cv, dict) and "cache_control" in _cv:
                                        _all_cc += 1
                print(
                    f"  🎯 FINAL body has {_all_cc} cache_control blocks (system+tools+messages)",
                    flush=True,
                )
                if _all_cc > 4:
                    with open("/tmp/debug_body.json", "w") as _df:
                        _j2.dump(_fb, _df, indent=2)
                    print("  ❌ DUMPED to /tmp/debug_body.json", flush=True)
            except Exception as _de:
                print(f"  debug error: {_de}", flush=True)

            # --- Early SSE keepalive ---
            # Send HTTP 200 + SSE headers BEFORE the upstream call when streaming.
            # This prevents OpenClaw from timing out during compression + upstream TTFB.
            # SSE comments (lines starting with ":") are ignored by spec-compliant parsers.
            _early_sse_sent = False
            # Keepalive disabled — causes framing issues with OpenClaw SDK

            _t0_conn = time.monotonic()
            resp = _POOL_MANAGER.request(
                method,
                target_url,
                headers=fwd_headers,
                body=body,
                timeout=urllib3.Timeout(connect=10.0, read=UPSTREAM_TIMEOUT),
                preload_content=False,
            )
            _conn_ms = int((time.monotonic() - _t0_conn) * 1000)
            print(f"  🔌 upstream connect+send: {_conn_ms}ms (pool reuse enabled)", flush=True)
            status = resp.status

            # Key pool failover: retry with next key on 401/429 (only when we injected)
            if (
                status in (401, 429)
                and _current_key_idx >= 0
                and not _client_has_auth
                and len(_ANTHROPIC_KEY_POOL) > 1
            ):
                _cooldown_dur = _KEY_COOLDOWN_401 if status == 401 else _KEY_COOLDOWN_429
                _cool_down_key(_current_key_idx, _cooldown_dur, f"HTTP {status}")
                _retry_key, _retry_idx = _get_next_key(exclude_idx=_current_key_idx)
                if _retry_key:
                    print(
                        f"[key-pool] Key #{_current_key_idx} returned {status}, "
                        f"retrying with key #{_retry_idx}",
                        flush=True,
                    )
                    fwd_headers["x-api-key"] = _retry_key
                    _current_key_idx = _retry_idx
                    try:
                        resp.drain_conn()
                    except Exception:
                        pass
                    _t0_conn = time.monotonic()
                    resp = _POOL_MANAGER.request(
                        method,
                        target_url,
                        headers=fwd_headers,
                        body=body,
                        timeout=urllib3.Timeout(connect=10.0, read=UPSTREAM_TIMEOUT),
                        preload_content=False,
                    )
                    status = resp.status
                    print(
                        f"[key-pool] Retry key #{_retry_idx} → HTTP {status} "
                        f"({int((time.monotonic() - _t0_conn) * 1000)}ms)",
                        flush=True,
                    )

            # CCI-05: Provider failover — only for claude-code-* profiles, only on 5xx or timeout.
            # The fallback chain is opt-in via TOKENPAK_FALLBACK_CHAIN (default: "anthropic" only).
            # Timeout is represented by status == 0 (set in the except block above this try).
            _cc05_profile = SESSION.get("active_profile", "")
            _cc05_is_cc_profile = _cc05_profile.startswith("claude-code-")
            _cc05_triggered = False
            if (
                _cc05_is_cc_profile
                and len(_FALLBACK_CHAIN) > 1
                and (500 <= status <= 599 or status == 0)
            ):
                # Determine which provider originally handled this request
                _cc05_current_provider = "anthropic" if "anthropic.com" in target_url else "other"
                # Walk the chain starting from the provider AFTER the current one
                try:
                    _cc05_chain_start = _FALLBACK_CHAIN.index(_cc05_current_provider) + 1
                except ValueError:
                    _cc05_chain_start = 1  # start from second if current not in chain
                _cc05_reason = "timeout" if status == 0 else f"http_{status}"
                _cc05_model_str = model  # captured earlier in _proxy_to
                for _cc05_next in _FALLBACK_CHAIN[_cc05_chain_start:]:
                    if _cc05_next == "queue":
                        # Last-resort: queue the request and return 202
                        _cc05_row_id = _write_failover_queue(body, _cc05_model_str, _cc05_profile)
                        _log_failover_event(
                            _cc05_current_provider, "queue", _cc05_reason,
                            _cc05_model_str, status, _cc05_profile,
                        )
                        try:
                            resp.drain_conn()
                        except Exception:
                            pass
                        _queue_resp = json.dumps({
                            "type": "queued",
                            "message": "Request queued for retry — provider temporarily unavailable.",
                            "queue_id": _cc05_row_id,
                        }).encode()
                        self.send_response(202)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(_queue_resp)))
                        self.send_header("Retry-After", "60")
                        self.send_header("X-TokenPak-Failover", f"queued:{_cc05_row_id}")
                        self.end_headers()
                        self.wfile.write(_queue_resp)
                        _cc05_triggered = True
                        break
                    # Build URL + headers for next provider
                    _cc05_fb_url = _build_failover_url(_cc05_next, target_url, _cc05_model_str)
                    if not _cc05_fb_url:
                        print(f"[failover] {_cc05_next}: no URL — skipping", flush=True)
                        continue
                    _cc05_fb_headers = _build_failover_headers(_cc05_next, fwd_headers)
                    # Translate model name in request body
                    _cc05_fb_body = body
                    try:
                        if body:
                            _cc05_parsed_body = json.loads(body)
                            _cc05_translated_model = _translate_model(_cc05_model_str, _cc05_next)
                            if _cc05_translated_model != _cc05_parsed_body.get("model", ""):
                                _cc05_parsed_body["model"] = _cc05_translated_model
                                _cc05_fb_body = json.dumps(_cc05_parsed_body).encode()
                                _cc05_fb_headers["Content-Length"] = str(len(_cc05_fb_body))
                    except Exception as _cc05_body_err:
                        print(f"[failover] body translation error: {_cc05_body_err}", flush=True)
                    _log_failover_event(
                        _cc05_current_provider, _cc05_next, _cc05_reason,
                        _cc05_model_str, status, _cc05_profile,
                    )
                    try:
                        resp.drain_conn()
                    except Exception:
                        pass
                    _t0_fb = time.monotonic()
                    resp = _POOL_MANAGER.request(
                        method,
                        _cc05_fb_url,
                        headers=_cc05_fb_headers,
                        body=_cc05_fb_body,
                        timeout=urllib3.Timeout(connect=10.0, read=UPSTREAM_TIMEOUT),
                        preload_content=False,
                    )
                    status = resp.status
                    print(
                        f"[failover] {_cc05_next} → HTTP {status} "
                        f"({int((time.monotonic() - _t0_fb) * 1000)}ms)",
                        flush=True,
                    )
                    if status < 500:
                        # Succeeded — add failover header so user can see it happened
                        _cc05_triggered = True
                        break
                    # Still failing — try next in chain
                    _cc05_current_provider = _cc05_next
            if _cc05_triggered and status == 202:
                return  # 202 queue response already sent

            # Fix #5: Record success/failure for circuit breaker
            if status >= 500:
                _circuit_record_failure(_cb_provider)
            else:
                _circuit_record_success(_cb_provider)
            content_type = resp.getheader("Content-Type", "")
            is_sse = "text/event-stream" in content_type

            # If upstream errored but we already sent 200+SSE headers, emit SSE error event
            if _early_sse_sent and status >= 400:
                try:
                    _err_body = resp.read()
                    _err_event = json.dumps({
                        "type": "error",
                        "error": {"type": "upstream_error", "message": f"HTTP {status}: {_err_body[:500].decode('utf-8', errors='replace')}"}
                    })
                    self.wfile.write(f"event: error\ndata: {_err_event}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

            # Fix #4: Normalize upstream error responses to unified JSON shape
            # Anthropic returns {"type":"error","error":{...},"request_id":"..."}
            # We normalize all 4xx/5xx to {"error":{"type":...,"message":...}}
            _resp_content_type = resp.getheader("Content-Type", "")
            if status >= 400 and "application/json" in _resp_content_type and not is_sse:
                try:
                    _err_raw = resp.read()
                    _err_data = json.loads(_err_raw)
                    # Anthropic shape: {"type":"error","error":{"type":...,"message":...}}
                    if (
                        "type" in _err_data
                        and _err_data.get("type") == "error"
                        and "error" in _err_data
                    ):
                        _inner = _err_data["error"]
                        _normalized = {
                            "error": {
                                "type": _inner.get("type", "upstream_error"),
                                "message": _inner.get("message", ""),
                                "request_id": _err_data.get("request_id", ""),
                            }
                        }
                    # OpenAI shape: {"error":{"message":...,"type":...,"code":...}}
                    elif "error" in _err_data and isinstance(_err_data["error"], dict):
                        _normalized = _err_data  # already correct shape
                    else:
                        _normalized = {
                            "error": {"type": "upstream_error", "message": str(_err_data)}
                        }
                    # Tier 2A: Error Normalizer — further standardize error message text
                    if ERROR_NORMALIZER_ENABLED:
                        try:
                            from tokenpak.agent.agentic.error_normalizer import ErrorNormalizer

                            _en = ErrorNormalizer()
                            _err_msg = _normalized.get("error", {}).get("message", "")
                            if _err_msg:
                                _normalized["error"]["message"] = _en.normalize(_err_msg)
                                SESSION["error_normalizer_applied"] = True
                        except Exception:
                            pass  # fail-open
                    # Tier 2C: Failure Memory — record error signature for future avoidance
                    if FAILURE_MEMORY_ENABLED:
                        try:
                            from tokenpak.agent.agentic.failure_memory import (
                                FailureMemoryDB,
                                FailureSignature,
                            )

                            _fm = FailureMemoryDB()
                            _fm_msg = _normalized.get("error", {}).get("message", "")
                            _fm_type = _normalized.get("error", {}).get("type", "unknown")
                            if _fm_msg and not _fm.match(_fm_msg):
                                _fm.add(
                                    FailureSignature(
                                        error_type=_fm_type, pattern=_fm_msg[:200], model=model
                                    )
                                )
                                SESSION["failure_memory_recorded"] = True
                        except Exception:
                            pass  # fail-open
                    # Actionable error enrichment — add hint/retry_after for key error paths
                    _retry_after_hdr = (
                        resp.getheader("Retry-After", None) if hasattr(resp, "getheader") else None
                    )
                    _normalized = _enrich_upstream_error(_normalized, status, _retry_after_hdr)
                    _err_body = json.dumps(_normalized, indent=2).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", len(_err_body))
                    # Forward Retry-After header for 429 responses
                    if status == 429 and _retry_after_hdr:
                        self.send_header("Retry-After", _retry_after_hdr)
                    self.end_headers()
                    self.wfile.write(_err_body)
                    return
                except Exception:
                    resp = type(
                        "FakeResp",
                        (),
                        {
                            "read": lambda self: _err_raw,
                            "getheaders": lambda self: [],
                            "getheader": lambda self, k, d="": d,
                        },
                    )()

            # HTTP 100 Continue keepalive — send BEFORE response headers if enabled + SSE
            # This signals liveness during compression/upstream delay to prevent client timeouts
            if HTTP100_KEEPALIVE_ENABLED and is_sse and status == 200 and not _early_sse_sent:
                try:
                    self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Client disconnected — fail gracefully
            
            # Skip header sending if we already sent early SSE headers
            if not _early_sse_sent:
                self.send_response(status)
                # urllib3 HTTPResponse uses .headers (HTTPHeaderDict) instead of .getheaders()
                _resp_headers = resp.headers.items() if hasattr(resp, "headers") else resp.getheaders()
                for h_key, h_val in _resp_headers:
                    h_lower = h_key.lower()
                    if h_lower in ("connection", "keep-alive", "transfer-encoding"):
                        continue
                    if h_lower == "content-length":
                        continue
                    self.send_header(h_key, h_val)
                # CCI-10: IDE savings header — inject when claude-code-ide profile active and
                # INLINE_SAVINGS_HEADER_ENABLED. Computed from compression savings (input side only)
                # because cache savings are not yet known at header-send time.
                if (
                    INLINE_SAVINGS_HEADER_ENABLED
                    and status == 200
                    and input_tokens > 0
                ):
                    try:
                        _ide_compression_saved = max(0.0, estimate_cost(model, input_tokens, 0, 0, 0) - estimate_cost(model, sent_input_tokens, 0, 0, 0))
                        self.send_header("X-TokenPak-Savings", f"${_ide_compression_saved:.2f}")
                        _ide_cache_hit_ratio = (
                            round(sent_input_tokens / input_tokens, 2) if input_tokens > 0 else 0.0
                        )
                        self.send_header("X-TokenPak-Cache-Hit", str(_ide_cache_hit_ratio))
                    except Exception:
                        pass  # fail-open — never break response delivery
                self.end_headers()

            if is_sse:
                output_tokens = 0
                sse_buffer = b""
                chunk_count = 0
                early_break = False
                _pending_chunk = b""
                _footer_injected = False
                import zlib as _zlib

                _ce = resp.getheader("Content-Encoding", "")
                _decomp = _zlib.decompressobj(_zlib.MAX_WBITS | 16) if "gzip" in _ce else None
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        # Flush any pending chunk at end of stream
                        if _pending_chunk:
                            try:
                                self.wfile.write(_pending_chunk)
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                            if should_log and is_messages:
                                sse_buffer += _pending_chunk
                        break
                    chunk_count += 1
                    if _decomp:
                        try:
                            chunk = _decomp.decompress(chunk)
                        except Exception:
                            pass
                    if not chunk:
                        continue

                    # Chat footer injection — buffer chunks to find message_stop
                    if CHAT_FOOTER_ENABLED and not _footer_injected and should_log and is_messages:
                        combined = _pending_chunk + chunk
                        _pending_chunk = b""
                        if (
                            b'"type":"message_stop"' in combined
                            or b'"type": "message_stop"' in combined
                        ):
                            try:
                                # Find injection point — right before message_stop event
                                stop_idx = combined.find(b"event: message_stop")
                                if stop_idx == -1:
                                    # Inline format — find the event: line before type:message_stop
                                    ms_idx = combined.find(b'"type":"message_stop"')
                                    if ms_idx == -1:
                                        ms_idx = combined.find(b'"type": "message_stop"')
                                    if ms_idx > 0:
                                        search_back = combined[:ms_idx].rfind(b"event:")
                                        stop_idx = search_back if search_back >= 0 else -1

                                if stop_idx > 0:
                                    before_stop = combined[:stop_idx]
                                    after_stop = combined[stop_idx:]
                                    self.wfile.write(before_stop)
                                    self.wfile.flush()
                                    sse_buffer += before_stop

                                    # Build footer stats
                                    _temp_usage = _extract_sse_tokens(sse_buffer)
                                    _temp_output = _temp_usage.get("output_tokens", 0)
                                    _temp_cache_r = _temp_usage.get("cache_read_input_tokens", 0)
                                    _saved = max(0, input_tokens - sent_input_tokens)
                                    _pct = (
                                        int(100 * _saved / input_tokens) if input_tokens > 0 else 0
                                    )
                                    _cost = estimate_cost(
                                        model, sent_input_tokens, _temp_output, _temp_cache_r, 0
                                    )
                                    _footer_text = f"\n\n───\n📊 {input_tokens:,}→{sent_input_tokens:,} tok (-{_pct}%) | ${_cost:.3f}"
                                    if _temp_cache_r > 0:
                                        _footer_text += f" | cache: {_temp_cache_r:,}r"
                                    # CCI-10: TUI savings tape — extended when INLINE_SAVINGS_ENABLED
                                    if INLINE_SAVINGS_ENABLED:
                                        _compression_saved_usd = max(0.0, estimate_cost(model, input_tokens, 0, 0, 0) - estimate_cost(model, sent_input_tokens, 0, 0, 0))
                                        _cache_saved_usd = estimate_cache_savings(_provider_for_url(target_url), _temp_cache_r, model)
                                        _vault_blocks = int(SESSION.get("vault_blocks_injected", injected_tokens > 0 and 1 or 0))
                                        _footer_text += f"\n💰 saved: ${_compression_saved_usd + _cache_saved_usd:.3f} (cmp ${_compression_saved_usd:.3f} + cache ${_cache_saved_usd:.3f})"
                                        if _vault_blocks:
                                            _footer_text += f" | vault: {_vault_blocks}blk"
                                    _footer_event = {
                                        "type": "content_block_delta",
                                        "index": 0,
                                        "delta": {"type": "text_delta", "text": _footer_text},
                                    }
                                    _footer_sse = f"event: content_block_delta\ndata: {json.dumps(_footer_event)}\n\n".encode()
                                    self.wfile.write(_footer_sse)
                                    self.wfile.flush()
                                    _footer_injected = True

                                    self.wfile.write(after_stop)
                                    self.wfile.flush()
                                    sse_buffer += after_stop
                                    continue
                                else:
                                    # Couldn't find injection point — write combined as-is
                                    self.wfile.write(combined)
                                    self.wfile.flush()
                                    sse_buffer += combined
                                    _footer_injected = True
                                    continue
                            except Exception:
                                # Fail-open — write the chunk normally
                                self.wfile.write(combined)
                                self.wfile.flush()
                                sse_buffer += combined
                                _footer_injected = True
                                continue
                        else:
                            # Buffer one chunk ahead to catch message_stop split across chunks
                            if _pending_chunk:
                                try:
                                    self.wfile.write(_pending_chunk)
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    early_break = True
                                    break
                                if should_log and is_messages:
                                    sse_buffer += _pending_chunk
                            _pending_chunk = combined
                            continue

                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        early_break = True
                        break
                    if should_log and is_messages:
                        sse_buffer += chunk
                if should_log and is_messages:
                    sse_usage = _extract_sse_tokens(sse_buffer)
                    output_tokens = extract_response_tokens(
                        sse_buffer, adapter=active_adapter, is_sse=True
                    )
                    cache_read_tokens = sse_usage.get("cache_read_input_tokens", 0)
                    cache_creation_tokens = sse_usage.get("cache_creation_input_tokens", 0)
                # CCI-10: SSE savings final event — appended after stream close when enabled.
                # Uses event name "tokenpak.savings" (non-Anthropic namespace) to avoid
                # conflicts with Anthropic's own event types. Claude CLI tolerates unknown
                # event types per SSE spec (unknown events are safely ignored).
                # Only fires when INLINE_SAVINGS_ENABLED=true (default OFF; TUI profile sets true).
                if (
                    INLINE_SAVINGS_ENABLED
                    and is_messages
                    and status == 200
                    and not early_break
                ):
                    try:
                        _sse_compression_saved = max(0.0, estimate_cost(model, input_tokens, 0, 0, 0) - estimate_cost(model, sent_input_tokens, 0, 0, 0))
                        _sse_cache_saved = estimate_cache_savings(_provider_for_url(target_url), cache_read_tokens, model)
                        _sse_vault_blocks = int(SESSION.get("vault_blocks_injected", injected_tokens > 0 and 1 or 0))
                        _savings_payload = {
                            "compression_savings_usd": round(_sse_compression_saved, 6),
                            "cache_savings_usd": round(_sse_cache_saved, 6),
                            "total_savings_usd": round(_sse_compression_saved + _sse_cache_saved, 6),
                            "vault_blocks_injected": _sse_vault_blocks,
                            "input_tokens_raw": input_tokens,
                            "input_tokens_sent": sent_input_tokens,
                            "cache_read_tokens": cache_read_tokens,
                        }
                        _savings_event = (
                            f"event: tokenpak.savings\ndata: {json.dumps(_savings_payload)}\n\n"
                        ).encode()
                        self.wfile.write(_savings_event)
                        self.wfile.flush()
                    except Exception:
                        pass  # fail-open — never break delivery
            else:
                resp_body = resp.read()
                output_tokens = 0
                # Chat footer — JSON (non-streaming) injection
                if CHAT_FOOTER_ENABLED and should_log and is_messages and status == 200:
                    try:
                        body_for_parse = resp_body
                        if "gzip" in resp.getheader("Content-Encoding", ""):
                            body_for_parse = gzip.decompress(resp_body)
                        resp_json = json.loads(body_for_parse)
                        usage = resp_json.get("usage", {})
                        _out_tok = usage.get("output_tokens", 0)
                        _cache_r = usage.get("cache_read_input_tokens", 0)
                        _pct = (
                            round((input_tokens - sent_input_tokens) / input_tokens * 100, 1)
                            if input_tokens
                            else 0
                        )
                        _cost = estimate_cost(model, sent_input_tokens, _out_tok, _cache_r, 0)
                        _footer_text = f"\n\n───\n📊 {input_tokens:,}→{sent_input_tokens:,} tok (-{_pct}%) | ${_cost:.3f}"
                        if _cache_r > 0:
                            _footer_text += f" | cache: {_cache_r:,}r"
                        # CCI-10: TUI savings tape extension for non-streaming
                        if INLINE_SAVINGS_ENABLED and SESSION.get("active_profile") == "claude-code-tui":
                            _compression_saved_usd = max(0.0, estimate_cost(model, input_tokens, 0, 0, 0) - estimate_cost(model, sent_input_tokens, 0, 0, 0))
                            _cache_saved_usd = estimate_cache_savings(_provider_for_url(target_url), _cache_r, model)
                            _vault_blocks = int(SESSION.get("vault_blocks_injected", injected_tokens > 0 and 1 or 0))
                            _footer_text += f"\n💰 saved: ${_compression_saved_usd + _cache_saved_usd:.3f} (cmp ${_compression_saved_usd:.3f} + cache ${_cache_saved_usd:.3f})"
                            if _vault_blocks:
                                _footer_text += f" | vault: {_vault_blocks}blk"
                        content = resp_json.get("content", [])
                        if content and isinstance(content, list):
                            for i in range(len(content) - 1, -1, -1):
                                if content[i].get("type") == "text":
                                    content[i]["text"] += _footer_text
                                    break
                            resp_json["content"] = content
                            resp_body = json.dumps(resp_json).encode()
                    except Exception:
                        pass  # fail-open

                # Phase 2.2: Session Capsules — compress and store session context
                if SESSION_CAPSULES_ENABLED and body:
                    try:
                        from tokenpak.agent.memory.session_capsules import (
                            build_session_capsule,
                            serialize_capsule,
                        )

                        _session_id = _resolve_session_id(self.headers, model)
                        _capsule_text = body.decode("utf-8") if isinstance(body, bytes) else body
                        _capsule = build_session_capsule(_capsule_text, source_path=_session_id)
                        _capsule_str = serialize_capsule(_capsule)
                        SESSION["session_capsule_built"] = True
                        SESSION["session_capsule_size"] = len(_capsule_str)
                    except Exception as _sc_err:
                        SESSION["session_capsule_error"] = str(_sc_err)
                        pass  # fail-open

                self.wfile.write(resp_body)
                self.wfile.flush()
                if should_log and is_messages:
                    resp_for_metrics = resp_body
                    if "gzip" in resp.getheader("Content-Encoding", ""):
                        try:
                            resp_for_metrics = gzip.decompress(resp_body)
                        except Exception:
                            pass
                    output_tokens = extract_response_tokens(
                        resp_for_metrics, adapter=active_adapter
                    )
                    try:
                        _resp_json = json.loads(resp_for_metrics)
                        usage = _resp_json.get("usage", {})
                        # Anthropic format: direct fields in usage object
                        cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
                        
                        # OpenAI format: prompt_tokens_details.cached_tokens
                        # Only apply if Anthropic fields not present (avoid double-counting)
                        if cache_read_tokens == 0:
                            prompt_details = usage.get("prompt_tokens_details", {})
                            if prompt_details:
                                openai_cached = prompt_details.get("cached_tokens", 0)
                                if openai_cached and openai_cached > 0:
                                    cache_read_tokens = openai_cached
                                # Log audio_tokens for future use (OpenAI audio model support)
                                audio_tokens = prompt_details.get("audio_tokens", 0)
                                if audio_tokens and audio_tokens > 0:
                                    # Future: store in dedicated column once schema supports it
                                    pass  # Logged for awareness; schema migration needed for storage
                        
                        # Gemini format: usageMetadata.cachedContentTokenCount
                        # Only apply if no cache tokens found from Anthropic/OpenAI formats
                        if cache_read_tokens == 0:
                            gemini_cached = _parse_gemini_cached_tokens(_resp_json)
                            if gemini_cached > 0:
                                cache_read_tokens = gemini_cached
                        
                        # Bedrock format: usage.cacheReadInputTokens (CACHE-P3-003)
                        # Only apply if no cache tokens found from other providers
                        if cache_read_tokens == 0:
                            bedrock_cached = _parse_bedrock_cached_tokens(_resp_json)
                            if bedrock_cached > 0:
                                cache_read_tokens = bedrock_cached
                            # Also check for Bedrock cache creation tokens
                            bedrock_creation = _parse_bedrock_cache_creation_tokens(_resp_json)
                            if bedrock_creation > 0:
                                cache_creation_tokens = bedrock_creation
                    except Exception:
                        pass

            # conn.close()  # REMOVED: urllib3 pool manager, no conn object here

            # Post-request: Store successful response in semantic cache
            if SEMANTIC_CACHE_ENABLED and status == 200 and not SESSION.get("semantic_cache_hit"):
                try:
                    _sem_cache = _get_sem_cache()
                    if _sem_cache is None:
                        raise ImportError("SemanticCache unavailable")
                    _store_query = (
                        _original_body.decode("utf-8")
                        if isinstance(_original_body, bytes)
                        else _original_body
                    )
                    _store_resp_raw = (
                        resp_body
                        if "resp_body" in locals()
                        else json.dumps({"status": status}).encode()
                    )
                    _store_resp_dict = (
                        json.loads(_store_resp_raw)
                        if isinstance(_store_resp_raw, (bytes, str))
                        else _store_resp_raw
                    )
                    _sem_cache.store(_store_query, _store_resp_dict)
                    SESSION["semantic_cache_stored"] = True
                except Exception as _sc_store_err:
                    SESSION["semantic_cache_store_error"] = str(_sc_store_err)
                    pass  # fail-open

            latency_ms = int((time.time() - t0) * 1000)

            # Post-request: Stability Scorer — track response consistency over time
            if STABILITY_SCORER_ENABLED:
                try:
                    from tokenpak.agent.regression.stability_scorer import (
                        RunRecord,
                        StabilityScorer,
                    )

                    _ss = StabilityScorer()
                    _workflow_id = _resolve_session_id(self.headers, model)
                    _resp_text = ""
                    try:
                        _resp_text = (
                            (
                                resp_body[:500].decode("utf-8")
                                if isinstance(resp_body, bytes)
                                else str(resp_body)[:500]
                            )
                            if "resp_body" in locals()
                            else ""
                        )
                    except Exception:
                        pass
                    _record = RunRecord(
                        timestamp=str(int(time.time())),
                        passed=status == 200,
                        retried=False,
                        token_count=(input_tokens or 0) + (output_tokens or 0),
                        output_text=_resp_text,
                        validation_passed=status == 200,
                    )
                    _ss.record_run(_workflow_id, _record)
                    _score = _ss.score_workflow(_workflow_id)
                    SESSION["stability_score"] = (
                        _score.score if hasattr(_score, "score") else str(_score)
                    )
                except Exception as _ss_err:
                    SESSION["stability_scorer_error"] = str(_ss_err)
                    pass  # fail-open

            # Post-request: Log completed request via Request Logger
            if REQUEST_LOGGER_ENABLED and _request_log_id:
                try:
                    from tokenpak.monitoring.request_logger import RequestLogger

                    _req_logger = RequestLogger.get_instance()
                    _record = _req_logger.build_record(
                        request_id=_request_log_id,
                        method="POST",
                        endpoint=target_url,
                        request_body_size=len(body) if body else 0,
                        response_status=status,
                        compression_ratio=round(sent_input_tokens / input_tokens, 3)
                        if input_tokens
                        else None,
                        latency_ms=latency_ms,
                        model=model,
                        provider=_cb_provider if "_cb_provider" in dir() else "",
                    )
                    _req_logger.log(_record)
                    SESSION["request_logger_logged"] = True
                except Exception as _rl_post_err:
                    SESSION["request_logger_post_error"] = str(_rl_post_err)
                    pass  # fail-open

            if should_log and is_messages and input_tokens > 0:
                cost = estimate_cost(
                    model,
                    sent_input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_creation_tokens,
                )
                saved = max(0, input_tokens - sent_input_tokens)
                # Estimate cost saved (what it would have cost without compression)
                cost_without_compression = estimate_cost(
                    model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
                )
                cost_saved = max(0.0, cost_without_compression - cost)
                sources_str = ",".join(injected_sources) if injected_sources else ""
                _log_compilation_mode = "bypass" if _bypass_request else COMPILATION_MODE
                
                # CACHE-P4-002: Detect provider and calculate cache savings
                _request_provider = detect_provider(target_url)
                _provider_name = _request_provider.value if _request_provider else "unknown"
                _cache_savings = estimate_cache_savings(_request_provider, cache_read_tokens, model)
                
                try:
                    MONITOR.log(
                        model,
                        sent_input_tokens,
                        output_tokens,
                        cost,
                        latency_ms,
                        status,
                        target_url,
                        _log_compilation_mode,
                        protected_tokens,
                        saved,
                        injected_tokens,
                        sources_str,
                        cache_read_tokens,
                        cache_creation_tokens,
                        cache_provider=_provider_name,
                        cache_estimated_savings=_cache_savings,
                        session_id=_resolve_session_id(self.headers, model),
                    )
                except Exception as _monitor_err:
                    print(
                        f"  ⚠️ Monitor.log() failed (SQLite error, request unaffected): {_monitor_err}"
                    )
                # CCG-06: Write mutation audit row — every request in every mode
                try:
                    _audit_mode = "bypass" if _bypass_request else COMPILATION_MODE
                    _write_mutation_audit(
                        MONITOR_DB,
                        None,  # request_id FK is advisory; not available synchronously
                        _resolve_session_id(self.headers, model),
                        _body_pre_audit,
                        _body_post_audit,
                        _audit_rules,
                        _audit_cache_risk,
                        _audit_mode,
                    )
                except Exception:
                    pass  # fail-open: never break a request over audit write
                try:
                    from tokenpak.telemetry.anon_metrics import record_request

                    record_request(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        tokens_saved=saved,
                        latency_ms=latency_ms,
                        model=model,
                    )
                except Exception:
                    pass  # never break the proxy
                # Record request latency for p50/p99 tracking
                _req_elapsed_ms = (time.time() - t0) * 1000
                with _latency_lock:
                    _request_latencies.append(_req_elapsed_ms)

                SESSION["requests"] += 1
                SESSION["input_tokens"] += input_tokens
                SESSION["sent_input_tokens"] += sent_input_tokens
                SESSION["saved_tokens"] += saved
                SESSION["protected_tokens"] += protected_tokens
                SESSION["output_tokens"] += output_tokens
                SESSION["cost"] += cost
                SESSION["cost_saved"] += cost_saved
                SESSION["injected_tokens"] += injected_tokens
                SESSION["cache_read_tokens"] += cache_read_tokens
                SESSION["cache_creation_tokens"] += cache_creation_tokens
                if cache_read_tokens > 0:
                    SESSION["cache_hits"] += 1
                else:
                    SESSION["cache_misses"] += 1
                    miss_reason = _classify_cache_miss_reason(
                        raw_request_body_for_cache_reason,
                        cache_poison_scrubbed=cache_poison_scrubbed,
                        tools_schema_changed=tools_schema_changed,
                        final_body=final_request_body_for_cache_reason,
                    )
                    miss_map = SESSION.setdefault("cache_miss_reasons", {})
                    miss_map[miss_reason] = int(miss_map.get(miss_reason, 0) or 0) + 1
                
                # CACHE-P4-002: Per-provider cache tracking (SESSION dict — backward compat)
                _provider_stats = SESSION["cache_by_provider"].setdefault(_provider_name, {
                    "hits": 0,
                    "misses": 0,
                    "read_tokens": 0,
                    "creation_tokens": 0,
                    "savings_usd": 0.0,
                })
                _provider_stats["read_tokens"] += cache_read_tokens
                _provider_stats["creation_tokens"] += cache_creation_tokens
                _provider_stats["savings_usd"] += _cache_savings
                if cache_read_tokens > 0:
                    _provider_stats["hits"] += 1
                else:
                    _provider_stats["misses"] += 1

                # CACHE-P4-002: CacheTelemetry — structured per-provider telemetry
                if CACHE_TELEMETRY is not None:
                    _mode_str = (
                        _effective_mode.value
                        if _effective_mode is not None and hasattr(_effective_mode, "value")
                        else None
                    )
                    try:
                        CACHE_TELEMETRY.record(
                            provider=_provider_name,
                            mode=_mode_str,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                            savings_usd=_cache_savings,
                        )
                    except Exception:
                        pass  # never break the proxy
                
                if injected_tokens > 0:
                    SESSION["injection_hits"] += 1

                # Complete and store pipeline trace
                if trace:
                    trace.model = model
                    trace.input_tokens = input_tokens
                    trace.output_tokens = output_tokens
                    trace.tokens_saved = saved
                    trace.cost_saved = cost_saved
                    trace.total_cost = cost
                    trace.duration_ms = latency_ms
                    trace.status = "complete"
                    TRACE_STORAGE.store(trace)

                # Workflow tracking: mark forward done → log_metrics → complete
                if _wf_id:
                    try:
                        from tokenpak.agent.agentic.proxy_workflow import (
                            advance_step,
                            complete_workflow,
                        )

                        advance_step(_wf_id, "forward", "log_metrics")
                        complete_workflow(_wf_id)
                    except Exception:
                        pass

                # Update last request stats for /stats/last endpoint
                request_id = trace.request_id if trace else str(uuid.uuid4())[:8]
                update_last_request(
                    request_id=request_id,
                    model=model,
                    input_raw=input_tokens,
                    input_sent=sent_input_tokens,
                    tokens_saved=saved,
                    cost_saved=cost_saved,
                    output_tokens=output_tokens,
                )

                stream_tag = " [SSE]" if is_sse else ""
                mode_tag = f" [{COMPILATION_MODE}]"
                inject_tag = f" [+{injected_tokens} vault]" if injected_tokens > 0 else ""
                # Cache status tag: show FRESH/CACHED with token counts for clarity
                if cache_read_tokens > 0:
                    _saved_k = f"{cache_read_tokens:,}"
                    cache_tag = f" (CACHED: {_saved_k} tokens)"
                elif cache_creation_tokens > 0:
                    _written_k = f"{cache_creation_tokens:,}"
                    cache_tag = f" (FRESH: {_written_k} written)"
                else:
                    cache_tag = " (FRESH)"
                print(
                    f"  📊 {model}{stream_tag}{mode_tag}{inject_tag}: {input_tokens:,} in → {sent_input_tokens:,} sent "
                    f"(saved {saved:,}, protected {protected_tokens:,}) / {output_tokens:,} out | "
                    f"~${cost:.4f}{cache_tag} | {latency_ms}ms"
                )

        except Exception as e:
            SESSION["errors"] += 1
            latency_ms = int((time.time() - t0) * 1000)
            import traceback as _tb

            _tb.print_exc(file=__import__("sys").stderr)
            print(f"  ❌ Proxy error: {type(e).__name__}: {e} | {latency_ms}ms")
            # Workflow tracking: mark the in-progress step as failed (not whole workflow)
            if _wf_id:
                try:
                    from tokenpak.agent.agentic.proxy_workflow import fail_step as _wf_fail

                    _wf_fail(_wf_id, "forward", error=f"{type(e).__name__}: {e}")
                except Exception:
                    pass
            try:
                err = json.dumps({"error": {"type": "proxy_error", "message": str(e)}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(err))
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass

    def _ingest(self, path):
        """Handle /ingest and /ingest/batch POST requests."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json({"error": "empty request body"}, status=400)
            return
        if content_length > 1024 * 1024:  # 1MB limit for ingest payloads
            self._send_json({"error": "request body too large (max 1MB)"}, status=413)
            return

        try:
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json({"error": f"invalid JSON: {e}"}, status=400)
            return

        if path == "/ingest":
            self._ingest_single(payload)
        elif path == "/ingest/batch":
            self._ingest_batch(payload)

    def _ingest_single(self, payload):
        """Handle single entry ingest."""
        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return

        # Validate required fields
        required = {"model", "tokens", "cost"}
        missing = required - set(payload.keys())
        if missing:
            self._send_json({"error": f"missing required fields: {', '.join(missing)}"}, status=400)
            return

        try:
            # Basic type validation
            model = payload.get("model")
            tokens = payload.get("tokens")
            cost = payload.get("cost")

            if not isinstance(model, str) or not model:
                raise ValueError("model must be a non-empty string")
            if not isinstance(tokens, int) or tokens < 0:
                raise ValueError("tokens must be a non-negative integer")
            if not isinstance(cost, (int, float)) or cost < 0:
                raise ValueError("cost must be a non-negative number")

            # Validate timestamp if provided
            timestamp = payload.get("timestamp")
            if timestamp is not None:
                if not isinstance(timestamp, str):
                    raise ValueError("timestamp must be a string")
                # Validate ISO 8601 format
                try:
                    datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(f"invalid ISO 8601 timestamp: {timestamp}")
            else:
                # Use current UTC time
                timestamp = datetime.now(timezone.utc).isoformat()
                payload["timestamp"] = timestamp

            # Write entry
            entry_id = _ingest_write_entry(payload)
            self._send_json({"status": "ok", "ids": [entry_id]}, status=200)
            SESSION["ingest_entries"] = SESSION.get("ingest_entries", 0) + 1
        except ValueError as e:
            self._send_json({"error": str(e)}, status=422)
        except Exception as e:
            self._send_json({"error": f"internal error: {e}"}, status=500)

    def _ingest_batch(self, payload):
        """Handle batch entry ingest."""
        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return

        if "events" not in payload:
            self._send_json({"error": "missing 'events' field"}, status=400)
            return

        events = payload["events"]
        if not isinstance(events, list):
            self._send_json({"error": "events must be a list"}, status=400)
            return

        if len(events) == 0:
            self._send_json({"error": "events list cannot be empty"}, status=400)
            return

        if len(events) > 1000:
            self._send_json({"error": "events list too large (max 1000)"}, status=400)
            return

        ids = []
        errors = []

        for i, event in enumerate(events):
            if not isinstance(event, dict):
                errors.append(f"event[{i}]: expected object, got {type(event).__name__}")
                continue

            required = {"model", "tokens", "cost"}
            missing = required - set(event.keys())
            if missing:
                errors.append(f"event[{i}]: missing fields {', '.join(missing)}")
                continue

            try:
                model = event.get("model")
                tokens = event.get("tokens")
                cost = event.get("cost")

                if not isinstance(model, str) or not model:
                    raise ValueError("model must be non-empty string")
                if not isinstance(tokens, int) or tokens < 0:
                    raise ValueError("tokens must be non-negative int")
                if not isinstance(cost, (int, float)) or cost < 0:
                    raise ValueError("cost must be non-negative number")

                timestamp = event.get("timestamp")
                if timestamp is not None:
                    if not isinstance(timestamp, str):
                        raise ValueError("timestamp must be string")
                    try:
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except ValueError:
                        raise ValueError(f"invalid timestamp: {timestamp}")
                else:
                    timestamp = datetime.now(timezone.utc).isoformat()
                    event["timestamp"] = timestamp

                entry_id = _ingest_write_entry(event)
                ids.append(entry_id)
            except ValueError as e:
                errors.append(f"event[{i}]: {e}")

        # Return success if we got any entries
        if ids:
            self._send_json(
                {"status": "ok", "ids": ids, "errors": errors if errors else None}, status=200
            )
            SESSION["ingest_entries"] = SESSION.get("ingest_entries", 0) + len(ids)
        else:
            # All events failed
            self._send_json({"error": f"all events failed: {'; '.join(errors)}"}, status=422)

    def _serve_api_docs(self):
        """Serve Swagger UI for interactive API documentation."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TokenPak API Docs</title>
  <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; background: #fafafa; }
    .swagger-ui .topbar { background: #1a1a2e; }
    .swagger-ui .topbar .download-url-wrapper { display: none; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
  <script>
    window.onload = function() {
      SwaggerUIBundle({
        url: "/openapi.yaml",
        dom_id: "#swagger-ui",
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        layout: "StandaloneLayout",
        deepLinking: true,
        defaultModelsExpandDepth: 1,
        tryItOutEnabled: true,
      });
    };
  </script>
</body>
</html>"""
        body_bytes = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _serve_openapi_yaml(self):
        """Serve the OpenAPI YAML spec file."""
        import pathlib

        # Look for openapi.yaml in the docs directory adjacent to the package
        candidates = [
            pathlib.Path(__file__).parent.parent.parent / "docs" / "openapi.yaml",
            pathlib.Path(__file__).parent.parent / "docs" / "openapi.yaml",
        ]
        for spec_path in candidates:
            if spec_path.exists():
                body_bytes = spec_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/yaml")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                return
        self._send_json(
            {"error": {"type": "not_found", "message": "openapi.yaml not found"}}, status=404
        )

    def _serve_tools_panel(self):
        """CCI-02: Serve /dashboard/tools — inline HTML panel for tool schema registry.

        Shows 4 metrics: tools_normalized_count, bytes_saved_total,
        cache_hit_rate_for_tools_block, and schema_changes (recent normalizations proxy).
        Fetches live data from /metrics/dashboard/tools every 30 s.
        """
        html = b"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TokenPak \xe2\x80\x94 Tool Registry</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#0a0a0a;color:#e4e4e7;padding:2rem;min-height:100vh}
    .container{max-width:1100px;margin:0 auto}
    h1{font-size:1.75rem;margin-bottom:.25rem;color:#fafafa}
    .subtitle{font-size:.875rem;color:#71717a;margin-bottom:2rem}
    .nav{font-size:.8rem;margin-bottom:1.5rem}
    .nav a{color:#10b981;text-decoration:none}
    .nav a:hover{text-decoration:underline}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1.5rem;margin-bottom:2rem}
    .card{background:#18181b;border:1px solid #27272a;border-radius:.5rem;padding:1.5rem;transition:border-color .2s}
    .card:hover{border-color:#3f3f46}
    .card-title{font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:#a1a1a6;margin-bottom:.75rem}
    .card-value{font-size:2rem;font-weight:600;color:#10b981;font-variant-numeric:tabular-nums}
    .card-sub{font-size:.75rem;color:#71717a;margin-top:.5rem}
    .card.warn .card-value{color:#f59e0b}
    .footer{font-size:.75rem;color:#52525b;margin-top:1rem}
    .status-ok{color:#10b981}.status-warn{color:#f59e0b}.status-err{color:#ef4444}
    #last-update{font-style:italic}
  </style>
</head>
<body>
<div class="container">
  <div class="nav"><a href="/dashboard">\xe2\x86\x90 Dashboard</a></div>
  <h1>Tool Schema Registry</h1>
  <p class="subtitle">Prompt-cache stability via deterministic tool normalization</p>
  <div class="grid">
    <div class="card" id="card-normalized">
      <div class="card-title">Tools Normalized</div>
      <div class="card-value" id="normalized-count">\xe2\x80\xa6</div>
      <div class="card-sub" id="normalized-sub">requests processed by registry</div>
    </div>
    <div class="card" id="card-bytes">
      <div class="card-title">Bytes Saved (Session)</div>
      <div class="card-value" id="bytes-saved">\xe2\x80\xa6</div>
      <div class="card-sub" id="bytes-sub">vs un-normalized request bodies</div>
    </div>
    <div class="card" id="card-hitrate">
      <div class="card-title">Cache-Hit Rate (Tools Block)</div>
      <div class="card-value" id="hit-rate">\xe2\x80\xa6</div>
      <div class="card-sub" id="hitrate-sub">requests where frozen schema matched</div>
    </div>
    <div class="card" id="card-changes">
      <div class="card-title">Schema Changes</div>
      <div class="card-value" id="schema-changes">\xe2\x80\xa6</div>
      <div class="card-sub" id="changes-sub">tool set updates since session start</div>
    </div>
  </div>
  <div class="footer">
    Auto-refreshing every 30 s \xc2\xb7 <span id="last-update">Never</span>
    \xc2\xb7 Data from <code>/metrics/dashboard/tools</code>
  </div>
</div>
<script>
const API = '/metrics/dashboard/tools';
const REFRESH = 30000;

function fmt(n) {
  if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return String(Math.round(n));
}
function fmtBytes(b) {
  if (b >= 1048576) return (b/1048576).toFixed(2)+' MB';
  if (b >= 1024) return (b/1024).toFixed(1)+' KB';
  return b+' B';
}
function fmtPct(r) { return (r*100).toFixed(1)+'%'; }

async function refresh() {
  try {
    const r = await fetch(API);
    if (!r.ok) throw new Error('HTTP '+r.status);
    const d = await r.json();

    document.getElementById('normalized-count').textContent = fmt(d.tools_normalized_count||0);
    document.getElementById('normalized-sub').textContent =
      'frozen tools: '+(d.frozen_tools||0)+' (\xe2\x89\x88'+(d.frozen_tokens_approx||0)+' tokens)';

    document.getElementById('bytes-saved').textContent = fmtBytes(d.bytes_saved_total||0);
    const fb = d.frozen_bytes||0;
    document.getElementById('bytes-sub').textContent =
      fb ? 'frozen schema: '+fmtBytes(fb) : 'vs un-normalized request bodies';

    const hr = d.cache_hit_rate_for_tools_block||0;
    const hitEl = document.getElementById('hit-rate');
    hitEl.textContent = fmtPct(hr);
    const card = document.getElementById('card-hitrate');
    card.className = 'card'+(hr < 0.5 ? ' warn' : '');

    const sc = d.schema_changes||0;
    document.getElementById('schema-changes').textContent = fmt(sc);
    const fh = d.frozen_hash;
    document.getElementById('changes-sub').textContent =
      fh ? 'current hash: '+fh : (sc===0 ? 'stable since session start' : sc+' invalidation(s)');

    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
  } catch(e) {
    console.error('tools panel fetch failed:', e);
  }
}

refresh();
setInterval(refresh, REFRESH);
</script>
</body>
</html>
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(html)

    def _serve_dashboard(self):
        """Serve static dashboard files (HTML/CSS/JS)."""
        # Token auth gate
        if DASHBOARD_AUTH_ENABLED:
            from urllib.parse import parse_qs, urlparse

            from tokenpak._internal.token_manager import load_or_create_token

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            provided = params.get("token", [None])[0]
            expected = load_or_create_token()
            if not provided or provided != expected:
                self._send_json(
                    {
                        "error": {
                            "type": "unauthorized",
                            "message": "Dashboard token required. Append ?token=<your-token> to the URL.",
                        }
                    },
                    status=401,
                )
                return
            # Use path without query string for file resolution
            self.path = parsed.path

        dashboard_dir = Path(__file__).parent / "tokenpak" / "dashboard"

        # Default to index.html
        if self.path == "/dashboard" or self.path == "/dashboard/":
            file_path = dashboard_dir / "index.html"
            content_type = "text/html; charset=utf-8"
        else:
            # Parse requested file
            rel_path = self.path[len("/dashboard/") :]
            file_path = (dashboard_dir / rel_path).resolve()

            # Security: prevent directory traversal
            if not str(file_path).startswith(str(dashboard_dir.resolve())):
                self._send_json(
                    {"error": {"type": "forbidden", "message": "Access denied"}}, status=403
                )
                return

            # Determine content type
            if rel_path.endswith(".html"):
                content_type = "text/html; charset=utf-8"
            elif rel_path.endswith(".css"):
                content_type = "text/css; charset=utf-8"
            elif rel_path.endswith(".js"):
                content_type = "application/javascript; charset=utf-8"
            elif rel_path.endswith(".json"):
                content_type = "application/json"
            else:
                content_type = "application/octet-stream"

        # Serve file
        if not file_path.exists():
            self._send_json(
                {"error": {"type": "not_found", "message": f"File not found: {rel_path}"}},
                status=404,
            )
            return

        try:
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send_json({"error": {"type": "server_error", "message": str(e)}}, status=500)

    def _send_json(self, data, status=200):
        body = json.dumps(data, separators=(",", ":")).encode()  # compact JSON: faster + smaller
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)


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
# WebSocket proxy — /ws endpoint on WS_PORT (default 8767)
# ---------------------------------------------------------------------------

_ws_active_connections: int = 0
_ws_active_connections_lock = threading.Lock()


async def _ws_handler(websocket) -> None:
    """Handle a single WebSocket connection: receive JSON, compress, proxy to Anthropic, stream back."""
    global _ws_active_connections

    # Check path — only /ws is supported
    req_path = "/"
    try:
        req_path = websocket.request.path
    except Exception:
        pass
    if req_path != "/ws":
        await websocket.close(1008, "Not found")
        return

    # Enforce max connections
    with _ws_active_connections_lock:
        if _ws_active_connections >= WS_MAX_CONNECTIONS:
            await websocket.close(1008, "Too many connections")
            return
        _ws_active_connections += 1

    try:
        # Receive request JSON from client
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=30.0)
        except asyncio.TimeoutError:
            await websocket.close(1008, "Receive timeout")
            return

        try:
            req_data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await websocket.close(1003, "Invalid JSON")
            return

        # Force streaming
        req_data["stream"] = True
        body_bytes: bytes = json.dumps(req_data).encode()

        # Apply TokenPak compression pipeline (sync — run in thread executor)
        loop = asyncio.get_event_loop()
        try:
            compressed_body, _sent, _orig, _prot = await loop.run_in_executor(
                None, compact_request_body, body_bytes
            )
        except Exception:
            compressed_body = body_bytes

        # Resolve Anthropic upstream
        upstream_base = UPSTREAM_ROUTES.get("anthropic-messages", "https://api.anthropic.com")
        parsed_up = urlparse(upstream_base)
        upstream_host = parsed_up.netloc or "api.anthropic.com"
        upstream_path = "/v1/messages"

        # Forward headers: pass through auth headers from WS upgrade request
        fwd_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Content-Length": str(len(compressed_body)),
            "Host": upstream_host,
            "anthropic-version": "2023-06-01",
        }
        try:
            for hname, hval in websocket.request.headers.items():
                hl = hname.lower()
                if hl in ("x-api-key", "authorization", "anthropic-version", "anthropic-beta"):
                    fwd_headers[hl] = hval
        except Exception:
            pass

        # Connect to upstream and stream SSE back (sync — run in executor)
        def _connect_upstream():
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(upstream_host, timeout=UPSTREAM_TIMEOUT, context=ctx)
            conn.request("POST", upstream_path, body=compressed_body, headers=fwd_headers)
            return conn, conn.getresponse()

        try:
            conn, resp = await loop.run_in_executor(None, _connect_upstream)
        except Exception as exc:
            await websocket.close(1011, f"Upstream connection failed: {str(exc)[:100]}")
            return

        # Non-2xx: close with error code 1011
        if resp.status >= 400:
            try:
                err_body = await loop.run_in_executor(None, resp.read)
                await websocket.send(err_body.decode("utf-8", errors="replace"))
            except Exception:
                pass
            await websocket.close(1011, f"Upstream error {resp.status}")
            return

        # Stream SSE chunks back as text frames
        while True:
            chunk = await loop.run_in_executor(None, resp.read, 4096)
            if not chunk:
                break
            try:
                await websocket.send(chunk.decode("utf-8", errors="replace"))
            except Exception:
                break  # client disconnected

        await websocket.close(1000, "Done")

    except Exception as exc:
        try:
            await websocket.close(1011, str(exc)[:123])
        except Exception:
            pass
    finally:
        with _ws_active_connections_lock:
            _ws_active_connections -= 1


def _start_ws_server() -> threading.Thread:
    """Start the asyncio WebSocket server in a daemon thread on WS_PORT."""
    try:
        from websockets.asyncio.server import serve as ws_serve
    except ImportError:
        print(
            "[ws] websockets library not installed — WebSocket server disabled. Run: pip install websockets>=12.0"
        )
        return None  # type: ignore[return-value]

    async def _serve() -> None:
        try:
            async with ws_serve(_ws_handler, "127.0.0.1", WS_PORT, reuse_address=True):
                print(f"[ws] TokenPak WebSocket server ready — port={WS_PORT}")
                await asyncio.Future()  # run until cancelled
        except Exception as exc:
            print(f"[ws] WebSocket server error: {exc}")

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name="tokenpak-ws-server")
    t.start()
    return t


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
    VAULT_INDEX.maybe_reload()
    _vault_index_reload_timer()
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
    if _DB_WRITE_QUEUE is not None:
        print("[shutdown] Draining DB write queue…")
        try:
            # Wait for queue to drain (up to 5 seconds)
            _DB_WRITE_QUEUE.join()
            print("[shutdown] ✅ DB write queue drained")
        except Exception as e:
            print(f"[shutdown] ⚠️  DB queue drain error: {e}")
        
        # Stop background worker
        _DB_BACKGROUND_STOP.set()
        if _DB_BACKGROUND_THREAD and _DB_BACKGROUND_THREAD.is_alive():
            _DB_BACKGROUND_THREAD.join(timeout=2)
            if _DB_BACKGROUND_THREAD.is_alive():
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


# Token cache counters moved above _token_count_cached (was unreachable here after main())
