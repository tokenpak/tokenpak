"""status command — show proxy health, mode, session stats, and degradation events."""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any, Dict, Optional

try:
    import click

    HAS_CLICK = True
except ImportError:
    HAS_CLICK = False

# Import pricing module for savings calculations
try:
    from tokenpak.pricing import estimate_savings
except ImportError:
    # Fallback if pricing module not available
    estimate_savings = None


def _fetch(url: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    """Fetch JSON from a URL. Returns None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def run(
    proxy_base: str = "http://127.0.0.1:8766", raw: bool = False, minimal: bool = False
) -> None:
    """Print proxy status to stdout."""
    SEP = "────────────────────────────────────"

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

    print("\nTOKENPAK  |  Status")
    print(SEP)

    print(f"{'✅  Proxy running':<28}port {proxy_base.split(':')[-1]} — hybrid mode")
    print(f"{'✅  Uptime':<28}{uptime_str}")
    print(f"{'✅  Health':<28}OK (0 errors)" if errors == 0 else f"{'⚠️  Health':<28}{errors} errors")
    print()

    # Calculate and display savings summary
    if estimate_savings and session:
        savings_data = estimate_savings(session)
        print("💰  Session Savings")
        print(f"    Requests:      {requests:,}")
        print(f"    Input tokens:  {tokens_raw:,}")
        print(f"    Tokens saved:  {tokens_saved:,} ({avg_savings:.1f}% compression)")
        print(f"    Cache reads:   {session.get('cache_read_tokens', 0):,} ({savings_data.get('cache_hit_rate', 0):.0f}% hit rate)")
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
        print(SEP)
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

    print(SEP)
    if is_degraded:
        print("ℹ️  Running degraded — requests still served. Run `tokenpak doctor` for details.")
    else:
        print("ℹ️  Run `tokenpak savings` for detailed breakdown.")
    print()


if HAS_CLICK:
    import click

    @click.command("status")
    @click.option(
        "--proxy",
        default="http://127.0.0.1:8766",
        envvar="TOKENPAK_PROXY_URL",
        help="Proxy base URL",
    )
    @click.option("--raw", is_flag=True, help="Dump raw JSON")
    @click.option("--minimal", is_flag=True, help="One-line summary")
    def status_cmd(proxy: str, raw: bool, minimal: bool) -> None:
        """Show proxy health, session stats, and degradation events.

        Reports:
          • Proxy status (Active / Degraded)
          • Session token savings and cost
          • Recent degradation events (compression failures, failovers)

        Examples:

        \\b
          tokenpak status
          tokenpak status --minimal
          tokenpak status --raw
        """
        run(proxy_base=proxy, raw=raw, minimal=minimal)
