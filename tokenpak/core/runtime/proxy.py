"""
tokenpak.core.runtime.proxy — compatibility shim

The old monolithic proxy.py that lived here has been deleted as part of the
TPK-CONSOLIDATION-B1 cleanup.  This shim re-exports canonical symbols from
their new modular locations and provides inline implementations for items
that have not yet been extracted to the modular tree.

CLAUDE_CODE_HEADER_ALLOWLIST, LEGACY_HEADER_ALLOWLIST, _classify_route
_write_mutation_audit, _prune_mutation_audit
_resolve_session_id
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

# ---------------------------------------------------------------------------
# Implemented symbols — re-exported from modular tree
# ---------------------------------------------------------------------------
from tokenpak.proxy.circuit_breaker import _sanitize_headers  # noqa: F401
from tokenpak.proxy.config import _PROFILE_PRESETS as _BASE_PROFILE_PRESETS
from tokenpak.proxy.config import (  # noqa: F401
    COMPILATION_MODE,
    MUTATION_AUDIT_TTL_DAYS,
    STABLE_CACHE_CONTROL_AUTO,
)
from tokenpak.proxy.monitor import Monitor as _BaseMonitor  # noqa: F401
from tokenpak.proxy.request_pipeline import _partition_stable_volatile  # noqa: F401
from tokenpak.proxy.request_pipeline import can_compress as _base_can_compress
from tokenpak.proxy.server import ForwardProxyHandler  # noqa: F401
from tokenpak.telemetry.monitoring.server import ThreadedHTTPServer  # noqa: F401

# ---------------------------------------------------------------------------
# can_compress — transparent mode must always return False
# ---------------------------------------------------------------------------


def can_compress(risk_class: str, mode: str) -> bool:
    """Return whether compression is allowed. Transparent and safe modes always return False."""
    if mode in ("strict", "transparent", "safe"):  # safe mode disables compression
        return False
    return _base_can_compress(risk_class, mode)


# ---------------------------------------------------------------------------
# _PROFILE_PRESETS — extend base with claude-code / transparent profiles
# ---------------------------------------------------------------------------
_PROFILE_PRESETS: dict[str, dict[str, str]] = {
    **_BASE_PROFILE_PRESETS,
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
}


# ---------------------------------------------------------------------------
# Monitor — subclass that adds schema additions (session_id +
# mutation_audit table) on top of the base modular Monitor._init_db.
# ---------------------------------------------------------------------------
class Monitor(_BaseMonitor):
    """Monitor with schema additions (session_id column + mutation_audit table)
    and session_id support in log()."""

    def _init_db(self) -> None:
        super()._init_db()
        conn = sqlite3.connect(str(self.db_path))
        # session_id column on requests
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN session_id TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_id)")
        # stable_hash and volatile_hash columns for safe-mode fingerprinting
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN stable_hash TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN volatile_hash TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        # mutation_audit table
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mutation_audit_session ON mutation_audit(session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mutation_audit_ts ON mutation_audit(timestamp)"
        )
        conn.commit()
        # cache_invalidator_events table (log-only, Phase 2)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_invalidator_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                session_id TEXT,
                timestamp TEXT NOT NULL,
                event_type TEXT,
                before_value TEXT,
                after_value TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cie_session ON cache_invalidator_events(session_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cie_ts ON cache_invalidator_events(timestamp)")
        conn.commit()
        conn.close()

    # This compatibility shim intentionally preserves the legacy positional ABI.
    def log(  # type: ignore[override]
        self,
        model: object,
        input_tokens: object,
        output_tokens: object,
        cost: object,
        latency_ms: object,
        status_code: object,
        endpoint: object,
        compilation_mode: object = "",
        protected_tokens: object = 0,
        compressed_tokens: object = 0,
        injected_tokens: object = 0,
        injected_sources: object = "",
        cache_read_tokens: object = 0,
        cache_creation_tokens: object = 0,
        would_have_saved: object = 0,
        session_id: object = "",
        stable_hash: object = "",
        volatile_hash: object = "",
        cache_origin: object = "unknown",
        user_id: object = "",
        cache_creation_ephemeral_1h_tokens: object = 0,
        cache_creation_ephemeral_5m_tokens: object = 0,
        ttl_attribution: object | None = None,
        agent_id: object = "",
        cycle_id: object = "",
        attribution_source: object = "",
        stop_reason: object = "",
    ) -> None:
        """Log a request; extends parent with session_id and fingerprints."""
        from datetime import datetime

        try:
            _conn = sqlite3.connect(str(self.db_path))
            _conn.execute(
                "INSERT INTO requests "
                "(timestamp, model, request_type, input_tokens, output_tokens, "
                "estimated_cost, latency_ms, status_code, endpoint, compilation_mode, "
                "protected_tokens, compressed_tokens, injected_tokens, injected_sources, "
                "cache_read_tokens, cache_creation_tokens, would_have_saved, session_id, "
                "stable_hash, volatile_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
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
                    session_id,
                    stable_hash,
                    volatile_hash,
                ),
            )
            _conn.commit()
            _conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# mutation audit helpers
# ---------------------------------------------------------------------------


def _prune_mutation_audit(conn: sqlite3.Connection, ttl_days: int) -> int:
    """Delete mutation_audit rows older than ttl_days. Returns number of rows deleted."""
    cur = conn.execute(
        "DELETE FROM mutation_audit WHERE timestamp < datetime('now', '-' || ? || ' days')",
        (ttl_days,),
    )
    conn.commit()
    return cur.rowcount


def _write_mutation_audit(
    db_path: str | Path,
    request_id: int | None,
    session_id: str,
    body_pre: bytes,
    body_post: bytes,
    rules_applied: Sequence[str],
    cache_risk: str,
    mode: str,
) -> None:
    """Write one mutation_audit row per request."""
    pre_hash = hashlib.sha256(body_pre).hexdigest()
    post_hash = hashlib.sha256(body_post).hexdigest()
    rollback_possible = 1
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
                json.dumps(rules_applied),
                cache_risk,
                rollback_possible,
                mode,
            ),
        )
        _conn.commit()
        _conn.close()
    except Exception:
        pass  # fail-open: never break a request over audit write


# ---------------------------------------------------------------------------
# session id resolver
# ---------------------------------------------------------------------------


def _resolve_session_id(headers: Mapping[str, object], model: str) -> str:
    """Resolve session id with Claude Code priority.

    Order: X-Claude-Code-Session-Id -> X-TokenPak-Session -> model name.
    """

    def _h(name: str) -> str | None:
        for variant in (name, name.lower(), name.title()):
            value = headers.get(variant)
            if isinstance(value, str) and value:
                return value
        return None

    cc_id = _h("X-Claude-Code-Session-Id")
    if cc_id:
        return cc_id
    oc_id = _h("X-TokenPak-Session")
    if oc_id:
        return oc_id
    return model


# ---------------------------------------------------------------------------
# Per-route header allowlist — real implementation.
# CLAUDE_CODE_HEADER_ALLOWLIST is canonical in tokenpak.proxy.headers (single
# frozenset). LEGACY_HEADER_ALLOWLIST + _classify_route live in
# tokenpak.proxy.passthrough alongside the HTTP-path wiring.
# ---------------------------------------------------------------------------
from tokenpak.proxy.headers import CLAUDE_CODE_HEADER_ALLOWLIST  # noqa: F401
from tokenpak.proxy.passthrough import (  # noqa: F401
    LEGACY_HEADER_ALLOWLIST,
    _classify_route,
)

# TOKENPAK_HEADER_ALLOWLIST is the canonical public name for LEGACY_HEADER_ALLOWLIST.
TOKENPAK_HEADER_ALLOWLIST = LEGACY_HEADER_ALLOWLIST  # noqa: F401

# ---------------------------------------------------------------------------
# SESSION — global request statistics dict. The modular tree tracks per-
# module state internally; this shim surfaces a single SESSION dict for
# compatibility with tests and the /stats endpoint.
# ---------------------------------------------------------------------------
import time as _time

SESSION: dict[str, object] = {
    "requests": 0,
    "input_tokens": 0,
    "sent_input_tokens": 0,
    "saved_tokens": 0,
    "protected_tokens": 0,
    "output_tokens": 0,
    "cost": 0.0,
    "cost_saved": 0.0,
    "start_time": _time.time(),
    "errors": 0,
    "compilation_mode": COMPILATION_MODE,
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
    "cache_by_provider": {},
    "token_cache_hits": 0,
    "token_cache_misses": 0,
    "canon_hits": 0,
    "canon_tokens_saved": 0,
    "ingest_entries": 0,
    "compression_timeouts": 0,
    "vault_last_timing_ms": {},
}

# ---------------------------------------------------------------------------
# _detect_adapter / extract_query_signal — adapter detection helpers
# re-exported from modular location (needed by vault_bridge lazy import).
# ---------------------------------------------------------------------------
from tokenpak.proxy.adapters.utils import (  # noqa: F401
    _detect_adapter,
    extract_query_signal,
)

# ---------------------------------------------------------------------------
# inject_vault_context — transferred to modular tree in A2b; re-exported here
# so that code/tests that import it from tokenpak.core.runtime.proxy still work.
# ---------------------------------------------------------------------------
from tokenpak.proxy.vault_bridge import inject_vault_context  # noqa: F401
