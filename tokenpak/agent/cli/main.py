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
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROXY_BASE = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
PROXY_SERVICE = "tokenpak-proxy.service"
SEP = "────────────────────────"
DB_PATH = os.path.expanduser("~/.openclaw/workspace/.tokenpak/monitor.db")


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
    print(
        f"✖ Proxy Not Responding\nReason: {PROXY_BASE} unreachable\nAction: tokenpak proxy restart"
    )
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
        f"FROM requests {where}",
        params,
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
        f"FROM requests {where}",
        params,
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
            capture_output=True,
            text=True,
        )
        state = r.stdout.strip()
    except Exception:
        state = "unknown"
    d = proxy_get("/health")
    if args.minimal:
        print(
            f"{'●' if state == 'active' else '○'} {state} | port 8766 {'responding' if d else 'unreachable'}"
        )
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
        time.sleep(2)
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
        capture_output=True,
        text=True,
    )
    print(r.stdout or r.stderr)


def cmd_reset(args):
    d = proxy_get("/reset") or proxy_err()
    print(header("Reset"))
    print("✓ Session stats reset" if d.get("reset") else f"⚠ Unexpected: {d}")


def cmd_help(args):
    target = getattr(args, "command_name", None)
    CMDS = [
        (
            "VISIBILITY",
            [
                ("status", "Proxy state, router, compression"),
                ("version", "Show installed version"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("config", "Show TOKENPAK_* env vars"),
                ("proxy status", "Check proxy service"),
                ("proxy restart", "Restart tokenpak-proxy.service"),
                ("logs [N]", "Show last N proxy log lines"),
                ("reset", "Reset session stats"),
                ("help", "This help"),
            ],
        ),
        (
            "PRO",
            [
                ("optimize", "Auto-analyze session for cost + token efficiency (Pro+)"),
                ("optimize --apply", "Auto-apply optimization recommendations (Pro+)"),
                ("cost", "Token usage and cost reporting"),
                ("budget intelligence", "Burn rate, ETA, trend, suggestions (Pro+)"),
                ("prune", "Remove low-priority blocks from store (Pro+)"),
                ("prune --dry-run", "Preview prune candidates without changes (Pro+)"),
                ("prune --auto", "Auto-prune without confirmation (Pro+)"),
                ("retain <id>", "Pin a block so it survives pruning (Pro+)"),
                ("retain --list", "Show all pinned blocks (Pro+)"),
                ("retain --remove <id>", "Unpin a block (Pro+)"),
            ],
        ),
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
# savings argparse helper
# ---------------------------------------------------------------------------


def _savings_argparse(argv: list) -> None:
    from tokenpak.agent.cli.commands.savings import run_savings_cmd

    sp = argparse.ArgumentParser(prog="tokenpak savings", add_help=True)
    sp.add_argument(
        "--period",
        default="24h",
        choices=["24h", "7d", "30d"],
        help="Time window (default: 24h)",
    )
    sp.add_argument("--verbose", "-v", action="store_true", help="Per-model breakdown")
    sp.add_argument("--json", dest="as_json", action="store_true", help="Machine-readable JSON")
    args = sp.parse_args(argv)
    run_savings_cmd(args)


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


def _diff_argparse(argv: list) -> None:
    from tokenpak.agent.cli.commands.diff import run_diff_cmd

    dp = argparse.ArgumentParser(prog="tokenpak diff", add_help=True)
    dp.add_argument("--verbose", "-v", action="store_true", help="Show token counts per block")
    dp.add_argument("--json", dest="raw", action="store_true", help="Output raw JSON")
    dp.add_argument("--since", default=None, help="Diff from specific ISO timestamp")
    args = dp.parse_args(argv)
    run_diff_cmd(args)


def _prune_argparse(argv: list) -> None:
    from tokenpak.agent.cli.commands.prune import run_prune

    pp = argparse.ArgumentParser(prog="tokenpak prune", add_help=True)
    pp.add_argument("--auto", action="store_true", help="Auto-prune without confirmation")
    pp.add_argument("--dry-run", dest="dry_run", action="store_true", help="Preview without changes")
    pp.add_argument(
        "--threshold", type=float, default=0.4, metavar="SCORE",
        help="Quality score below which blocks are pruned (default: 0.4)"
    )
    pp.add_argument("--json", dest="as_json", action="store_true", help="Output raw JSON")
    args = pp.parse_args(argv)
    run_prune(auto=args.auto, dry_run=args.dry_run, threshold=args.threshold, as_json=args.as_json)


def _retain_argparse(argv: list) -> None:
    from tokenpak.agent.cli.commands.retain import run_retain

    rp = argparse.ArgumentParser(prog="tokenpak retain", add_help=True)
    rp.add_argument("block_id", nargs="?", default=None, help="Block ID to pin")
    rp.add_argument("--list", dest="list_pins", action="store_true", help="Show all pinned blocks")
    rp.add_argument("--remove", metavar="BLOCK_ID", default=None, help="Unpin a block")
    args = rp.parse_args(argv)
    run_retain(block_id=args.block_id, list_pins=args.list_pins, remove=args.remove)


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

    # tokenpak budget intelligence (Pro+)
    intp = bsub.add_parser("intelligence", help="Pro: burn rate, ETA, trend, suggestions")
    intp.add_argument("--json", dest="raw", action="store_true", help="Output raw JSON")

    bp.add_argument("--raw", action="store_true", help="Output raw JSON")
    args = bp.parse_args(argv)
    run_budget_cmd(args)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    # Delegate serve subcommand (Phase 5A: Ingest API)
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        import argparse as _ap
        from tokenpak.agent.cli.commands.serve import _default_workers

        sp = _ap.ArgumentParser(prog="tokenpak serve")
        sp.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
        sp.add_argument("--port", type=int, default=8766, help="Bind port (default: 8766)")
        sp.add_argument(
            "--workers",
            type=int,
            default=None,
            metavar="N",
            help=(
                f"Number of worker processes (default: max(1, cpu_count//2) = {_default_workers()}). "
                "Workers restart on crash; graceful shutdown drains all workers."
            ),
        )
        sargs = sp.parse_args(sys.argv[2:])
        from tokenpak.agent.cli.commands.serve import run_serve_cmd

        run_serve_cmd(sargs)
        return

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

    # Delegate teacher subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "teacher":
        try:
            from tokenpak.agent.cli.commands.teacher import run_teacher_cmd

            run_teacher_cmd(sys.argv[2:])
        except ImportError as e:
            print(f"teacher command not available: {e}")
            sys.exit(1)
        return

    # Delegate index subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "index":
        try:
            from tokenpak.agent.cli.commands.index import index_cmd

            sys.argv = sys.argv[1:]
            index_cmd(standalone_mode=True)
        except ImportError as e:
            print(f"index command not available: {e}")
            sys.exit(1)
        return

    # Delegate fingerprint subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "fingerprint":
        try:
            from tokenpak.agent.cli.commands.fingerprint import fingerprint_cmd

            sys.argv = sys.argv[1:]
            fingerprint_cmd(standalone_mode=True)
        except ImportError as e:
            print(f"fingerprint command not available: {e}")
            sys.exit(1)
        return

    # Delegate doctor subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        try:
            from tokenpak.agent.cli.commands.doctor import doctor_cmd

            sys.argv = sys.argv[1:]
            doctor_cmd(standalone_mode=True)
        except ImportError as e:
            print(f"doctor command not available: {e}")
            sys.exit(1)
        return

    # Delegate dashboard subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        try:
            from tokenpak.agent.cli.commands.dashboard import dashboard_cmd

            sys.argv = sys.argv[1:]
            dashboard_cmd(standalone_mode=True)
        except ImportError as e:
            print(f"dashboard command not available: {e}")
            sys.exit(1)
        return

    # Delegate exec subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "exec":
        try:
            from tokenpak.agent.cli.commands.exec import exec_cmd

            sys.argv = sys.argv[1:]
            exec_cmd(standalone_mode=True)
        except ImportError as e:
            print(f"exec command not available: {e}")
            sys.exit(1)
        return

    # Delegate metrics subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "metrics":
        import argparse as _ap

        from tokenpak.agent.cli.commands.metrics import (
            cmd_history,
            cmd_preview,
            cmd_status,
            cmd_sync,
        )

        mp = _ap.ArgumentParser(prog="tokenpak metrics", add_help=True)
        msub = mp.add_subparsers(dest="metrics_cmd")
        msub.add_parser("status")
        msub.add_parser("preview")
        hh = msub.add_parser("history")
        hh.add_argument("--days", type=int, default=30)
        hh.add_argument("--raw", action="store_true")
        ss = msub.add_parser("sync")
        ss.add_argument("--dry-run", dest="dry_run", action="store_true")
        margs = mp.parse_args(sys.argv[2:])
        dispatch_m = {
            "status": cmd_status,
            "preview": cmd_preview,
            "history": cmd_history,
            "sync": cmd_sync,
        }
        fn_m = dispatch_m.get(margs.metrics_cmd)
        if fn_m:
            fn_m(margs)
        else:
            mp.print_help()
        return

    # Delegate savings subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "savings":
        _savings_argparse(sys.argv[2:])
        return

    # Delegate cost subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "cost":
        _cost_argparse(sys.argv[2:])
        return

    # Delegate optimize subcommand (Pro+)
    if len(sys.argv) > 1 and sys.argv[1] == "optimize":
        import argparse as _ap
        from tokenpak.agent.cli.commands.optimize import run_optimize
        op = _ap.ArgumentParser(prog="tokenpak optimize", add_help=True)
        op.add_argument("--verbose", "-v", action="store_true", help="Per-block analysis")
        op.add_argument("--json", dest="as_json", action="store_true", help="Machine-readable JSON")
        op.add_argument("--apply", action="store_true", help="Auto-apply recommendations")
        oargs = op.parse_args(sys.argv[2:])
        run_optimize(verbose=oargs.verbose, as_json=oargs.as_json, apply=oargs.apply)
        return

    # Delegate budget subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "budget":
        _budget_argparse(sys.argv[2:])
        return

    # Delegate diff subcommand (Pro+)
    if len(sys.argv) > 1 and sys.argv[1] == "diff":
        _diff_argparse(sys.argv[2:])
        return

    # Delegate prune subcommand (Pro+)
    if len(sys.argv) > 1 and sys.argv[1] == "prune":
        _prune_argparse(sys.argv[2:])
        return

    # Delegate retain subcommand (Pro+)
    if len(sys.argv) > 1 and sys.argv[1] == "retain":
        _retain_argparse(sys.argv[2:])
        return

    # Delegate Enterprise policy/sla/compliance commands
    if len(sys.argv) > 1 and sys.argv[1] == "policy":
        from tokenpak.agent.cli.commands.policy import run as _policy_run
        _policy_run(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "sla":
        from tokenpak.agent.cli.commands.sla import run as _sla_run
        _sla_run(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "compliance":
        from tokenpak.agent.cli.commands.compliance import run as _compliance_run
        _compliance_run(sys.argv[2:])
        return

    p = argparse.ArgumentParser(prog="tokenpak", add_help=False)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--minimal", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    for c in ["status", "version", "config", "reset"]:
        sub.add_parser(c)

    # activate / deactivate / plan (license management)
    act_p = sub.add_parser("activate", help="Activate a Pro/Team/Enterprise license key")
    act_p.add_argument("key", help="License token to activate")
    sub.add_parser("deactivate", help="Remove license and revert to OSS")
    sub.add_parser("plan", help="Show current license tier, expiry, seats, and features")

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

    # agent subcommand
    agentp = sub.add_parser("agent", help="Agent coordination commands")
    agent_sub = agentp.add_subparsers(dest="agent_cmd")
    # agent handoff
    handoffp = agent_sub.add_parser("handoff", help="Context handoff between agents")
    handoff_sub = handoffp.add_subparsers(dest="handoff_cmd")
    # create
    createp = handoff_sub.add_parser("create", help="Create a context handoff")
    createp.add_argument("--from", dest="handoff_from", required=True, help="Sending agent name")
    createp.add_argument("--to", dest="handoff_to", required=True, help="Receiving agent name")
    createp.add_argument(
        "--ref", action="append", metavar="TYPE:PATH[:DESC]", help="Context ref (repeatable)"
    )
    createp.add_argument("--done", metavar="TEXT", help="What was done")
    createp.add_argument("--next", dest="next", metavar="TEXT", help="What comes next")
    createp.add_argument(
        "--file", action="append", metavar="PATH", help="Relevant file path (repeatable)"
    )
    createp.add_argument(
        "--ttl", type=float, default=24.0, metavar="HOURS", help="TTL in hours (default 24)"
    )
    # receive
    receivep = handoff_sub.add_parser("receive", help="Receive and validate a handoff")
    receivep.add_argument("handoff_id", help="Handoff ID")
    # apply
    applyp = handoff_sub.add_parser("apply", help="Apply a handoff (mark context as loaded)")
    applyp.add_argument("handoff_id", help="Handoff ID")
    # list
    listp = handoff_sub.add_parser("list", help="List handoffs")
    listp.add_argument("--to", dest="handoff_to", metavar="AGENT", help="Filter by recipient")
    listp.add_argument("--from", dest="handoff_from", metavar="AGENT", help="Filter by sender")
    listp.add_argument("--status", metavar="STATUS", help="Filter by status")
    # show
    showp = handoff_sub.add_parser("show", help="Show handoff details")
    showp.add_argument("handoff_id", help="Handoff ID")
    # expire
    handoff_sub.add_parser("expire", help="Expire stale handoffs")

    args, _ = p.parse_known_args()

    if args.cmd == "proxy":
        dispatch = {"status": cmd_proxy_status, "restart": cmd_proxy_restart}
        dispatch.get(args.proxy_cmd, lambda a: print("Usage: tokenpak proxy <status|restart>"))(
            args
        )
    elif args.cmd == "learn":
        _dispatch_learn(args)
    elif args.cmd in ("activate", "deactivate", "plan"):
        _dispatch_license(args)
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


def _dispatch_license(args) -> None:
    """Handle tokenpak activate / deactivate / plan."""
    from tokenpak.agent.cli.commands.license import _run_activate, _run_deactivate, _run_plan

    if args.cmd == "activate":
        _run_activate(args.key)
    elif args.cmd == "deactivate":
        _run_deactivate()
    elif args.cmd == "plan":
        _run_plan()


def _dispatch_debug(args) -> None:
    from tokenpak.agent.cli.commands.debug import debug_cmd

    debug_cmd(args)


def _dispatch_agent(args) -> None:
    """Handle `tokenpak agent <subcommand>` commands."""
    from tokenpak.agent.cli.commands.handoff import handoff_cmd

    agent_cmd = getattr(args, "agent_cmd", None)
    if agent_cmd == "handoff":
        handoff_cmd(args)
    else:
        print("Usage: tokenpak agent <handoff>")


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
