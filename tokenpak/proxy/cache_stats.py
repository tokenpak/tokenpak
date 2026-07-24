"""TokenPak Proxy — DB-backed cache statistics aggregation.

Transferred from monolith (packages/core/tokenpak/runtime/proxy.py) as part of
TPK-CONSOLIDATION-A2c.

Provides:
- _get_cache_stats_by_window(hours, db_path) — per-provider cache stats from SQLite
- _build_cache_stats_payload(session, db_path, token_cache_hits, token_cache_misses) —
  comprehensive cache stats payload for /cache/stats endpoint

The A3 wire-up task supplies the runtime db_path and session dict; these
functions accept them as parameters to keep unit tests side-effect-free.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


def _get_cache_stats_by_window(hours: int = 24, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Query DB for cache stats within a time window.

    Returns per-provider stats and overall totals for the given time window.

    Args:
        hours: Look-back window in hours (default 24).
        db_path: SQLite DB file path. If None the function returns a graceful
                 error dict (matches monolith fail-open behaviour).

    Returns:
        Dict with keys: total_requests, cache_hits, hit_rate,
        cache_read_tokens, cache_creation_tokens, estimated_savings_usd,
        per_provider (dict).  On error, includes an "error" key.
    """
    try:
        if not db_path:
            raise ValueError("db_path not configured")

        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        # Detect whether cache_origin column exists (added by monitor._init_db).
        # Pre-migration rows (legacy) lack origin signal → treated as 'unknown'.
        col_names = {r[1] for r in cur.execute("PRAGMA table_info(requests)").fetchall()}
        has_origin = "cache_origin" in col_names
        origin_expr = "COALESCE(cache_origin, 'unknown')" if has_origin else "'unknown'"

        # Overall stats: cache reads are observed regardless of origin; savings
        # are attributed only to proxy-owned cache markers.  unknown → no
        # tokenpak credit (conservative).
        cur.execute(
            f"""
            SELECT
                COUNT(*) as total_requests,
                SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) as cache_hits,
                COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation,
                COALESCE(SUM(CASE WHEN {origin_expr} = 'client'  THEN cache_read_tokens ELSE 0 END), 0) as cache_read_client,
                COALESCE(SUM(CASE WHEN {origin_expr} = 'proxy'   THEN cache_read_tokens ELSE 0 END), 0) as cache_read_proxy,
                COALESCE(SUM(CASE WHEN {origin_expr} = 'unknown' THEN cache_read_tokens ELSE 0 END), 0) as cache_read_unknown
            FROM requests
            WHERE timestamp >= ?
        """,
            (cutoff,),
        )
        overall = cur.fetchone()
        conn.close()

        total_requests = overall[0] or 0
        cache_hits = overall[1] or 0
        hit_rate = (cache_hits / total_requests) if total_requests > 0 else 0.0

        return {
            "total_requests": total_requests,
            "cache_hits": cache_hits,
            "hit_rate": round(hit_rate, 4),
            "cache_read_tokens": overall[2] or 0,
            "cache_creation_tokens": overall[3] or 0,
            "cache_read_by_origin": {
                "client": overall[4] or 0,
                "proxy": overall[5] or 0,
                "unknown": overall[6] or 0,
            },
            "per_provider": {},
        }
    except Exception as e:
        # Fail gracefully — return empty stats if DB query fails
        return {
            "total_requests": 0,
            "cache_hits": 0,
            "hit_rate": 0.0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_by_origin": {"client": 0, "proxy": 0, "unknown": 0},
            "per_provider": {},
            "error": str(e),
        }


def _build_cache_stats_payload(
    session: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    token_cache_hits: int = 0,
    token_cache_misses: int = 0,
) -> Dict[str, Any]:
    """Build comprehensive cache stats including per-provider breakdowns.

    Transferred from monolith (TPK-CONSOLIDATION-A2c, lines 3723–3788).
    In the modular tree this function is called from the /cache/stats handler
    with explicit parameters rather than reading module-level globals (A3 wires
    these up for the runtime path; tests can pass any session dict).

    Returns:
        - Session-level stats (since proxy start)
        - Per-provider session stats
        - DB-backed time-windowed stats (1h, 24h, 7d)
    """
    s = session or {}

    hits = int(s.get("cache_hits", 0) or 0)
    misses = int(s.get("cache_misses", 0) or 0)
    total = hits + misses
    hit_rate = (hits / total) if total > 0 else 0.0
    miss_reasons = dict(s.get("cache_miss_reasons", {}))

    # Session per-provider stats
    session_by_provider: Dict[str, Any] = {}
    cache_by_provider = s.get("cache_by_provider", {})
    for provider_name, pstats in cache_by_provider.items():
        provider_hits = pstats.get("hits", 0)
        provider_total = provider_hits + pstats.get("misses", 0)
        session_by_provider[provider_name] = {
            "cache_hits": provider_hits,
            "cache_misses": pstats.get("misses", 0),
            "hit_rate": round((provider_hits / provider_total) if provider_total > 0 else 0.0, 4),
            "cache_read_tokens": pstats.get("read_tokens", 0),
            "cache_creation_tokens": pstats.get("creation_tokens", 0),
            "estimated_savings_usd": round(pstats.get("savings_usd", 0.0), 6),
        }

    # Time-windowed stats from DB
    stats_1h = _get_cache_stats_by_window(hours=1, db_path=db_path)
    stats_24h = _get_cache_stats_by_window(hours=24, db_path=db_path)
    stats_7d = _get_cache_stats_by_window(hours=168, db_path=db_path)

    return {
        # Session stats (backward compatible)
        "hit_rate": round(hit_rate, 4),
        "cache_read_tokens": int(s.get("cache_read_tokens", 0) or 0),
        "cache_creation_tokens": int(s.get("cache_creation_tokens", 0) or 0),
        "cache_hits": hits,
        "cache_misses": misses,
        "total_cache_decisions": total,
        "miss_reasons": miss_reasons,
        "token_cache_hits": token_cache_hits,
        "token_cache_misses": token_cache_misses,
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
