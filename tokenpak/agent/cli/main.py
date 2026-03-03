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

    p = argparse.ArgumentParser(prog="tokenpak", add_help=False)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--minimal", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    for c in ["status", "version", "config", "reset"]:
        sub.add_parser(c)

    lp = sub.add_parser("logs")
    lp.add_argument("lines", nargs="?", type=int, default=30)

    hp = sub.add_parser("help")
    hp.add_argument("command_name", nargs="?")

    pp = sub.add_parser("proxy")
    pps = pp.add_subparsers(dest="proxy_cmd")
    pps.add_parser("status")
    pps.add_parser("restart")

    args, _ = p.parse_known_args()

    if args.cmd == "proxy":
        dispatch = {"status": cmd_proxy_status, "restart": cmd_proxy_restart}
        dispatch.get(args.proxy_cmd, lambda a: print("Usage: tokenpak proxy <status|restart>"))(args)
    elif args.cmd:
        dispatch = {
            "status": cmd_status,
            "version": cmd_version,
            "config": cmd_config,
            "reset": cmd_reset,
            "logs": cmd_logs,
            "help": cmd_help,
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
