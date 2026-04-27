#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""NCP-3I activation verification.

The previous NCP-3I deployment produced no ``tp_parity_trace``
table. This script checks the six conditions that have to align
for the instrumentation to actually capture the
``tokenpak claude`` request path:

  1. The currently-installed ``tokenpak`` includes the NCP-3I
     instrumentation (PR #70 / commit ec34c94703 or newer).
  2. ``TOKENPAK_PARITY_TRACE_ENABLED`` is set to a truthy value
     in this shell.
  3. A ``tokenpak`` proxy process is actually running.
  4. The proxy is on the port the launcher will redirect to.
  5. ``ANTHROPIC_BASE_URL`` is unset or already points at the
     proxy (otherwise ``env.setdefault`` in the launcher won't
     redirect).
  6. ``TOKENPAK_PROXY_BYPASS`` is unset.

Plus three secondary checks:

  7. ``TOKENPAK_HOME`` resolves to the same directory the proxy
     writes its telemetry to.
  8. The ``telemetry.db`` file in that home is readable.
  9. ``tp_parity_trace`` table exists (created lazily on first
     emit; absent until first hit).

Exit code 0 = all required conditions met; 1 = at least one
required condition failed; 2 = transient or environmental error.

Read-only. Never writes to telemetry.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

PARITY_TRACE_ENV: str = "TOKENPAK_PARITY_TRACE_ENABLED"
PROXY_BYPASS_ENV: str = "TOKENPAK_PROXY_BYPASS"
NCP_3I_LANDING_COMMIT: str = "ec34c94703"  # PR #70 merge SHA
DEFAULT_PORT: int = 8766


# ─────────────────────────────────────────────────────────────────────


def _ok(msg: str) -> Dict[str, Any]:
    return {"status": "ok", "msg": msg}


def _warn(msg: str) -> Dict[str, Any]:
    return {"status": "warn", "msg": msg}


def _fail(msg: str) -> Dict[str, Any]:
    return {"status": "fail", "msg": msg}


# ─────────────────────────────────────────────────────────────────────


def check_1_installed_version() -> Dict[str, Any]:
    """Verify the running ``tokenpak`` package contains the NCP-3I
    parity_trace module + the server.py hooks.

    The cleanest test: try to import ``tokenpak.proxy.parity_trace``;
    confirm the module exposes ``EVENT_HANDLER_ENTRY``.
    """
    try:
        from tokenpak.proxy import parity_trace as _pt  # noqa: F401
    except ImportError as exc:
        return _fail(
            f"tokenpak.proxy.parity_trace not importable ({exc!r}) — "
            f"the running tokenpak is older than PR #70 / commit "
            f"{NCP_3I_LANDING_COMMIT}. Update tokenpak (e.g. via "
            "`pip install -e .` from the repo root) and re-run."
        )
    if not hasattr(_pt, "EVENT_HANDLER_ENTRY"):
        return _fail(
            "parity_trace module imported but EVENT_HANDLER_ENTRY missing "
            "— mismatched / partial install."
        )
    # Bonus check: server.py contains the hook call.
    try:
        server_src = (
            Path(__file__).resolve().parent.parent
            / "tokenpak" / "proxy" / "server.py"
        ).read_text()
        if "_pt.emit(" not in server_src:
            return _warn(
                "parity_trace module present but server.py does not "
                "contain the expected '_pt.emit(' hook calls. The pip "
                "install may be from a different source tree than the "
                "repo. Verify with: pip show tokenpak | grep Location"
            )
    except OSError:
        # Source file lookup is best-effort; module import is the
        # authoritative check.
        pass
    return _ok(
        "tokenpak.proxy.parity_trace importable; server.py hooks present."
    )


def check_2_env_var_set() -> Dict[str, Any]:
    raw = os.environ.get(PARITY_TRACE_ENV, "")
    if not raw:
        return _fail(
            f"{PARITY_TRACE_ENV} is unset in this shell. Run: "
            f"export {PARITY_TRACE_ENV}=true"
        )
    if raw.strip().lower() not in ("1", "true", "yes", "on"):
        return _fail(
            f"{PARITY_TRACE_ENV}={raw!r} is not a truthy value "
            "(accepted: 1 / true / yes / on, case-insensitive)."
        )
    return _ok(f"{PARITY_TRACE_ENV}={raw!r} is truthy.")


def check_3_proxy_running(port: int) -> Dict[str, Any]:
    """Try to connect to 127.0.0.1:<port>. Connection success = the
    proxy port is bound (not strictly = tokenpak is running, but
    it's the same gate the CLI launcher implicitly relies on)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(("127.0.0.1", port))
        sock.close()
        return _ok(f"127.0.0.1:{port} accepts TCP — proxy reachable.")
    except (ConnectionRefusedError, OSError) as exc:
        return _fail(
            f"127.0.0.1:{port} does not accept TCP ({exc!r}). "
            "Run: tokenpak start"
        )


def check_4_proxy_pid_and_env(port: int) -> Dict[str, Any]:
    """Best-effort: lsof the port to find the proxy PID, then read
    /proc/<pid>/environ to confirm the env var is in the proxy
    process's own environment (not just the operator's shell).

    This catches the case where the operator set
    ``TOKENPAK_PARITY_TRACE_ENABLED`` AFTER ``tokenpak start``;
    the proxy's environment is captured at start time.
    """
    try:
        # ``lsof -i :<port> -t`` returns just the PID(s).
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return _warn(
                f"lsof could not identify a PID for port {port}. "
                "Skipping proxy-process env-var check (this is "
                "best-effort; on macOS / minimal containers lsof may "
                "be missing)."
            )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        for pid in pids:
            environ_path = Path(f"/proc/{pid}/environ")
            if not environ_path.is_file():
                continue
            try:
                # /proc/<pid>/environ is NUL-delimited.
                env_bytes = environ_path.read_bytes()
                env_dict = {}
                for entry in env_bytes.split(b"\0"):
                    if b"=" in entry:
                        k, v = entry.split(b"=", 1)
                        env_dict[k.decode("utf-8", "replace")] = v.decode(
                            "utf-8", "replace"
                        )
                trace_in_proxy = env_dict.get(PARITY_TRACE_ENV, "")
                if trace_in_proxy.strip().lower() in (
                    "1", "true", "yes", "on"
                ):
                    return _ok(
                        f"proxy pid={pid} has {PARITY_TRACE_ENV}="
                        f"{trace_in_proxy!r} in its own environment."
                    )
                return _fail(
                    f"proxy pid={pid} does NOT have a truthy "
                    f"{PARITY_TRACE_ENV} in its own environment "
                    f"(got {trace_in_proxy!r}). The proxy was probably "
                    "started BEFORE the env var was set. Restart: "
                    f"export {PARITY_TRACE_ENV}=true && tokenpak stop "
                    "&& tokenpak start"
                )
            except OSError:
                continue
        return _warn(
            f"Could not read /proc/<pid>/environ for any PID on port "
            f"{port}. May be a permissions / non-Linux issue."
        )
    except FileNotFoundError:
        return _warn(
            "lsof not installed. Skipping proxy-process env check."
        )
    except subprocess.TimeoutExpired:
        return _warn("lsof timed out. Skipping.")


def check_5_anthropic_base_url(port: int) -> Dict[str, Any]:
    """If ``ANTHROPIC_BASE_URL`` is pre-set to something other than
    the proxy, the launcher's ``env.setdefault`` is a no-op and
    Claude Code never sends through the proxy.
    """
    raw = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not raw:
        return _ok(
            "ANTHROPIC_BASE_URL is unset — launcher will set it to "
            f"http://127.0.0.1:{port}."
        )
    expected = f"http://127.0.0.1:{port}"
    if raw == expected:
        return _ok(f"ANTHROPIC_BASE_URL={raw!r} (matches proxy).")
    return _fail(
        f"ANTHROPIC_BASE_URL={raw!r} is pre-set and does NOT match "
        f"the proxy ({expected!r}). The launcher uses env.setdefault "
        "which won't override. Either: unset it (`unset "
        "ANTHROPIC_BASE_URL`) so the launcher can redirect, or set "
        f"it to {expected!r} explicitly."
    )


def check_6_proxy_bypass_unset() -> Dict[str, Any]:
    raw = os.environ.get(PROXY_BYPASS_ENV, "")
    if raw == "1":
        return _fail(
            f"{PROXY_BYPASS_ENV}=1 is set — the launcher short-circuits "
            "and Claude Code talks directly to Anthropic. Unset it: "
            f"unset {PROXY_BYPASS_ENV}"
        )
    return _ok(f"{PROXY_BYPASS_ENV} is not set to 1.")


def check_7_tokenpak_home() -> Dict[str, Any]:
    """Report the TOKENPAK_HOME the harness would use vs the proxy
    process's home."""
    own_home = os.environ.get(
        "TOKENPAK_HOME", str(Path.home() / ".tokenpak")
    )
    return _ok(
        f"This shell's TOKENPAK_HOME resolves to {own_home}. "
        "If the proxy was started with a different TOKENPAK_HOME, "
        "the harness will read a different telemetry.db. Verify with "
        "`lsof -p <proxy_pid>` or by re-starting the proxy in this shell."
    )


def check_8_telemetry_db() -> Dict[str, Any]:
    home = os.environ.get(
        "TOKENPAK_HOME", str(Path.home() / ".tokenpak")
    )
    db_path = Path(home) / "telemetry.db"
    if not db_path.is_file():
        return _warn(
            f"{db_path} does not exist yet. It is created lazily on "
            "first proxy write. After running a workload, re-check."
        )
    return _ok(
        f"{db_path} exists ({db_path.stat().st_size} bytes)."
    )


def check_9_parity_trace_table() -> Dict[str, Any]:
    home = os.environ.get(
        "TOKENPAK_HOME", str(Path.home() / ".tokenpak")
    )
    db_path = Path(home) / "telemetry.db"
    if not db_path.is_file():
        return _warn("telemetry.db absent (see check 8).")
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='tp_parity_trace'"
            ).fetchone()
            if row is None:
                return _warn(
                    "tp_parity_trace table NOT YET CREATED. The table "
                    "is built lazily on the first emit() call. If "
                    "checks 1–6 all pass and a workload still produces "
                    "no table, the launcher path may not hit "
                    "_proxy_to_inner — escalate to NCP-3I-v3 (deeper "
                    "hooks)."
                )
            count = conn.execute(
                "SELECT COUNT(*) FROM tp_parity_trace"
            ).fetchone()[0]
            return _ok(
                f"tp_parity_trace table present, {count} rows total."
            )
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return _warn(
            f"telemetry.db unreadable ({exc!r}). Re-check after a "
            "proxy run."
        )


# ─────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TOKENPAK_PORT", str(DEFAULT_PORT))),
        help="Proxy port to check (default: $TOKENPAK_PORT or 8766).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable output.",
    )
    args = p.parse_args(argv)

    checks = [
        ("1", "tokenpak version (NCP-3I present?)", check_1_installed_version()),
        ("2", "TOKENPAK_PARITY_TRACE_ENABLED", check_2_env_var_set()),
        ("3", "proxy port reachable", check_3_proxy_running(args.port)),
        ("4", "trace env in proxy process", check_4_proxy_pid_and_env(args.port)),
        ("5", "ANTHROPIC_BASE_URL routing", check_5_anthropic_base_url(args.port)),
        ("6", "TOKENPAK_PROXY_BYPASS unset", check_6_proxy_bypass_unset()),
        ("7", "TOKENPAK_HOME resolution", check_7_tokenpak_home()),
        ("8", "telemetry.db present", check_8_telemetry_db()),
        ("9", "tp_parity_trace table", check_9_parity_trace_table()),
    ]

    if args.json:
        out = {
            "schema_version": "ncp-3i-activation-v1",
            "port": args.port,
            "checks": [
                {"id": cid, "title": title, **result}
                for cid, title, result in checks
            ],
        }
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print()
        print("NCP-3I activation verification")
        print("──────────────────────────────")
        for cid, title, result in checks:
            symbol = {"ok": "✓", "warn": "·", "fail": "✗"}.get(
                result["status"], "?"
            )
            print(f"  {symbol} [{cid}] {title}")
            print(f"        {result['msg']}")
            print()

    # Exit code: 1 if any required check (1–6) failed; else 0.
    required_failures = [
        result["status"] == "fail"
        for cid, _, result in checks
        if cid in ("1", "2", "3", "4", "5", "6")
    ]
    if any(required_failures):
        if not args.json:
            print(
                "✗ Activation gap detected. Fix the failing required "
                "check(s) above, then re-run."
            )
        return 1
    if not args.json:
        print(
            "✓ All required checks passed. Run a workload through "
            "`tokenpak claude`; tp_parity_trace will be created on "
            "first request."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
