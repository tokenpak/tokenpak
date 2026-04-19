"""
TokenPak Proxy Startup Self-Test

Runs lightweight checks before the proxy begins accepting connections:
  1. Port availability (critical — logs error if blocked)
  2. Failover config validity (non-critical — warns and continues)
  3. Core dependency imports (critical — logs error if missing)
  4. ~/.tokenpak directory presence (non-critical — auto-creates)

Principle: the proxy always starts (graceful degradation).
Critical failures are LOGGED but do NOT raise — they get surfaced
through the /health and /degradation endpoints instead.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Packages that MUST be importable for the proxy to function
_CRITICAL_DEPS = ["httpx", "json", "threading"]


def run_startup_checks(port: int) -> Tuple[bool, List[str]]:
    """
    Run startup self-test.

    Args:
        port: The port the proxy intends to bind on.

    Returns:
        (all_critical_passed, list_of_warnings)
        Warnings include both critical and non-critical issues.
        all_critical_passed=False means something fundamental is wrong;
        the proxy may not start, but we report clearly instead of crashing.
    """
    warnings: List[str] = []
    all_ok = True

    # ------------------------------------------------------------------ #
    # 1. Port availability                                                 #
    # ------------------------------------------------------------------ #
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.close()
    except OSError as exc:
        msg = (
            f"Port {port} is already in use ({exc}). "
            f"Another proxy may be running. "
            f"Kill it with: pkill -f 'tokenpak serve' "
            f"or set TOKENPAK_PORT to a different port."
        )
        logger.error("startup: %s", msg)
        warnings.append(msg)
        all_ok = False  # Critical — proxy will fail to bind

    # ------------------------------------------------------------------ #
    # 2. Failover config validity                                          #
    # ------------------------------------------------------------------ #
    try:
        from tokenpak.agent.proxy.failover import load_failover_config

        fc = load_failover_config()
        if fc.enabled and not fc.chain:
            msg = (
                "Failover is enabled in ~/.tokenpak/config.yaml but no providers "
                "are configured. Add at least one provider under 'failover.chain'."
            )
            logger.warning("startup: %s", msg)
            warnings.append(msg)
    except Exception as exc:
        msg = f"Could not load failover config (using built-in defaults): {exc}"
        logger.warning("startup: %s", msg)
        warnings.append(msg)

    # ------------------------------------------------------------------ #
    # 3. Critical dependency imports                                       #
    # ------------------------------------------------------------------ #
    missing: List[str] = []
    for dep in _CRITICAL_DEPS:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)

    if missing:
        msg = f"Missing dependencies: {', '.join(missing)}. " f"Run: pip install tokenpak"
        logger.error("startup: %s", msg)
        warnings.append(msg)
        all_ok = False

    # ------------------------------------------------------------------ #
    # 4. ~/.tokenpak directory                                             #
    # ------------------------------------------------------------------ #
    tokenpak_dir = Path.home() / ".tokenpak"
    if not tokenpak_dir.exists():
        try:
            tokenpak_dir.mkdir(parents=True, exist_ok=True)
            logger.info("startup: Created ~/.tokenpak")
        except Exception as exc:
            msg = f"Could not create ~/.tokenpak: {exc}. Some features may not persist."
            logger.warning("startup: %s", msg)
            warnings.append(msg)

    # ------------------------------------------------------------------ #
    # Summary                                                              #
    # ------------------------------------------------------------------ #
    if not warnings:
        logger.info("startup: all checks passed — listening on port %d", port)
    else:
        level = logger.error if not all_ok else logger.warning
        level("startup: %d issue(s) found: %s", len(warnings), "; ".join(warnings))

    return all_ok, warnings


def format_startup_report(warnings: List[str], all_ok: bool) -> str:
    """Format a human-readable startup report for the terminal."""
    if not warnings:
        return ""
    prefix = "⛔️ STARTUP ERROR" if not all_ok else "⚠️  STARTUP WARNING"
    lines = [f"{prefix} — {len(warnings)} issue(s):"]
    for i, w in enumerate(warnings, 1):
        lines.append(f"  {i}. {w}")
    return "\n".join(lines)
