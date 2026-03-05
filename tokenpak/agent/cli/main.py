#!/usr/bin/env python3
"""TokenPak CLI — entry point.

Delegates to sub-command modules in commands/ for each verb.
Also provides the classic argparse-based proxy/stats interface
adapted from the vault CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROXY_BASE = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
PROXY_SERVICE = "tokenpak-proxy.service"
SEP = "────────────────────────"
DB_PATH = os.path.expanduser("~/.openclaw/workspace/.ocp/monitor.db")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def header(title: str) -> str:
    return f"TOKENPAK  |  {title}\n{SEP}\n"


def kv(label: str, value: str, width: int = 26) -> str:
    return f"{label:<{width}}{value}"


def sym(b: bool) -> str:
    return "●" if b else "○"


def fmt_n(n: int) -> str:
    return f"{n:,}"


def fmt_c(c: float) -> str:
    return f"${c:.2f}"


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

def proxy_get(path: str):
    try:
        with urllib.request.urlopen(f"{PROXY_BASE}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def proxy_err():
    print(header("Error"))
    print(f"✖ Proxy Not Responding\nReason: {PROXY_BASE} unreachable\nAction: tokenpak proxy restart")
    sys.exit(1)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db_connect():
    if not os.path.exists(DB_PATH):
        return None
    return sqlite3.connect(DB_PATH)


def _db_usage(days=None, model=None):
    conn = _db_connect()
    if not conn:
        return 0, 0, 0, 0.0
    clauses, params = [], []
    if days is not None:
        clauses.append("date(timestamp) >= date('now', ?)")
        params.append(f"-{days} days")
    if model:
        clauses.append("model = ?")
        params.append(model)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    row = conn.execute(
        f"SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(estimated_cost) "
        f"FROM requests {where}", params
    ).fetchone()
    conn.close()
    return (row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0.0)


def _db_savings(days=None, model=None):
    conn = _db_connect()
    if not conn:
        return 0, 0, 0, 0.0
    clauses, params = [], []
    if days is not None:
        clauses.append("date(timestamp) >= date('now', ?)")
        params.append(f"-{days} days")
    if model:
        clauses.append("model = ?")
        params.append(model)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    row = conn.execute(
        f"SELECT COUNT(*), SUM(input_tokens), SUM(compressed_tokens), SUM(estimated_cost) "
        f"FROM requests {where}", params
    ).fetchone()
    conn.close()
    return (row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0.0)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_status(args):
    d = proxy_get("/health") or proxy_err()
    if args.raw:
        print(json.dumps(d, indent=2))
        return
    st = d.get("stats", {})
    saved = st.get("saved_tokens", 0)
    sent = st.get("sent_input_tokens", 0)
    raw_in = sent + saved
    pct = f"▼ {saved/raw_in*100:.1f}%" if raw_in else "n/a"
    if args.minimal:
        print(f"● Active | {d.get('compilation_mode','?')} | {pct}")
        return
    print(header("Status"))
    print(kv("State:", "● Active"))
    print(kv("Mode:", d.get("compilation_mode", "unknown")))
    print()
    print(kv("Session Requests:", fmt_n(st.get("requests", 0))))
    print(kv("Tokens Saved:", fmt_n(saved)))
    print(kv("Compression:", pct))


def cmd_version(args):
    try:
        import importlib.metadata
        ver = importlib.metadata.version("tokenpak")
        print(f"TOKENPAK  |  v{ver}")
    except Exception:
        print("TOKENPAK  |  version unknown")


def cmd_config(args):
    print(header("Configuration"))
    for var, label in [
        ("TOKENPAK_ROUTER_ENABLED", "Router enabled"),
        ("TOKENPAK_MODE", "Compilation mode"),
        ("TOKENPAK_PORT", "Proxy port"),
        ("TOKENPAK_COMPACT", "Compaction on/off"),
        ("TOKENPAK_COMPACT_THRESHOLD_TOKENS", "Compaction threshold"),
        ("TOKENPAK_PROXY_URL", "Proxy URL override"),
        ("TOKENPAK_DB", "Monitor DB path"),
    ]:
        print(kv(label + ":", os.environ.get(var, "○ not set")))


def cmd_proxy_status(args):
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", PROXY_SERVICE],
            capture_output=True, text=True,
        )
        state = r.stdout.strip()
    except Exception:
        state = "unknown"
    d = proxy_get("/health")
    if args.minimal:
        print(f"{'●' if state == 'active' else '○'} {state} | port 8766 {'responding' if d else 'unreachable'}")
        return
    print(header("Proxy Status"))
    print(kv("Service:", f"{'●' if state == 'active' else '○'} {state}"))
    print(kv("Port:", "8766"))
    print(kv("HTTP:", "● Responding" if d else "✖ Unreachable"))
    if d:
        print(kv("Mode:", d.get("compilation_mode", "?")))


def cmd_proxy_restart(args):
    print(header("Proxy Restart"))
    try:
        subprocess.run(["systemctl", "--user", "restart", PROXY_SERVICE], check=True)
        import time; time.sleep(2)
        d = proxy_get("/health")
        print("✓ Restarted and healthy" if d else "⚠ Restarted — health check pending")
    except subprocess.CalledProcessError as e:
        print(f"✖ Restart failed: {e}")
        sys.exit(1)


def cmd_logs(args):
    n = getattr(args, "lines", 30) or 30
    print(header(f"Logs (last {n})"))
    r = subprocess.run(
        ["journalctl", "--user", "-u", PROXY_SERVICE, f"-n{n}", "--no-pager"],
        capture_output=True, text=True,
    )
    print(r.stdout or r.stderr)


def cmd_reset(args):
    d = proxy_get("/reset") or proxy_err()
    print(header("Reset"))
    print("✓ Session stats reset" if d.get("reset") else f"⚠ Unexpected: {d}")


def cmd_help(args):
    target = getattr(args, "command_name", None)
    CMDS = [
        ("VISIBILITY", [
            ("status",        "Proxy state, router, compression"),
            ("version",       "Show installed version"),
        ]),
        ("SYSTEM", [
            ("config",        "Show TOKENPAK_* env vars"),
            ("proxy status",  "Check proxy service"),
            ("proxy restart", "Restart tokenpak-proxy.service"),
            ("logs [N]",      "Show last N proxy log lines"),
            ("reset",         "Reset session stats"),
            ("help",          "This help"),
        ]),
    ]
    if target:
        flat = {cmd: desc for _, grp in CMDS for cmd, desc in grp}
        print(header(target))
        print(f"Purpose:  {flat.get(target, 'Unknown command')}")
        print(f"Usage:    tokenpak {target} [--verbose] [--raw] [--minimal]")
        return
    print(header("Command Reference"))
    for cat, cmds in CMDS:
        print(f"{cat}")
        for cmd, desc in cmds:
            print(f"  {cmd:<18}{desc}")
        print()
    print(f"{SEP}")
    print("Flags: --verbose  --raw  --minimal")
    print("Tip:   tokenpak help <command>")


# ---------------------------------------------------------------------------
# cost / budget argparse helpers
# ---------------------------------------------------------------------------

def _cost_argparse(argv: list) -> None:
    from tokenpak.agent.cli.commands.cost import run_cost_cmd
    cp = argparse.ArgumentParser(prog="tokenpak cost", add_help=True)
    cp.add_argument("--yesterday", action="store_true", help="Yesterday's spend")
    cp.add_argument("--week", action="store_true", help="Last 7 days")
    cp.add_argument("--month", action="store_true", help="This month")
    cp.add_argument("--by-model", dest="by_model", action="store_true", help="Break down by model")
    cp.add_argument("--by-agent", dest="by_agent", action="store_true", help="Break down by agent")
    cp.add_argument("--export", choices=["csv"], default=None, help="Export format")
    cp.add_argument("--raw", action="store_true", help="Output raw JSON")
    args = cp.parse_args(argv)
    run_cost_cmd(args)


def _budget_argparse(argv: list) -> None:
    from tokenpak.agent.cli.commands.budget import run_budget_cmd
    bp = argparse.ArgumentParser(prog="tokenpak budget", add_help=True)
    bsub = bp.add_subparsers(dest="budget_cmd")

    # tokenpak budget set --daily N --monthly N
    setp = bsub.add_parser("set", help="Set budget limits")
    setp.add_argument("--daily", type=float, default=None, help="Daily budget in USD")
    setp.add_argument("--monthly", type=float, default=None, help="Monthly budget in USD")

    # tokenpak budget alert --at N
    alp = bsub.add_parser("alert", help="Set alert threshold")
    alp.add_argument("--at", type=float, required=True, help="Alert at N%%")

    # tokenpak budget history
    hisp = bsub.add_parser("history", help="Budget vs actual history")
    hisp.add_argument("--days", type=int, default=30, help="Number of days")
    hisp.add_argument("--raw", action="store_true")

    # tokenpak budget forecast
    bsub.add_parser("forecast", help="Projected spend forecast")

    bp.add_argument("--raw", action="store_true", help="Output raw JSON")
    args = bp.parse_args(argv)
    run_budget_cmd(args)



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Delegate trigger subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "trigger":
        try:
            from tokenpak.agent.cli.commands.trigger import trigger_group
            sys.argv = sys.argv[1:]
            trigger_group(standalone_mode=True)
        except ImportError as e:
            print(f"trigger command not available: {e}")
            sys.exit(1)
        return

    # Delegate workflow subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "workflow":
        try:
            from tokenpak.agent.cli.commands.workflow import workflow_cmd
            sys.argv = sys.argv[1:]
            workflow_cmd(standalone_mode=True)
        except ImportError as e:
            print(f"workflow command not available: {e}")
            sys.exit(1)
        return

    # Delegate cost subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "cost":
        from tokenpak.agent.cli.commands.cost import run_cost_cmd as _cost_cmd
        _cost_argparse(sys.argv[2:])
        return

    # Delegate budget subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "budget":
        _budget_argparse(sys.argv[2:])
        return

    p = argparse.ArgumentParser(prog="tokenpak", add_help=False)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--minimal", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    for c in ["status", "version", "config", "reset"]:
        sub.add_parser(c)

    # Special argument handling for 'last' command
    lastp = sub.add_parser("last")
    lastp.add_argument("--oneline", action="store_true")
    lastp.add_argument("--no-session", action="store_true")


    lp = sub.add_parser("logs")
    lp.add_argument("lines", nargs="?", type=int, default=30)

    hp = sub.add_parser("help")
    hp.add_argument("command_name", nargs="?")

    pp = sub.add_parser("proxy")
    pps = pp.add_subparsers(dest="proxy_cmd")
    pps.add_parser("status")
    pps.add_parser("restart")

    # debug subcommand
    dbgp = sub.add_parser("debug")
    dbg_sub = dbgp.add_subparsers(dest="debug_cmd")
    dbg_on = dbg_sub.add_parser("on")
    dbg_on.add_argument("--requests", dest="debug_requests", type=int, default=None)
    dbg_sub.add_parser("off")
    dbg_sub.add_parser("status")

    # learn subcommand
    learnp = sub.add_parser("learn", help="Show or reset learned patterns")
    learn_sub = learnp.add_subparsers(dest="learn_cmd")
    learn_sub.add_parser("status", help="Show learned patterns summary")
    learn_sub.add_parser("reset", help="Clear all learned data")

    args, _ = p.parse_known_args()

    if args.cmd == "proxy":
        dispatch = {"status": cmd_proxy_status, "restart": cmd_proxy_restart}
        dispatch.get(args.proxy_cmd, lambda a: print("Usage: tokenpak proxy <status|restart>"))(args)
    elif args.cmd == "learn":
        _dispatch_learn(args)
    elif args.cmd:
        dispatch = {
            "status": cmd_status,
            "version": cmd_version,
            "config": cmd_config,
            "reset": cmd_reset,
            "last": cmd_last,
            "logs": cmd_logs,
            "help": cmd_help,
            "debug": _dispatch_debug,
        }
        fn = dispatch.get(args.cmd)
        if fn:
            fn(args)
        else:
            print(f"Unknown command: {args.cmd}")
            cmd_help(args)
    else:
        cmd_help(args)


if __name__ == "__main__":
    main()


def cmd_last(args):
    """Show last request stats."""
    from datetime import datetime
    
    oneline = getattr(args, "oneline", False)
    no_session = getattr(args, "no_session", False)
    raw = getattr(args, "raw", False)
    
    d = proxy_get("/stats/last") or proxy_err()
    
    if raw:
        print(json.dumps(d, indent=2))
        return
    
    request = d.get("request")
    session = d.get("session", {})
    
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
    print(header("Last Request"))
    print()
    print(f"Request ID:              {request_id}")
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            print(f"Time:                    {dt.strftime('%H:%M:%S')}")
        except:
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


def _dispatch_debug(args) -> None:
    from tokenpak.agent.cli.commands.debug import debug_cmd
    debug_cmd(args)


def _dispatch_learn(args) -> None:
    """Handle `tokenpak learn status` and `tokenpak learn reset`."""
    from tokenpak.agent.agentic.learning import cmd_learn_status, learn, reset

    learn_cmd = getattr(args, "learn_cmd", None)
    if learn_cmd == "reset":
        reset()
        print("✓ Learning store cleared.")
    elif learn_cmd == "status":
        # Refresh from current telemetry files first
        learn()
        cmd_learn_status()
    else:
        print("Usage: tokenpak learn <status|reset>")
