"""status command — show proxy state, mode, session stats."""

from __future__ import annotations

import json
import sys

try:
    import click
    HAS_CLICK = True
except ImportError:
    HAS_CLICK = False


def run(proxy_base: str = "http://127.0.0.1:8766", raw: bool = False, minimal: bool = False) -> None:
    """Print proxy status to stdout."""
    import urllib.request
    SEP = "────────────────────────"

    try:
        with urllib.request.urlopen(f"{proxy_base}/health", timeout=5) as r:
            d = json.loads(r.read())
    except Exception:
        print(f"✖ Proxy unreachable at {proxy_base}")
        sys.exit(1)

    if raw:
        print(json.dumps(d, indent=2))
        return

    st = d.get("stats", {})
    mode = d.get("compilation_mode", "unknown")
    saved = st.get("saved_tokens", 0)
    sent = st.get("sent_input_tokens", 0)
    raw_in = sent + saved
    pct = f"▼ {saved/raw_in*100:.1f}%" if raw_in else "n/a"

    if minimal:
        print(f"● Active | {mode} | {pct}")
        return

    print(f"TOKENPAK  |  Status\n{SEP}")
    print(f"{'State:':<26}● Active")
    print(f"{'Mode:':<26}{mode}")
    print(f"{'Session Requests:':<26}{st.get('requests', 0):,}")
    print(f"{'Tokens Saved:':<26}{saved:,}")
    print(f"{'Compression:':<26}{pct}")


if HAS_CLICK:
    import click

    @click.command("status")
    @click.option("--proxy", default="http://127.0.0.1:8766", envvar="TOKENPAK_PROXY_URL")
    @click.option("--raw", is_flag=True)
    @click.option("--minimal", is_flag=True)
    def status_cmd(proxy, raw, minimal):
        """Show proxy state, mode, and session stats."""
        run(proxy_base=proxy, raw=raw, minimal=minimal)
