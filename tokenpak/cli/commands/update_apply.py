# SPDX-License-Identifier: Apache-2.0
"""``tokenpak update apply`` — load a pending update, only when nothing is in use.

An update is *pending* when a newer version is staged or installed than the
one the proxy is running (see :mod:`tokenpak.core.runtime.update_pending`).
This command restarts the TokenPak services so that version takes effect. It
never switches versions under a live session: if a request is in flight or a
client is connected to the proxy, it refuses, changes nothing and exits with
``EXIT_BUSY``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from typing import Any, Optional

from tokenpak.cli.exit_codes import (
    EXIT_BUSY,
    EXIT_FAILURE,
    EXIT_MISSING_PREREQUISITE,
    EXIT_OK,
)
from tokenpak.core.runtime import update_pending as _pending

_STARTUP_WAIT_SECONDS = 20.0
_SYSTEMCTL_TIMEOUT_SECONDS = 60


def _port() -> int:
    try:
        return int(os.environ.get("TOKENPAK_PORT", "8766"))
    except ValueError:
        return 8766


def established_connections(port: int) -> Optional[int]:
    """Count established inbound TCP connections to *port*.

    Returns ``None`` where the kernel table is not readable (non-Linux), so the
    caller can say the check was not possible instead of claiming idle.
    """
    total = 0
    seen = False
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name, encoding="ascii") as handle:
                rows = handle.read().splitlines()[1:]
        except OSError:
            continue
        seen = True
        for row in rows:
            fields = row.split()
            if len(fields) < 4 or fields[3] != "01":
                continue
            try:
                if int(fields[1].rsplit(":", 1)[1], 16) == port:
                    total += 1
            except (IndexError, ValueError):
                continue
    return total if seen else None


def busy_reason(port: Optional[int] = None) -> Optional[str]:
    """Why the proxy is in use right now, or ``None`` when it is idle or down."""
    from tokenpak.core.runtime import lifecycle

    port = port or _port()
    ok, health = lifecycle.probe_health(port, timeout=2.0)
    if not ok:
        return None  # nothing answering: nothing to interrupt
    try:
        in_flight = int(health.get("in_flight_requests") or 0)
    except (TypeError, ValueError):
        in_flight = 0
    if in_flight > 0:
        noun = "request" if in_flight == 1 else "requests"
        return f"{in_flight} {noun} in flight"
    clients = established_connections(port)
    if clients:
        noun = "client is" if clients == 1 else "clients are"
        return f"{clients} {noun} connected to the proxy"
    return None


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        timeout=_SYSTEMCTL_TIMEOUT_SECONDS,
    )


def _units_loaded(units: list[str]) -> bool:
    """True when systemd (user) knows every unit in *units*."""
    if not shutil.which("systemctl"):
        return False
    try:
        for unit in units:
            result = _systemctl("show", "-p", "LoadState", "--value", unit)
            if result.returncode != 0 or result.stdout.strip() != "loaded":
                return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _manual_step(found: _pending.PendingUpdate, units: list[str]) -> str:
    if found.source == _pending.SOURCE_STAGED and units:
        joined = " ".join(units)
        return f"systemctl --user stop {joined} && systemctl --user start {joined}"
    return "tokenpak restart"


def _wait_for_version(target: str) -> Optional[str]:
    """Poll /health until it reports a version, return it (or None on timeout)."""
    deadline = time.monotonic() + _STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        live = _pending._health_version(1.0)
        if live:
            return live
        time.sleep(1.0)
    return None


def run(args: argparse.Namespace) -> int:
    """Entry point for ``tokenpak update apply``."""
    check_only = bool(getattr(args, "apply_check", False))
    found = _pending.detect(probe=True, timeout=2.0)

    if not found.pending:
        print("No update is pending. Nothing to apply.")
        return EXIT_OK

    print(f"Update pending: {found.summary()}")

    reason = busy_reason()
    if reason:
        print(f"✗ Not applied: {reason}.")
        print("  Nothing was changed. Run `tokenpak update apply` when the proxy is idle,")
        print("  or restart it yourself when you are ready.")
        return EXIT_BUSY

    units = _pending.stack_units() if found.source == _pending.SOURCE_STAGED else []
    managed = list(units) if units else ["tokenpak-proxy.service"]
    use_systemd = _units_loaded(managed)

    if check_only:
        print("Dry run: the proxy is idle, so this would apply now.")
        if use_systemd:
            print(f"  Would restart: {', '.join(managed)}")
        else:
            print(f"  No service manager detected. Manual step: {_manual_step(found, units)}")
        return EXIT_OK

    if not use_systemd:
        print("✗ No service manager detected for TokenPak, so nothing was restarted.")
        print(f"  Apply it yourself: {_manual_step(found, units)}")
        return EXIT_MISSING_PREREQUISITE

    try:
        if found.source == _pending.SOURCE_STAGED:
            # Stop the whole stack, then start it: the staged version switches
            # in only on a whole-stack start, never on a single-unit restart.
            stop = _systemctl("stop", *managed)
            start = _systemctl("start", *managed) if stop.returncode == 0 else stop
            failed = start if start.returncode != 0 else None
        else:
            result = _systemctl("restart", *managed)
            failed = result if result.returncode != 0 else None
    except (OSError, subprocess.SubprocessError) as error:
        print(f"✗ Could not run systemctl: {error}")
        print(f"  Apply it yourself: {_manual_step(found, units)}")
        return EXIT_FAILURE

    if failed is not None:
        detail = (failed.stderr or failed.stdout or "").strip()
        print(f"✗ Restart failed (systemctl exit {failed.returncode}).")
        if detail:
            print(f"  {detail.splitlines()[0]}")
        print("  Check with: tokenpak status")
        return EXIT_FAILURE

    live = _wait_for_version(found.target or "")
    if live and live == found.target:
        print(f"✓ Applied. TokenPak {live} is running.")
        return EXIT_OK
    if live:
        print(f"✗ Restarted, but the proxy still reports {live} (expected {found.target}).")
        print("  Check with: tokenpak doctor")
        return EXIT_FAILURE
    print("✗ Restarted, but the proxy did not answer in time.")
    print("  Check with: tokenpak status")
    return EXIT_FAILURE


def add_parser(update_parser: Any) -> None:
    """Register ``apply`` under the existing ``update`` command."""
    sub = update_parser.add_subparsers(dest="update_action", metavar="{apply}")
    apply_p = sub.add_parser(
        "apply",
        help="Load a pending update (only when nothing is in use)",
        description=(
            "Restart TokenPak so a staged or installed update takes effect. "
            "Refuses, and changes nothing, while a request is in flight or a "
            "client is connected."
        ),
    )
    apply_p.add_argument(
        "--check",
        action="store_true",
        dest="apply_check",
        help="Report whether it would apply now, without restarting anything",
    )
