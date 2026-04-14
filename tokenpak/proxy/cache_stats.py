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

        # Get overall stats — exclude client-managed routes (e.g. claude-code)
        # from tokenpak-attributed savings.  Cache reads are still reported for
        # observability but savings_usd only counts proxy-managed caching.
        cur.execute("""
            SELECT
                COUNT(*) as total_requests,
                SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) as cache_hits,
                COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation,
                COALESCE(SUM(CASE WHEN cache_provider IS NOT NULL AND COALESCE(route, '') != 'claude-code' THEN cache_estimated_savings ELSE 0 END), 0) as total_savings,
                COALESCE(SUM(CASE WHEN COALESCE(route, '') = 'claude-code' AND cache_read_tokens > 0 THEN cache_read_tokens ELSE 0 END), 0) as client_managed_cache_read
            FROM requests
            WHERE timestamp >= ?
        """, (cutoff,))
        overall = cur.fetchone()

        # Get per-provider stats — only count proxy-managed savings
        cur.execute("""
            SELECT
                cache_provider,
                COUNT(*) as requests,
                SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) as hits,
                COALESCE(SUM(cache_read_tokens), 0) as read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) as creation_tokens,
                COALESCE(SUM(CASE WHEN COALESCE(route, '') != 'claude-code' THEN cache_estimated_savings ELSE 0 END), 0) as savings
            FROM requests
            WHERE timestamp >= ? AND cache_provider IS NOT NULL AND cache_provider != ''
            GROUP BY cache_provider
        """, (cutoff,))
        per_provider = cur.fetchall()

        conn.close()

        total_requests = overall[0] or 0
        cache_hits = overall[1] or 0
        hit_rate = (cache_hits / total_requests) if total_requests > 0 else 0.0

        provider_stats: Dict[str, Any] = {}
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
            "client_managed_cache_read_tokens": overall[5] or 0,
            "per_provider": provider_stats,
        }
    except Exception as e:
        # Fail gracefully — return empty stats if DB query fails
        return {
            "total_requests": 0,
            "cache_hits": 0,
            "hit_rate": 0.0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "estimated_savings_usd": 0.0,
            "client_managed_cache_read_tokens": 0,
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
