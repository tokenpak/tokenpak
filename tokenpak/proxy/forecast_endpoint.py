"""
tokenpak.proxy.forecast_endpoint — POST /v1/messages/forecast implementation.

Estimates cost, token counts, and cache hit likelihood for a request body
identical to /v1/messages WITHOUT forwarding to the upstream API.

AC2-compliant response shape:
  {
    "estimated_cost_usd": float,
    "input_tokens": int,
    "cached_tokens": int,
    "ttfb_estimate_ms": int,
    "cache_hit_likelihood": float,
    "model": str,
  }

The response also includes a ``breakdown`` dict for backward compatibility
with existing callers that use the pre-AC2 nested structure.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import deque
from typing import Union

# ---------------------------------------------------------------------------
# Rolling latency buffer (shared with server.py via import)
# ---------------------------------------------------------------------------

_forecast_latencies: deque = deque(maxlen=100)
_forecast_latency_lock = threading.Lock()


def count_request_tokens(body: dict) -> int:
    """Count input tokens for a /v1/messages-shaped request body.

    Handles both string content and list-of-content-block shapes.
    Uses tokenpak.proxy.token_cache.count_tokens (tiktoken-backed, LRU-cached).
    """
    from tokenpak.proxy.token_cache import count_tokens

    total = 0

    system = body.get("system", "")
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

    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or ""
                    total += count_tokens(str(text))
                elif isinstance(block, str):
                    total += count_tokens(block)

    for tool in body.get("tools", []):
        if not isinstance(tool, dict):
            continue
        total += count_tokens(tool.get("name", ""))
        total += count_tokens(tool.get("description", ""))
        schema = tool.get("input_schema", {})
        if isinstance(schema, dict):
            total += count_tokens(json.dumps(schema, separators=(",", ":")))

    return total


def estimate_cache_hit_likelihood(
    model: str,
    db_path: Union[str, object],
    session_id: str = "",
    window_hours: int = 24,
) -> float:
    """Estimate cache hit likelihood from monitor.db history.

    Tries session-scoped history first (last 20 requests for the session),
    then falls back to model-scoped history over the past ``window_hours``.
    Returns 0.0 if there is no history or the DB is unavailable.
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            if session_id:
                cur = conn.execute(
                    "SELECT cache_read_tokens FROM requests "
                    "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 20",
                    (session_id,),
                )
                rows = cur.fetchall()
                if len(rows) >= 3:
                    hits = sum(1 for r in rows if (r[0] or 0) > 0)
                    return round(hits / len(rows), 4)

            row = conn.execute(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) AS hits
                   FROM requests
                   WHERE model = ? AND timestamp >= datetime('now', ?)""",
                (model, f"-{window_hours} hours"),
            ).fetchone()
            if row and row[0] and row[0] > 0:
                hits = row[1] or 0
                return round(hits / row[0], 4)
        finally:
            conn.close()
    except Exception:
        pass
    return 0.0


def estimate_ttfb_ms(
    model: str,
    input_tokens: int,
    db_path: Union[str, object],
) -> int:
    """Estimate TTFB in milliseconds from rolling latency buffer or DB history.

    Priority: rolling in-process buffer → DB 7-day average → formula fallback.
    Formula fallback: 150ms base + 0.02ms per input token.
    """
    with _forecast_latency_lock:
        lats = list(_forecast_latencies)
    if lats:
        return int(sum(lats) / len(lats))

    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            row = conn.execute(
                """SELECT AVG(latency_ms) FROM requests
                   WHERE model = ? AND latency_ms IS NOT NULL AND latency_ms > 0
                   AND timestamp >= datetime('now', '-7 days')""",
                (model,),
            ).fetchone()
            if row and row[0]:
                return max(50, int(row[0]))
        finally:
            conn.close()
    except Exception:
        pass

    return max(100, 150 + int(input_tokens * 0.02))


def build_forecast_response(
    body: dict,
    db_path: Union[str, object],
    session_id: str = "",
) -> dict:
    """Build the AC2-compliant forecast response dict from a /v1/messages body.

    Args:
        body:       Parsed JSON body (same shape as /v1/messages).
        db_path:    Path to monitor.db for cache/latency history lookups.
        session_id: Optional session header for per-session cache likelihood.

    Returns dict with AC2 fields:
        estimated_cost_usd, input_tokens, cached_tokens,
        ttfb_estimate_ms, cache_hit_likelihood, model.
    Plus backward-compat ``breakdown`` key.
    """
    from tokenpak.proxy.router import estimate_cost

    model = body.get("model") or "claude-sonnet-4-6"
    if not isinstance(model, str):
        model = "claude-sonnet-4-6"

    input_tokens = count_request_tokens(body)

    # Output estimate: respect max_tokens if small, else default 500
    max_tokens = body.get("max_tokens")
    if isinstance(max_tokens, int) and 0 < max_tokens < 500:
        output_estimate = max_tokens
    else:
        output_estimate = 500

    # Cache creates estimate from cache_control hints
    cache_creates_estimate = 0
    system = body.get("system", "")
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("cache_control"):
                text = block.get("text", "")
                if isinstance(text, str):
                    from tokenpak.proxy.token_cache import count_tokens
                    cache_creates_estimate += count_tokens(text)
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control"):
                    block_text = block.get("text", "")
                    if isinstance(block_text, str):
                        from tokenpak.proxy.token_cache import count_tokens
                        cache_creates_estimate += count_tokens(block_text)

    cache_hit_likelihood = estimate_cache_hit_likelihood(
        model, db_path, session_id=session_id
    )
    cached_tokens = int(input_tokens * cache_hit_likelihood)
    ttfb_estimate_ms = estimate_ttfb_ms(model, input_tokens, db_path)

    estimated_cost_usd = estimate_cost(
        model,
        input_tokens,
        output_estimate,
        cache_read_tokens=cached_tokens,
        cache_creation_tokens=cache_creates_estimate,
    )

    return {
        # AC2-required flat fields
        "estimated_cost_usd": round(estimated_cost_usd, 6),
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "ttfb_estimate_ms": ttfb_estimate_ms,
        "cache_hit_likelihood": cache_hit_likelihood,
        "model": model,
        # Backward-compat breakdown (pre-AC2 callers)
        "breakdown": {
            "input_tokens": input_tokens,
            "output_estimate": output_estimate,
            "cache_hits_estimate": cached_tokens,
            "cache_creates_estimate": cache_creates_estimate,
        },
    }
