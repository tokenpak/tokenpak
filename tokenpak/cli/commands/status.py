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
    from tokenpak.telemetry.pricing import MODEL_RATES, DEFAULT_RATE, get_rates
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
    elif period and period.endswith("h_custom"):
        # Custom hour filter from --days/--hours flags
        hours = int(period.replace("h_custom", ""))
        where_clause = "WHERE timestamp >= datetime('now', ?)"
        params = [f"-{hours} hours"]

    # Check if 'route' column exists in the requests table (legacy DBs lack it)
    col_names = {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
    has_route = "route" in col_names

    route_expr = (
        "COALESCE(SUM(CASE WHEN COALESCE(route, '') = 'claude-code' THEN cache_read_tokens ELSE 0 END), 0)"
        if has_route
        else "0"
    )

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
                COALESCE(SUM(estimated_cost), 0.0) AS estimated_cost,
                {route_expr} AS client_managed_cache_read
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

    total_claude_code_savings = 0.0

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

        # Attribution: separate proxy-managed cache reads (tokenpak caused) from
        # client-managed reads (Claude Code caused — tokenpak just observed).
        #
        # When the DB has a 'route' column, rows tagged 'claude-code' are
        # client-managed.  When the column is missing (legacy DB), ALL traffic
        # flowed through the byte-preserved passthrough proxy, meaning Claude
        # Code's own cache_control headers caused the cache hits — not tokenpak.
        # In that case, conservatively attribute all cache reads to Claude Code.
        if has_route:
            client_managed_cr = row["client_managed_cache_read"] if "client_managed_cache_read" in row.keys() else 0
        else:
            # No route column → assume all cache is client-managed (Claude Code)
            client_managed_cr = cache_read
        proxy_managed_cr = max(0, cache_read - client_managed_cr)

        # "Without TokenPak" cost: what you'd pay if tokenpak hadn't compressed.
        # - Compressed tokens would have been sent at full input rate
        # - Proxy-managed cache reads would have been full-price input (tokenpak caused the discount)
        # - Client-managed cache reads stay at cached rate (Claude Code does this regardless)
        # - Output at output rate
        raw_input = input_tok + compressed_tok  # pre-compression input
        baseline_input = raw_input + proxy_managed_cr  # only proxy-managed cache was tokenpak's doing
        without_cost = (
            (baseline_input / 1_000_000) * input_rate
            + (client_managed_cr / 1_000_000) * cached_rate
            + (output_tok / 1_000_000) * output_rate
        )

        # "With TokenPak" cost:
        # Fresh input at input rate + all cache reads at cached rate + output at output rate
        with_cost = (
            (input_tok / 1_000_000) * input_rate
            + (cache_read / 1_000_000) * cached_rate
            + (output_tok / 1_000_000) * output_rate
        )

        saved = without_cost - with_cost
        pct = (saved / without_cost * 100) if without_cost > 0 else 0.0

        # Breakdown: only proxy-managed cache counts as tokenpak savings
        cache_saving = (proxy_managed_cr / 1_000_000) * (input_rate - cached_rate)
        compression_saving = (compressed_tok / 1_000_000) * input_rate

        # Claude Code cache savings (observability — not tokenpak's doing)
        claude_code_cache_saving = (client_managed_cr / 1_000_000) * (input_rate - cached_rate)

        # Cache hit rate: all cache_read / total input handled (observability, not attribution)
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
            "claude_code_cache_savings": round(claude_code_cache_saving, 2),
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "cache_read_tokens": cache_read,
            "proxy_managed_cache_read": proxy_managed_cr,
            "client_managed_cache_read": client_managed_cr,
            "compressed_tokens": compressed_tok,
        })

        total_without += without_cost
        total_with += with_cost
        total_cache_savings += cache_saving
        total_compression_savings += compression_saving
        total_claude_code_savings += claude_code_cache_saving
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
            "claude_code_cache_savings": round(total_claude_code_savings, 2),
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


def _fmt_num(n: int) -> str:
    """Compact number: 1234 -> 1.2K, 1234567 -> 1.2M, etc."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    if n >= 1_000:
        return f"{n:,}"
    return str(n)


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
    by_source: bool = False,
    by_provider: bool = False,
    as_json: bool = False,
    no_meme: bool = False,
    db_path: Optional[str] = None,
    days: int = 0,
    hours: int = 0,
) -> None:
    """Print savings-first status to stdout.

    Flags:
      --full         Expanded default view with all sections
      --by-source    Breakdown by request source (Claude Code, Codex, API, etc.)
      --by-provider  Breakdown by provider (Anthropic, OpenAI, etc.)
      --minimal      One-line summary
      --json         Machine-readable JSON dump
      --days N       Filter to last N days
      --hours N      Filter to last N hours (combinable with --days)
    """
    if as_json:
        return _run_json(proxy_base=proxy_base, db_path=db_path)
    if minimal:
        return _run_minimal(proxy_base=proxy_base, db_path=db_path, no_meme=no_meme)
    if by_source:
        return _run_by_source(proxy_base=proxy_base, db_path=db_path)
    if by_provider:
        return _run_by_provider(proxy_base=proxy_base, db_path=db_path)

    # --- Fetch live proxy data (primary source for current session) ---
    health = _fetch(f"{proxy_base}/health")
    stats = _fetch(f"{proxy_base}/stats")
    cache = _fetch(f"{proxy_base}/cache-stats")
    proxy_up = health is not None
    session = stats.get("session", {}) if stats else {}

    # --- Fetch DB data (historical, with optional time filter) ---
    total_hours = days * 24 + hours
    if total_hours > 0:
        db_period = f"{total_hours}h_custom"
    else:
        db_period = None  # all time
    savings_all = _calculate_fleet_savings(db_path=db_path, period=db_period)

    version = _get_version()

    # --- Derive metrics from live proxy when available ---
    if proxy_up and session:
        total_reqs = session.get("requests", 0)
        input_tok = session.get("input_tokens", 0)
        sent_tok = session.get("sent_input_tokens", 0)
        output_tok = session.get("output_tokens", 0)
        cache_read_tok = session.get("cache_read_tokens", 0)
        cache_create_tok = session.get("cache_creation_tokens", 0)
        saved_tok = session.get("saved_tokens", 0)
        cost = session.get("cost", 0.0)
        errors = session.get("errors", 0)
        injected_tok = session.get("injected_tokens", 0)
        injection_hits = session.get("injection_hits", 0)
        start_time = session.get("start_time", time.time())
        uptime_s = time.time() - start_time
        source_label = "this session"
    else:
        # Fall back to DB
        if savings_all.get("error"):
            print(f"\nTOKENPAK {version}")
            print(SEP)
            if savings_all.get("error") == "db_not_found":
                print("\n  Proxy unreachable and no monitor database found.")
                print("  Start the proxy with `tokenpak serve`.\n")
            else:
                print(f"\n  {savings_all['error']}\n")
            return
        t = savings_all["totals"]
        total_reqs = t["requests"]
        input_tok = sum(m["input_tokens"] for m in savings_all["models"])
        sent_tok = input_tok  # can't distinguish from DB without route
        saved_tok = sum(m["compressed_tokens"] for m in savings_all["models"])
        output_tok = sum(m["output_tokens"] for m in savings_all["models"])
        cache_read_tok = sum(m["cache_read_tokens"] for m in savings_all["models"])
        cache_create_tok = 0
        cost = t["with_cost"]
        errors = 0
        injected_tok = 0
        injection_hits = 0
        uptime_s = 0
        if total_hours > 0:
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0:
                parts.append(f"{hours}h")
            source_label = f"last {' '.join(parts)}"
        else:
            source_label = "all time"

    # --- Compute attribution ---
    tp_compression_usd = 0.0
    total_cache_usd = 0.0
    if total_reqs > 0:
        total_billed_input = sent_tok + cache_read_tok
        avg_input_rate = (cost / (total_billed_input / 1_000_000)) if total_billed_input > 0 else 3.0
        avg_input_rate = min(avg_input_rate, 20.0)
        tp_compression_usd = (saved_tok / 1_000_000) * avg_input_rate
        total_cache_usd = (cache_read_tok / 1_000_000) * avg_input_rate * 0.9

    # TokenPak compression %: tokens avoided out of what would have been sent
    raw_input = sent_tok + saved_tok
    tp_compression_pct = (saved_tok / raw_input * 100) if raw_input > 0 else 0.0

    # Cache %: cache_read out of total input handled by provider
    total_input_handled = sent_tok + cache_read_tok
    provider_cache_pct = (cache_read_tok / total_input_handled * 100) if total_input_handled > 0 else 0.0

    # --- TokenPak cache attribution ---
    # tokenpak actively manages cache behavior via:
    # - apply_stable_cache_control: places cache_control breakpoints at system
    #   prompt, tools, conversation midpoint, second-to-last assistant
    # - tool_schema_registry: normalizes tool JSON to byte-identical across
    #   requests, preventing cache busts from non-deterministic ordering
    # - classify_system_blocks: separates stable vs volatile content, placing
    #   breakpoints before volatile blocks so stable prefix stays cached
    # Pull tokenpak-specific cache stats from the live proxy's /cache-stats
    tp_cache_hit_rate = 0.0
    tp_cache_misses_prevented = 0
    tp_cache_hits = 0
    tp_cache_misses = 0
    if cache:
        tp_cache_hits = cache.get("cache_hits", 0)
        tp_cache_misses = cache.get("cache_misses", 0)
        tp_total = tp_cache_hits + tp_cache_misses
        tp_cache_hit_rate = (tp_cache_hits / tp_total * 100) if tp_total > 0 else 0.0
        # Schema changes absorbed = misses prevented by tool normalization
        tp_cache_misses_prevented = health.get("tool_schema_registry", {}).get("schema_changes", 0) if health else 0

    # =====================================================================
    # RENDER
    # =====================================================================

    print(f"\n  TOKENPAK {version}")
    print(SEP)

    # --- 1. VALUE CREATED ---
    tp_total_usd = tp_compression_usd + total_cache_usd
    print()
    print(f"  💰 Value Created ({source_label})")
    print(f"     Total saved           {_fmt_cost(tp_total_usd):>10}")
    print(f"       Compression         {_fmt_cost(tp_compression_usd):>10}   {tp_compression_pct:4.1f}% token reduction")
    print(f"       Cache management    {_fmt_cost(total_cache_usd):>10}   {tp_cache_hit_rate:.0f}% hit rate")
    if saved_tok > 0:
        print(f"     Tokens compressed    {_fmt_num(saved_tok):>10}   of {_fmt_num(raw_input)} input")
    if injected_tok > 0:
        print(f"     Vault injected       {_fmt_num(injected_tok):>10}   across {injection_hits} requests")

    # --- 2. TRAFFIC ---
    print()
    print(f"  📡 Traffic")
    print(f"     Requests             {total_reqs:>10,}")
    print(f"     Input tokens         {_fmt_num(sent_tok):>10}   sent to provider")
    print(f"     Output tokens        {_fmt_num(output_tok):>10}")
    print(f"     Cost                 {_fmt_cost(cost):>10}")

    # --- 3. CACHE ---
    print()
    total_cache_handled = sent_tok + cache_read_tok
    print(f"  🔄 Cache")
    print(f"     Token cache rate     {provider_cache_pct:>9.0f}%   {_fmt_num(cache_read_tok)} of {_fmt_num(total_cache_handled)} input tokens")
    print(f"     Request hit rate     {tp_cache_hit_rate:>9.0f}%   {tp_cache_hits:,} of {tp_cache_hits + tp_cache_misses:,} requests")
    if tp_cache_misses_prevented > 0:
        print(f"     Schema normalized    {tp_cache_misses_prevented:>10}   tool changes absorbed")
    if cache:
        miss_reasons = cache.get("miss_reasons", {})
        if miss_reasons:
            top_reason = max(miss_reasons, key=miss_reasons.get)
            top_count = miss_reasons[top_reason]
            print(f"     Top miss reason      {top_count:>10}   {top_reason.replace('_', ' ')}")

    # --- 4. MODELS ---
    if not savings_all.get("error") and savings_all.get("models"):
        model_rows = savings_all["models"]
        show_limit = len(model_rows) if full else 6
        print()
        print(f"  🤖 Models (all time)")
        print(f"     {'Model':<26} {'Reqs':>6}  {'Input':>8}  {'Cache%':>6}  {'Compressed':>10}")
        print(f"     {'─' * 26} {'─' * 6}  {'─' * 8}  {'─' * 6}  {'─' * 10}")
        for m in model_rows[:show_limit]:
            name = m["model"]
            reqs = m["requests"]
            inp = m["input_tokens"]
            cr = m["cache_read_tokens"]
            comp = m.get("compressed_tokens", 0)
            total_h = inp + cr
            c_pct = (cr / total_h * 100) if total_h > 0 else 0.0
            print(
                f"     {name:<26} {reqs:>6}  {_fmt_num(inp):>8}"
                f"  {c_pct:>5.0f}%  {_fmt_num(comp):>10}"
            )
        if not full and len(model_rows) > 6:
            print(f"     ... +{len(model_rows) - 6} more (use --full to see all)")

    # --- 4b. FULL: by-source and by-provider summaries inline ---
    if full:
        _print_by_source_inline(db_path)
        _print_by_provider_inline(db_path)

    # --- 5. PERFORMANCE ---
    print()
    print(f"  ⚡ Performance")
    uptime_str = _fmt_uptime(uptime_s) if uptime_s > 0 else "n/a"
    latency_str = "n/a"
    if proxy_up and stats:
        avg_lat = session.get("avg_latency_ms", 0)
        if avg_lat > 0:
            latency_str = f"{avg_lat:.0f}ms"
    print(f"     Uptime               {uptime_str:>10}")
    print(f"     Proxy overhead       {latency_str:>10}")

    # --- 6. HEALTH ---
    print()
    if not proxy_up:
        print(f"  ⚠️  Proxy unreachable — showing DB data only")
    elif errors > 0:
        print(f"  ⚠️  {errors} error(s) — run `tokenpak doctor`")
    else:
        print(f"  ✅ Healthy")

    # === MEME LINE ===
    if not no_meme:
        meme = random.choice(MEME_LINES)
        print()
        print(f"  📦 {meme}")

    print()


# ---------------------------------------------------------------------------
# Shared DB queries for breakdowns
# ---------------------------------------------------------------------------

_SOURCE_CASE = """
    CASE
        WHEN session_id LIKE 'gpt-%' OR endpoint LIKE '%openai%' THEN 'Codex / OpenAI'
        WHEN endpoint LIKE '%api.anthropic.com%?beta=true' THEN 'Claude Code'
        WHEN endpoint LIKE '%api.anthropic.com%' AND endpoint NOT LIKE '%?beta=true' THEN 'API Direct'
        WHEN endpoint LIKE '%127.0.0.1%' OR endpoint LIKE '%localhost%' THEN 'Local / Ollama'
        ELSE 'Other'
    END
"""

_PROVIDER_CASE = """
    CASE
        WHEN endpoint LIKE '%api.anthropic.com%' THEN 'Anthropic'
        WHEN endpoint LIKE '%openai%' OR endpoint LIKE '%api.openai.com%' THEN 'OpenAI'
        WHEN endpoint LIKE '%googleapis.com%' THEN 'Google'
        WHEN endpoint LIKE '%127.0.0.1%' OR endpoint LIKE '%localhost%' THEN 'Local'
        ELSE 'Other'
    END
"""


def _query_breakdown(db_path: Optional[str], group_expr: str) -> list:
    """Run a grouped breakdown query against monitor.db."""
    conn = _connect_db(db_path)
    if conn is None:
        return []
    try:
        return conn.execute(f"""
            SELECT
                {group_expr} AS label,
                COUNT(*) AS reqs,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read,
                COALESCE(SUM(compressed_tokens), 0) AS compressed,
                COALESCE(SUM(estimated_cost), 0.0) AS cost,
                GROUP_CONCAT(DISTINCT model) AS models
            FROM requests
            GROUP BY label
            ORDER BY reqs DESC
        """).fetchall()
    except Exception:
        return []
    finally:
        conn.close()


def _print_breakdown_table(title: str, emoji: str, rows: list) -> None:
    """Print a formatted breakdown table."""
    version = _get_version()
    print(f"\n  TOKENPAK {version}  |  {title}")
    print(SEP)
    print()
    print(f"  {emoji} {title}")
    print(f"     {'Source':<20} {'Reqs':>7}  {'Input':>8}  {'Cache%':>6}  {'Compressed':>10}  {'Cost':>10}")
    print(f"     {'─' * 20} {'─' * 7}  {'─' * 8}  {'─' * 6}  {'─' * 10}  {'─' * 10}")

    total_reqs = 0
    total_cost = 0.0
    for r in rows:
        label = r["label"]
        reqs = r["reqs"]
        inp = r["input_tokens"]
        cr = r["cache_read"]
        comp = r["compressed"]
        cost = r["cost"]
        total_h = inp + cr
        c_pct = (cr / total_h * 100) if total_h > 0 else 0.0
        total_reqs += reqs
        total_cost += cost
        print(
            f"     {label:<20} {reqs:>7,}  {_fmt_num(inp):>8}"
            f"  {c_pct:>5.0f}%  {_fmt_num(comp):>10}  {_fmt_cost(cost):>10}"
        )

    print(f"     {'─' * 20} {'─' * 7}  {'─' * 8}  {'─' * 6}  {'─' * 10}  {'─' * 10}")
    print(f"     {'Total':<20} {total_reqs:>7,}  {'':>8}  {'':>6}  {'':>10}  {_fmt_cost(total_cost):>10}")

    # Show models per source
    print()
    for r in rows:
        models = r["models"] or ""
        model_list = sorted(set(models.split(",")))[:4]
        if model_list:
            suffix = f" +{len(set(models.split(','))) - 4}" if len(set(models.split(","))) > 4 else ""
            print(f"     {r['label']:<20} {', '.join(model_list)}{suffix}")
    print()


def _run_by_source(
    proxy_base: str = PROXY_DEFAULT,
    db_path: Optional[str] = None,
) -> None:
    """Print breakdown by request source."""
    rows = _query_breakdown(db_path, _SOURCE_CASE)
    if not rows:
        print("No data available. Run requests through the proxy first.")
        return
    _print_breakdown_table("By Source", "📱", rows)


def _run_by_provider(
    proxy_base: str = PROXY_DEFAULT,
    db_path: Optional[str] = None,
) -> None:
    """Print breakdown by provider."""
    rows = _query_breakdown(db_path, _PROVIDER_CASE)
    if not rows:
        print("No data available. Run requests through the proxy first.")
        return
    _print_breakdown_table("By Provider", "🏢", rows)


def _print_by_source_inline(db_path: Optional[str] = None) -> None:
    """Print compact by-source summary for --full mode."""
    rows = _query_breakdown(db_path, _SOURCE_CASE)
    if not rows:
        return
    print()
    print(f"  📱 Sources (all time)")
    print(f"     {'Source':<20} {'Reqs':>7}  {'Cost':>10}  {'Cache%':>6}")
    print(f"     {'─' * 20} {'─' * 7}  {'─' * 10}  {'─' * 6}")
    for r in rows:
        inp = r["input_tokens"]
        cr = r["cache_read"]
        total_h = inp + cr
        c_pct = (cr / total_h * 100) if total_h > 0 else 0.0
        print(f"     {r['label']:<20} {r['reqs']:>7,}  {_fmt_cost(r['cost']):>10}  {c_pct:>5.0f}%")


def _print_by_provider_inline(db_path: Optional[str] = None) -> None:
    """Print compact by-provider summary for --full mode."""
    rows = _query_breakdown(db_path, _PROVIDER_CASE)
    if not rows:
        return
    print()
    print(f"  🏢 Providers (all time)")
    print(f"     {'Provider':<20} {'Reqs':>7}  {'Cost':>10}  {'Cache%':>6}")
    print(f"     {'─' * 20} {'─' * 7}  {'─' * 10}  {'─' * 6}")
    for r in rows:
        inp = r["input_tokens"]
        cr = r["cache_read"]
        total_h = inp + cr
        c_pct = (cr / total_h * 100) if total_h > 0 else 0.0
        print(f"     {r['label']:<20} {r['reqs']:>7,}  {_fmt_cost(r['cost']):>10}  {c_pct:>5.0f}%")


# ---------------------------------------------------------------------------
# Minimal output (one-liner for scripts/dashboards)
# ---------------------------------------------------------------------------


def _run_minimal(
    proxy_base: str = PROXY_DEFAULT,
    db_path: Optional[str] = None,
    no_meme: bool = False,
) -> None:
    """Print one-line savings summary. Prefers live proxy data."""
    stats = _fetch(f"{proxy_base}/stats")
    session = stats.get("session", {}) if stats else {}

    if session:
        reqs = session.get("requests", 0)
        saved_tok = session.get("saved_tokens", 0)
        sent = session.get("sent_input_tokens", 0)
        raw = sent + saved_tok
        pct = (saved_tok / raw * 100) if raw > 0 else 0.0
        # Pull tokenpak cache hit rate from /cache-stats
        cache_data = _fetch(f"{proxy_base}/cache-stats")
        tp_hits = cache_data.get("cache_hits", 0) if cache_data else 0
        tp_misses = cache_data.get("cache_misses", 0) if cache_data else 0
        tp_total = tp_hits + tp_misses
        tp_cache_pct = (tp_hits / tp_total * 100) if tp_total > 0 else 0.0
        line = f"📦 TokenPak: {_fmt_num(saved_tok)} tokens saved ({pct:.0f}%) | {reqs:,} reqs | {tp_cache_pct:.0f}% cache hit"
    else:
        # Fall back to DB
        savings = _calculate_fleet_savings(db_path=db_path, period="24h")
        if savings.get("error"):
            print("📦 TokenPak: proxy unreachable, no recent data")
            return
        t = savings["totals"]
        line = f"📦 TokenPak: {_fmt_cost(t['saved'])} saved | {t['requests']:,} reqs"

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
        from tokenpak.telemetry.pricing import estimate_savings
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
    @click.option("--days", default=0, type=int, help="Filter to last N days (combinable with --hours)")
    @click.option("--hours", default=0, type=int, help="Filter to last N hours (combinable with --days)")
    def status_cmd(
        proxy: str,
        full: bool,
        raw: bool,
        minimal: bool,
        as_json: bool,
        no_meme: bool,
        db_path: Optional[str],
        days: int,
        hours: int,
    ) -> None:
        """Show savings report (default) or full technical status.

        Default output leads with dollar savings — the number that matters.
        Use --full for the legacy technical output.
        Use --days and --hours to filter to a specific time window.

        Examples:

        \\b
          tokenpak status                     # savings-first (all time)
          tokenpak status --days 1            # last 24 hours
          tokenpak status --hours 6           # last 6 hours
          tokenpak status --days 1 --hours 6  # last 30 hours
          tokenpak status --full              # legacy technical output
          tokenpak status --minimal           # one-liner for scripts
          tokenpak status --json              # machine-readable
        """
        run(
            proxy_base=proxy,
            raw=raw,
            minimal=minimal,
            full=full,
            as_json=as_json,
            no_meme=no_meme,
            db_path=db_path,
            days=days,
            hours=hours,
        )
