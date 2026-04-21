"""last command — show last request stats with --oneline option."""

from __future__ import annotations

import json
import sys
from datetime import datetime

try:
    import click

    HAS_CLICK = True
except ImportError:
    HAS_CLICK = False


def run(
    proxy_base: str = "http://127.0.0.1:8766",
    oneline: bool = False,
    json_output: bool = False,
    no_session: bool = False,
) -> None:
    """Print last request stats to stdout."""
    import urllib.request

    SEP = "────────────────────────"

    try:
        with urllib.request.urlopen(f"{proxy_base}/stats/last", timeout=5) as r:
            data = json.loads(r.read())
    except Exception:
        print(f"✖ Proxy unreachable at {proxy_base}")
        sys.exit(1)

    request = data.get("request")
    session = data.get("session", {})

    if json_output:
        print(json.dumps(data, indent=2))
        return

    if not request:
        print("⚠ No requests captured yet")
        return

    tokens_saved = request.get("tokens_saved", 0)
    percent_saved = request.get("percent_saved", 0)
    cost_saved = request.get("cost_saved", 0)
    request_id = request.get("request_id", "unknown")
    timestamp = request.get("timestamp", "")

    if oneline:
        # Format: ⚡ TokenPak: -312 tokens (18%) | $0.003 saved | Session: $1.24 total
        if tokens_saved == 0:
            footer = "⚡ TokenPak: 0 tokens saved"
        else:
            footer = f"⚡ TokenPak: -{tokens_saved:,} tokens ({percent_saved:.0f}%) | ${cost_saved:.3f} saved"

        if not no_session and session:
            session_total = session.get("session_total_cost_saved", 0)
            footer += f" | Session: ${session_total:.2f} total"

        print(footer)
        return

    # Full format
    print("TOKENPAK  |  Last Request")
    print(f"{SEP}")
    print()
    print(f"Request ID:              {request_id}")
    if timestamp:
        # Parse ISO timestamp for display
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            print(f"Time:                    {dt.strftime('%H:%M:%S')}")
        except Exception:
            print(f"Time:                    {timestamp}")
    print()

    # Tokens section
    input_raw = request.get("input_tokens_raw", 0)
    input_sent = request.get("input_tokens_sent", 0)

    print("Tokens:")
    print(f"  Raw Input:             {input_raw:,}")
    print(f"  Sent:                  {input_sent:,}")
    print(f"  Saved:                 {tokens_saved:,} ({percent_saved:.1f}%)")
    print()

    # Cost section
    print("Cost:")
    print(f"  This Request:          ${cost_saved:.3f} saved")

    if session:
        session_total = session.get("session_total_cost_saved", 0)
        print(f"  Session Total:         ${session_total:.2f} saved")

    print()

    # Session stats
    if session and not no_session:
        requests = session.get("session_requests", 0)
        print(f"Requests This Session:   {requests}")


if HAS_CLICK:
    import click

    @click.command("last")
    @click.option("--proxy", default="http://127.0.0.1:8766", envvar="TOKENPAK_PROXY_URL")
    @click.option("--oneline", is_flag=True, help="Output single-line footer format")
    @click.option("--json", "json_output", is_flag=True, help="Output raw JSON")
    @click.option("--no-session", is_flag=True, help="Omit session totals")
    def last_cmd(proxy, oneline, json_output, no_session):
        """Show last request stats with compression/cost savings.

        Default shows detailed breakdown. Use --oneline for single-line footer.
        """
        run(proxy_base=proxy, oneline=oneline, json_output=json_output, no_session=no_session)
