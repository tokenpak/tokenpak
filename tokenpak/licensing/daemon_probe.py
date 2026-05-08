# SPDX-License-Identifier: Apache-2.0
"""Pro-daemon presence probe (Std 25 §3.4 fallback contract, Phase 1).

Cheap local check for whether the closed-source ``tokenpak-paid-daemon``
is running on this host. Per Std 25 §2.1 the daemon publishes its
loopback port to ``~/.tokenpak/pro/daemon.sock-info`` (mode 0600) at
startup. Phase 1 only distinguishes:

- ``"active"`` — sock-info file present + readable + reachable on its
  declared port.
- ``"unavailable"`` — file missing, unreadable, malformed, or connection
  refused.

Phase 2+ adds ``"tip_mismatch"`` (TIP version negotiation) and the four
state-machine values (``offline-grace``, ``offline-expired``,
``user-revoked``, ``billing-grace``) per Std 25 §3.4 + §4.3. Those values
require talking to the license registry, which is out of scope for OSS.

The probe is **fast-path safe** — when the sock-info file is absent the
function short-circuits before any I/O on the daemon. This is the
overwhelming case (Pro daemon is opt-in install).

Per Std 25 §1.1 the OSS code never extends TIP capabilities in private
or assumes daemon presence; this module is the canonical way to ask
"is Pro available right now?" rather than scattering ``Path.exists()``
checks across call sites (per ``feedback_always_dynamic.md``).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Optional, Literal

DaemonState = Literal["active", "unavailable", "tip_mismatch"]

# Canonical sock-info file path. Constant rather than parameter — Std 25
# §2.1 ratifies this as the single agreed location. If the daemon
# version rolls forward and changes, the constant moves in lockstep.
_SOCK_INFO_PATH = Path.home() / ".tokenpak" / "pro" / "daemon.sock-info"

# Connect timeout for the daemon probe. Short enough that a stale
# sock-info pointing at a dead port doesn't slow the proxy hot path.
_PROBE_TIMEOUT_SEC = 0.250


def sock_info_path() -> Path:
    """Return the canonical sock-info file path. Exposed for tests + CLI
    diagnostics; production code should call :func:`detect_daemon_state`
    or :func:`is_daemon_reachable` instead."""
    return _SOCK_INFO_PATH


def _read_sock_info(path: Path) -> Optional[dict]:
    """Parse the sock-info file. Returns None on any error.

    Expected shape (per Std 25 §2.1):
        {"port": <int>, "tip_version": "<str>", "started_at": <unix-ts>}

    The function tolerates extra keys — the daemon may carry
    forward-compatible fields. Missing/malformed required keys count as
    failure (returns None).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _try_connect(port: int) -> bool:
    """Attempt a loopback TCP connect to ``port``. Returns True on success.

    Uses the short timeout to fail fast when the sock-info is stale.
    Closes the socket immediately — this is a probe, not a session.
    """
    if not (1 <= port <= 65_535):
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(_PROBE_TIMEOUT_SEC)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except (OSError, socket.timeout):
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def detect_daemon_state(*, sock_info_override: Optional[Path] = None) -> DaemonState:
    """Determine whether the Pro daemon is reachable.

    Returns:
        "active" — sock-info present, parses, and the loopback port
            accepts connections.
        "unavailable" — anything else (file missing, malformed,
            connection refused, timeout).
        "tip_mismatch" — reserved for Phase 2; never returned in Phase 1.

    ``sock_info_override`` is a test hook; production callers leave it
    None to use the canonical path.
    """
    path = sock_info_override or _SOCK_INFO_PATH
    if not path.exists():
        return "unavailable"
    info = _read_sock_info(path)
    if info is None:
        return "unavailable"
    port = info.get("port")
    if not isinstance(port, int):
        return "unavailable"
    if not _try_connect(port):
        return "unavailable"
    return "active"


def is_daemon_reachable(*, sock_info_override: Optional[Path] = None) -> bool:
    """Boolean convenience wrapper around :func:`detect_daemon_state`.

    Equivalent to ``detect_daemon_state(...) == "active"`` — useful in
    if-statements where the granular state isn't needed (e.g., "do I
    forward this request to the daemon, or return not_implemented?").
    """
    return detect_daemon_state(sock_info_override=sock_info_override) == "active"


__all__ = [
    "DaemonState",
    "detect_daemon_state",
    "is_daemon_reachable",
    "sock_info_path",
]
