"""status command — savings-first proxy health & ROI report.

Default output leads with dollar savings (v3 layout).
Use ``--full`` for legacy technical output.

Modes:
    tokenpak status            → savings-first (new default)
    tokenpak status --full     → current technical output (backward compatible)
    tokenpak status --minimal  → one-liner for scripts
    tokenpak status --json     → machine-readable JSON
    tokenpak status --no-meme  → suppress tagline
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import click

    HAS_CLICK = True
except ImportError:
    HAS_CLICK = False

# Import pricing module for per-model rates
try:
    from tokenpak.pricing import MODEL_RATES, DEFAULT_RATE, get_rates
except ImportError:
    MODEL_RATES = {
        "claude-opus-4-5": {"input": 15.0, "cached": 1.50, "output": 75.0},
        "claude-opus-4-6": {"input": 15.0, "cached": 1.50, "output": 75.0},
        "claude-sonnet-4-5": {"input": 3.0, "cached": 0.30, "output": 15.0},
        "claude-sonnet-4-6": {"input": 3.0, "cached": 0.30, "output": 15.0},
        "claude-haiku-4-5": {"input": 0.80, "cached": 0.08, "output": 4.0},
        "claude-haiku-4-6": {"input": 0.80, "cached": 0.08, "output": 4.0},
        "gpt-4o": {"input": 2.50, "cached": 1.25, "output": 10.0},
        "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.60},
    }
    DEFAULT_RATE = {"input": 3.0, "cached": 0.30, "output": 15.0}

    def get_rates(model: Optional[str] = None) -> dict:
        if not model:
            return DEFAULT_RATE
        return MODEL_RATES.get(model, DEFAULT_RATE)


# ---------------------------------------------------------------------------
# Meme lines — 28 curated by Kevin, random pick per invocation
# ---------------------------------------------------------------------------

MEME_LINES = [
    "Keep my tokens out yo damn prompt.",
    "Your API bill called. It's crying.",
    "Caching harder than your ex caches grudges.",
    "We don't do full price around here.",
    "Less tokens, more problems solved.",
    "Your wallet says thanks.",
    "Built different. Billed different.",
    "Making Anthropic wonder where the traffic went.",
    "Every token saved is a token earned.",
    "Compression: because your prompts are 90% filler.",
    "Cache hits > cache fits.",
    "TokenPak: putting tokens on a diet since 2026.",
    "Your prompt was long. We made it strong.",
    "Running lean so you don't run broke.",
    "Saving tokens while you sleep.",
    "Less input, same output. That's the deal.",
    "Prompt obesity is a real condition.",
    "We compress so you don't stress.",
    "Cache is king. Tokens are pawns.",
    "Your bill just got TokenPak'd.",
    "Proxy running. Savings stacking.",
    "Token diet starts now.",
    "Why pay full price? We don't.",
    "Smart routing > dumb spending.",
    "Compressing prompts. Expanding wallets.",
    "Vault blocks: loaded. Savings: automatic.",
    "Turning token waste into token taste.",
    "Your API provider hates this one trick.",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEP = "────────────────────────────────────────"
SEP_INNER = "─────────────────────────────────"
PROXY_DEFAULT = "http://127.0.0.1:8766"
DB_DEFAULT = os.environ.get(
    "TOKENPAK_DB",
    os.path.expanduser("~/tokenpak/monitor.db"),
)


# ---------------------------------------------------------------------------
# Network / DB helpers
# ---------------------------------------------------------------------------


def _fetch(url: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    """Fetch JSON from a URL. Returns None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _get_db_path() -> str:
    """Resolve the monitor DB path."""
    # Check env var first, then common locations
    for candidate in [
        os.environ.get("TOKENPAK_DB", ""),
        os.path.expanduser("~/tokenpak/monitor.db"),
        os.path.expanduser("~/.tokenpak/data/monitor.db"),
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return DB_DEFAULT


def _connect_db(db_path: Optional[str] = None) -> Optional[sqlite3.Connection]:
    """Open monitor.db. Returns None if not found."""
    path = db_path or _get_db_path()
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Version helper
# ---------------------------------------------------------------------------


def _get_version() -> str:
    """Get tokenpak version string."""
    try:
        import tokenpak
        ver = getattr(tokenpak, "__version__", None)
        if ver:
            return "v" + ver
    except Exception:
        pass
    try:
        import importlib.metadata
        return "v" + importlib.metadata.version("tokenpak")
    except Exception:
        pass
    return "v1.0.x"


# ---------------------------------------------------------------------------
# Fleet savings calculation (inline — TPK-SAVINGS-001 not yet available)
# ---------------------------------------------------------------------------


def _calculate_fleet_savings(
    db_path: Optional[str] = None,
    period: Optional[str] = "24h",
) -> Dict[str, Any]:
    """Query monitor.db for savings data grouped by model.

    Uses pricing.MODEL_RATES for correct per-model pricing instead of
    naive cost-per-token averaging.

    Args:
        db_path: Path to monitor.db (auto-detected if None)
        period: '1h', '24h', '7d', '30d', or None for all-time

    Returns:
        Dict with keys: models (list), totals (dict), period (str)
    """
    conn = _connect_db(db_path)
    if conn is None:
        return {"error": "db_not_found", "db_path": db_path or _get_db_path()}

    # Build time filter
    period_map = {
        "1h": "-1 hours",
        "24h": "-1 days",
        "7d": "-7 days",
        "30d": "-30 days",
    }
    where_clause = ""
    params: list = []
    if period and period in period_map:
        where_clause = "WHERE timestamp >= datetime('now', ?)"
        params = [period_map[period]]

    try:
        rows = conn.execute(
            f"""
            SELECT
                model,
                COUNT(*) AS requests,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                COALESCE(SUM(compressed_tokens), 0) AS compressed_tokens,
                COALESCE(SUM(protected_tokens), 0) AS protected_tokens,
                COALESCE(SUM(estimated_cost), 0.0) AS estimated_cost
            FROM requests
            {where_clause}
            GROUP BY model
            ORDER BY SUM(input_tokens) DESC
            """,
            params,
        ).fetchall()
    except Exception as e:
        conn.close()
        return {"error": str(e)}

    # Also get total row count
    try:
        total_rows = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    except Exception:
        total_rows = 0

    conn.close()

    if not rows:
        return {
            "error": "no_data",
            "period": period,
            "db_path": db_path or _get_db_path(),
        }

    # Calculate per-model savings using real rates
    models: List[Dict[str, Any]] = []
    total_without = 0.0
    total_with = 0.0
    total_cache_savings = 0.0
    total_compression_savings = 0.0
    total_requests = 0

    for row in rows:
        model_name = row["model"]
        rates = get_rates(model_name)
        input_rate = rates["input"]
        cached_rate = rates["cached"]
        output_rate = rates["output"]

        req_count = row["requests"]
        input_tok = row["input_tokens"]      # post-compression tokens actually sent
        output_tok = row["output_tokens"]
        cache_read = row["cache_read_tokens"]
        cache_create = row["cache_creation_tokens"]
        compressed_tok = row["compressed_tokens"]  # tokens removed by compression

        # "Without TokenPak" cost:
        # All input tokens (including compressed + cache_read) at full input rate
        # + output at output rate
        raw_input = input_tok + compressed_tok  # pre-compression input
        baseline_input = raw_input + cache_read  # if no caching, all would be fresh input
        without_cost = (
            (baseline_input / 1_000_000) * input_rate
            + (output_tok / 1_000_000) * output_rate
        )

        # "With TokenPak" cost:
        # Fresh input at input rate + cache reads at cached rate + output at output rate
        with_cost = (
            (input_tok / 1_000_000) * input_rate
            + (cache_read / 1_000_000) * cached_rate
            + (output_tok / 1_000_000) * output_rate
        )

        saved = without_cost - with_cost
        pct = (saved / without_cost * 100) if without_cost > 0 else 0.0

        # Breakdown: cache savings vs compression savings
        cache_saving = (cache_read / 1_000_000) * (input_rate - cached_rate)
        compression_saving = (compressed_tok / 1_000_000) * input_rate

        # Cache hit rate: cache_read / (cache_read + input_tok) — what % of input was cached
        total_input_handled = cache_read + input_tok
        cache_hit_rate = (cache_read / total_input_handled * 100) if total_input_handled > 0 else 0.0

        models.append({
            "model": model_name,
            "requests": req_count,
            "without_cost": round(without_cost, 2),
            "with_cost": round(with_cost, 2),
            "saved": round(saved, 2),
            "savings_pct": round(pct, 1),
            "cache_hit_rate": round(cache_hit_rate, 1),
            "cache_savings": round(cache_saving, 2),
            "compression_savings": round(compression_saving, 2),
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "cache_read_tokens": cache_read,
            "compressed_tokens": compressed_tok,
        })

        total_without += without_cost
        total_with += with_cost
        total_cache_savings += cache_saving
        total_compression_savings += compression_saving
        total_requests += req_count

    total_saved = total_without - total_with
    total_pct = (total_saved / total_without * 100) if total_without > 0 else 0.0

    # Smart routing savings = total_saved - cache - compression (remainder)
    routing_savings = max(0.0, total_saved - total_cache_savings - total_compression_savings)

    return {
        "period": period,
        "models": models,
        "totals": {
            "requests": total_requests,
            "without_cost": round(total_without, 2),
            "with_cost": round(total_with, 2),
            "saved": round(total_saved, 2),
            "savings_pct": round(total_pct, 1),
            "cache_savings": round(total_cache_savings, 2),
            "compression_savings": round(total_compression_savings, 2),
            "routing_savings": round(routing_savings, 2),
        },
        "db_rows": total_rows,
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_cost(amount: float) -> str:
    """Format dollar amount for display."""
    if amount >= 1000:
        return f"${amount:,.0f}"
    if amount >= 100:
        return f"${amount:,.1f}"
    if amount >= 1:
        return f"${amount:,.2f}"
    return f"${amount:.2f}"


def _fmt_pct(pct: float) -> str:
    """Format percentage with alignment-friendly padding."""
    s = f"{pct:.1f}%"
    return s


def _fmt_uptime(seconds: float) -> str:
    """Format uptime seconds to human string."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes:02d}m"
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _shorten_model(name: str) -> str:
    """Shorten model name for table display."""
    # Remove common prefixes for compact display
    return name


# ---------------------------------------------------------------------------
# Savings-first default output (v3 layout)
# ---------------------------------------------------------------------------


def run(
    proxy_base: str = PROXY_DEFAULT,
    raw: bool = False,
    minimal: bool = False,
    full: bool = False,
    as_json: bool = False,
    no_meme: bool = False,
    db_path: Optional[str] = None,
) -> None:
    """Print savings-first status (v3 layout) to stdout.

    This is the new default. The old technical output is accessible
    via ``full=True`` (``--full`` flag).
    """
    # Dispatch to sub-modes
    if full:
        return run_full(proxy_base=proxy_base, raw=raw, minimal=minimal)
    if as_json:
        return _run_json(proxy_base=proxy_base, db_path=db_path)
    if minimal:
        return _run_minimal(proxy_base=proxy_base, db_path=db_path, no_meme=no_meme)

    # --- Fetch proxy health (optional — savings still work without it) ---
    health = _fetch(f"{proxy_base}/health")
    stats = _fetch(f"{proxy_base}/stats")
    cache = _fetch(f"{proxy_base}/cache-stats")
    proxy_up = health is not None

    # --- Fetch savings from DB ---
    savings_24h = _calculate_fleet_savings(db_path=db_path, period="24h")
    savings_1h = _calculate_fleet_savings(db_path=db_path, period="1h")
    savings_all = _calculate_fleet_savings(db_path=db_path, period=None)

    version = _get_version()

    # --- Handle empty DB ---
    if savings_24h.get("error") == "db_not_found":
        print(f"\nTOKENPAK {version}  |  Savings Report")
        print(SEP)
        print()
        print("  ⚠️  Monitor database not found.")
        print(f"     Expected: {savings_24h.get('db_path', DB_DEFAULT)}")
        print()
        print("  Run some requests through the proxy to start tracking.")
        print("  Then try `tokenpak status` again.")
        print()
        return

    if savings_24h.get("error") == "no_data":
        print(f"\nTOKENPAK {version}  |  Savings Report")
        print(SEP)
        print()
        print("  📭 No savings data yet for the last 24 hours.")
        print()
        print("  Run some requests through the proxy to start tracking.")
        print("  If you just started, give it a few minutes.")
        print()
        if not proxy_up:
            print("  ⚠️  Proxy unreachable — start it with `tokenpak serve`")
            print()
        return

    if savings_24h.get("error"):
        print(f"\nTOKENPAK {version}  |  Savings Report")
        print(SEP)
        print()
        print(f"  ❌ Error querying savings: {savings_24h['error']}")
        print()
        return

    # --- Render v3 layout ---
    totals = savings_24h["totals"]
    model_rows = savings_24h["models"]

    print(f"\nTOKENPAK {version}  |  Savings Report")
    print(SEP)

    # === SAVINGS SECTION ===
    print()
    print("💰 SAVINGS (Last 24h)")
    print(f"  Without TokenPak:    {_fmt_cost(totals['without_cost']):>10}")
    print(f"  With TokenPak:       {_fmt_cost(totals['with_cost']):>10}")
    print(f"  {SEP_INNER}")
    print(f"  Total saved:         {_fmt_cost(totals['saved']):>10}  ({_fmt_pct(totals['savings_pct'])})")

    # === HOW IT SAVED BREAKDOWN ===
    print()
    print("📊 HOW IT SAVED")
    cache_sav = totals["cache_savings"]
    comp_sav = totals["compression_savings"]
    route_sav = totals["routing_savings"]
    total_sav = totals["saved"]

    cache_pct = (cache_sav / total_sav * 100) if total_sav > 0 else 0.0
    comp_pct = (comp_sav / total_sav * 100) if total_sav > 0 else 0.0
    route_pct = (route_sav / total_sav * 100) if total_sav > 0 else 0.0

    print(f"  Cache optimization:    {_fmt_cost(cache_sav):>10}  ({cache_pct:4.1f}%)")
    print(f"  Token compression:     {_fmt_cost(comp_sav):>10}  ({comp_pct:4.1f}%)")
    print(f"  Smart routing:         {_fmt_cost(route_sav):>10}  ({route_pct:4.1f}%)")

    # === PER-MODEL TABLE ===
    print()
    print("🤖 MODELS")
    for m in model_rows:
        name = _shorten_model(m["model"])
        reqs = m["requests"]
        saved = m["saved"]
        cache_hr = m["cache_hit_rate"]
        sav_pct = m["savings_pct"]
        print(
            f"  {name:<24} {reqs:>5} reqs"
            f"    {_fmt_cost(saved):>10}"
            f"    {cache_hr:3.0f}% cache"
            f"    {sav_pct:3.0f}% saved"
        )

    # === PERFORMANCE SECTION ===
    print()
    print("⚡ PERFORMANCE")

    total_reqs = totals["requests"]
    db_rows = savings_24h.get("db_rows", 0)

    # Uptime from proxy health
    if proxy_up and health:
        s = health.get("stats", {})
        start_time = s.get("start_time", time.time())
        uptime_s = time.time() - start_time
        uptime_str = _fmt_uptime(uptime_s)
    else:
        uptime_str = "n/a"

    # Cache hit rate from live proxy
    if cache:
        hits = cache.get("cache_hits", 0)
        misses = cache.get("cache_misses", 0)
        total_cache = hits + misses
        hit_rate = (hits / total_cache * 100) if total_cache > 0 else 0.0
    else:
        # Estimate from DB data
        total_cache_read = sum(m.get("cache_read_tokens", 0) for m in model_rows)
        total_input = sum(m.get("input_tokens", 0) for m in model_rows)
        total_handled = total_cache_read + total_input
        hit_rate = (total_cache_read / total_handled * 100) if total_handled > 0 else 0.0

    # Errors from proxy
    errors = 0
    if proxy_up and health:
        errors = health.get("stats", {}).get("errors", 0)

    # Avg latency from proxy
    latency_str = "n/a"
    if proxy_up and stats:
        session = stats.get("session", {})
        avg_lat = session.get("avg_latency_ms", 0)
        if avg_lat > 0:
            latency_str = f"+{avg_lat:.0f}ms"

    print(f"  Requests:   {total_reqs:>6,}  |  Uptime: {uptime_str}")
    print(f"  Cache hit:  {hit_rate:>5.0f}%  |  Avg latency: {latency_str}")
    print(f"  Errors:     {errors:>6,}  |  DB rows: {db_rows:,}")

    # === SAVINGS VELOCITY ===
    print()
    last_hour_saved = 0.0
    if not savings_1h.get("error"):
        last_hour_saved = savings_1h["totals"]["saved"]

    all_time_saved = 0.0
    if not savings_all.get("error"):
        all_time_saved = savings_all["totals"]["saved"]

    print(f"  Last hour:  {_fmt_cost(last_hour_saved)} saved  |  All-time:  {_fmt_cost(all_time_saved)} saved")

    # === HEALTH STATUS ===
    print()
    if not proxy_up:
        print("⚠️  Proxy unreachable — showing historical data only")
    elif errors > 0:
        print(f"⚠️  {errors} error(s) this session — run `tokenpak doctor` for details")
    else:
        print("✅ All systems healthy")

    # === MEME LINE ===
    if not no_meme:
        print()
        meme = random.choice(MEME_LINES)
        print(f"📦 {meme}")

    print()


# ---------------------------------------------------------------------------
# Minimal output (one-liner for scripts/dashboards)
# ---------------------------------------------------------------------------


def _run_minimal(
    proxy_base: str = PROXY_DEFAULT,
    db_path: Optional[str] = None,
    no_meme: bool = False,
) -> None:
    """Print one-line savings summary."""
    savings = _calculate_fleet_savings(db_path=db_path, period="24h")

    if savings.get("error"):
        print("📦 TokenPak: no data yet")
        return

    t = savings["totals"]
    saved = _fmt_cost(t["saved"])
    pct = t["savings_pct"]
    reqs = t["requests"]

    # Cache hit rate from DB
    model_rows = savings.get("models", [])
    total_cache_read = sum(m.get("cache_read_tokens", 0) for m in model_rows)
    total_input = sum(m.get("input_tokens", 0) for m in model_rows)
    total_handled = total_cache_read + total_input
    cache_pct = (total_cache_read / total_handled * 100) if total_handled > 0 else 0.0

    line = f"📦 TokenPak: {saved} saved ({pct:.0f}%) | {reqs:,} reqs | {cache_pct:.0f}% cache"
    if not no_meme:
        meme = random.choice(MEME_LINES)
        line += f" — {meme}"

    print(line)


# ---------------------------------------------------------------------------
# JSON output (machine-readable full dump)
# ---------------------------------------------------------------------------


def _run_json(
    proxy_base: str = PROXY_DEFAULT,
    db_path: Optional[str] = None,
) -> None:
    """Dump all status data as JSON."""
    health = _fetch(f"{proxy_base}/health")
    stats = _fetch(f"{proxy_base}/stats")
    cache = _fetch(f"{proxy_base}/cache-stats")

    savings_24h = _calculate_fleet_savings(db_path=db_path, period="24h")
    savings_1h = _calculate_fleet_savings(db_path=db_path, period="1h")
    savings_all = _calculate_fleet_savings(db_path=db_path, period=None)

    output = {
        "version": _get_version(),
        "proxy": {
            "reachable": health is not None,
            "health": health,
            "stats": stats,
            "cache": cache,
        },
        "savings": {
            "last_24h": savings_24h if not savings_24h.get("error") else None,
            "last_1h": savings_1h if not savings_1h.get("error") else None,
            "all_time": savings_all if not savings_all.get("error") else None,
        },
        "meme_lines": MEME_LINES,
    }
    print(json.dumps(output, indent=2, default=str))


# ---------------------------------------------------------------------------
# Full/legacy output (backward compat — original run() renamed)
# ---------------------------------------------------------------------------


def run_full(
    proxy_base: str = PROXY_DEFAULT,
    raw: bool = False,
    minimal: bool = False,
) -> None:
    """Print legacy proxy status to stdout (original output, backward compat)."""
    SEP_LEGACY = "────────────────────────────────────"

    # --- Fetch health ---
    health = _fetch(f"{proxy_base}/health")
    if health is None:
        print(f"⛔️  TokenPak proxy unreachable at {proxy_base}")
        print("    What happened:  The proxy is not running or crashed.")
        print(f"    Why:            Connection refused on port {proxy_base.split(':')[-1]}.")
        print("    What to do:     Run `tokenpak serve` to start it, or")
        print("                    `tokenpak doctor` to diagnose the issue.")
        sys.exit(1)

    if raw:
        # Fetch everything and dump
        session = _fetch(f"{proxy_base}/stats/session") or {}
        deg = _fetch(f"{proxy_base}/degradation") or {}
        print(json.dumps({"health": health, "session": session, "degradation": deg}, indent=2))
        return

    # --- Fetch session stats ---
    session = _fetch(f"{proxy_base}/stats/session") or {}
    deg = _fetch(f"{proxy_base}/degradation") or {}

    # Core fields
    is_degraded = health.get("is_degraded", False)
    status_icon = "⚠️ " if is_degraded else "●"
    status_text = "DEGRADED" if is_degraded else "Active"
    uptime_s = health.get("uptime_seconds", 0)
    uptime_h = uptime_s // 3600
    uptime_m = (uptime_s % 3600) // 60
    uptime_str = f"{uptime_h}h {uptime_m}m" if uptime_h else f"{uptime_m}m"

    requests = session.get("session_requests", 0)
    tokens_saved = session.get("tokens_saved", 0)
    tokens_raw = session.get("tokens_raw", 0)
    total_cost = session.get("total_cost", 0.0)
    cost_saved = session.get("session_total_saved", 0.0)
    avg_savings = session.get("avg_savings_pct", 0.0)
    errors = session.get("errors", 0)
    compression_avg = health.get("compression_ratio_avg", 0.0)

    if minimal:
        mark = "⚠️ DEGRADED" if is_degraded else "● Active"
        pct = f"{avg_savings:.1f}% saved" if tokens_raw else "n/a"
        print(f"{mark} | {requests:,} req | {pct}")
        return

    print(f"\nTOKENPAK  |  Status (Full)")
    print(SEP_LEGACY)

    print(f"{'✅  Proxy running':<28}port {proxy_base.split(':')[-1]} — hybrid mode")
    print(f"{'✅  Uptime':<28}{uptime_str}")
    print(
        f"{'✅  Health':<28}OK (0 errors)" if errors == 0 else f"{'⚠️  Health':<28}{errors} errors"
    )
    print()

    # Import estimate_savings if available
    try:
        from tokenpak.pricing import estimate_savings
    except ImportError:
        estimate_savings = None

    # Calculate and display savings summary
    if estimate_savings and session:
        savings_data = estimate_savings(session)
        print("💰  Session Savings")
        print(f"    Requests:      {requests:,}")
        print(f"    Input tokens:  {tokens_raw:,}")
        print(f"    Tokens saved:  {tokens_saved:,} ({avg_savings:.1f}% compression)")
        print(
            f"    Cache reads:   {session.get('cache_read_tokens', 0):,} ({savings_data.get('cache_hit_rate', 0):.0f}% hit rate)"
        )
        print(f"    Est. saved:    ${savings_data.get('total_cost_saved', 0):.2f}")
        print()
    else:
        # Fallback without pricing module
        print(f"{'Session Requests:':<28}{requests:,}")
        print(f"{'Errors:':<28}{errors:,}")
        print(f"{'Tokens (raw):':<28}{tokens_raw:,}")
        print(f"{'Tokens (saved):':<28}{tokens_saved:,}")
        print(f"{'Avg Compression:':<28}{avg_savings:.1f}%  (ratio {compression_avg:.3f})")
        print(f"{'Cost (this session):':<28}${total_cost:.4f}")
        print(f"{'Cost Saved:':<28}${cost_saved:.4f}")
        print()

    # --- Degradation block ---
    if is_degraded or deg.get("recent_events"):
        print()
        print(SEP_LEGACY)
        deg.get("status", "unknown")
        deg_msg = deg.get("message", "")
        print(f"{'Degradation:':<28}{deg_msg}")

        comp_fail = deg.get("lifetime_compression_failures", 0)
        fo = deg.get("lifetime_provider_failovers", 0)
        if comp_fail or fo:
            print(f"{'Compression failures:':<28}{comp_fail}")
            print(f"{'Provider failovers:':<28}{fo}")

        recent = deg.get("recent_events", [])
        if recent:
            print()
            print("Recent degradation events:")
            for ev in recent[:5]:
                ts = ev.get("timestamp", "")[:19].replace("T", " ")
                etype = ev.get("event_type", "?")
                detail = ev.get("detail", "")
                recovered = "✅" if ev.get("recovered") else "❌"
                print(f"  {recovered} [{ts}] {etype}: {detail[:70]}")

    print(SEP_LEGACY)
    if is_degraded:
        print("ℹ️  Running degraded — requests still served. Run `tokenpak doctor` for details.")
    else:
        print("ℹ️  Run `tokenpak status` for savings overview.")
    print()


# ---------------------------------------------------------------------------
# Click CLI integration
# ---------------------------------------------------------------------------

if HAS_CLICK:
    import click

    @click.command("status")
    @click.option(
        "--proxy",
        default=PROXY_DEFAULT,
        envvar="TOKENPAK_PROXY_URL",
        help="Proxy base URL",
    )
    @click.option("--full", is_flag=True, help="Show full technical output (legacy format)")
    @click.option("--raw", is_flag=True, help="Dump raw JSON (with --full)")
    @click.option("--minimal", is_flag=True, help="One-line savings summary")
    @click.option("--json", "as_json", is_flag=True, help="Full JSON data dump")
    @click.option("--no-meme", is_flag=True, help="Suppress tagline")
    @click.option("--db", "db_path", default=None, help="Monitor DB path override")
    def status_cmd(
        proxy: str,
        full: bool,
        raw: bool,
        minimal: bool,
        as_json: bool,
        no_meme: bool,
        db_path: Optional[str],
    ) -> None:
        """Show savings report (default) or full technical status.

        Default output leads with dollar savings — the number that matters.
        Use --full for the legacy technical output.

        Examples:

        \\b
          tokenpak status               # savings-first (v3)
          tokenpak status --full        # legacy technical output
          tokenpak status --minimal     # one-liner for scripts
          tokenpak status --json        # machine-readable
          tokenpak status --no-meme     # suppress tagline
        """
        run(
            proxy_base=proxy,
            raw=raw,
            minimal=minimal,
            full=full,
            as_json=as_json,
            no_meme=no_meme,
            db_path=db_path,
        )
