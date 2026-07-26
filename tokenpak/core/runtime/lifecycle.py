# SPDX-License-Identifier: Apache-2.0
"""One lifecycle snapshot, shared by every surface that reports run state.

``setup``, ``start``, ``stop``, ``restart``, ``status``, ``doctor`` and the
interactive menu each used to answer "is the proxy running?" their own way.
The most damaging version was in ``setup``, which spawned a child, wrote its
PID before checking anything, slept, then probed ``http://localhost:<port>/health``
— **the port, not the child** — and printed "✅ Proxy running" if anything
answered. Both branches of its try/except printed a checkmark, so no code path
could report failure. With port 8766 already occupied, setup reported success
while its child was dead and the user was routed at someone else's process.

This module answers the question once, and distinguishes the cases that
matter:

* Is a process alive at the PID we recorded?          (``pid_alive``)
* Does something answer ``/health`` on the port?      (``health_ok``)
* Is that responder *ours*?                           (``owned``)
* Is the client actually routed through us?           (``routed``)
* Is a user config persisted?                         (``configured``)

A "started successfully" claim requires the conjunction, not any one signal.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

#: The proxy's PID file, relative to the TokenPak home.
PID_FILENAME = "proxy.pid"

#: How long to wait for a freshly spawned proxy to answer /health.
DEFAULT_START_TIMEOUT_S = 15.0


@dataclass
class LifecycleSnapshot:
    """Observed lifecycle state. Every field is an observation, not a guess."""

    port: int
    pid: Optional[int] = None
    pid_source: str = ""
    pid_alive: bool = False
    health_ok: bool = False
    health_payload: Dict[str, Any] = field(default_factory=dict)
    owned: Optional[bool] = None
    port_in_use: bool = False
    configured: bool = False
    config_path: Optional[str] = None
    routed: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        """True only when a live PID *and* a healthy endpoint agree.

        Deliberately conservative: a healthy endpoint alone is not proof that
        *our* proxy is running, which is exactly the inference that made setup
        report false success.
        """
        return self.pid_alive and self.health_ok

    @property
    def foreign_listener(self) -> bool:
        """Something else owns our port."""
        return self.port_in_use and not self.pid_alive

    def to_json(self) -> Dict[str, Any]:
        return {
            "port": self.port,
            "pid": self.pid,
            "pid_source": self.pid_source or None,
            "pid_alive": self.pid_alive,
            "health_ok": self.health_ok,
            "owned": self.owned,
            "port_in_use": self.port_in_use,
            "running": self.running,
            "foreign_listener": self.foreign_listener,
            "configured": self.configured,
            "config_path": self.config_path,
            "routed": self.routed,
            "reasons": list(self.reasons),
        }


# -- primitives -------------------------------------------------------------


def pid_path() -> Path:
    """Canonical PID file location (write home)."""
    from tokenpak import _paths

    return _paths.write_home() / PID_FILENAME


def read_pid() -> tuple[Optional[int], str]:
    """Read the recorded proxy PID from any known home.

    Returns ``(pid, source_path)``; ``(None, "")`` when absent or malformed.
    A malformed PID file is treated as absent rather than as an error, but it
    is never treated as a running proxy.
    """
    from tokenpak import _paths

    found = _paths.resolve_existing(PID_FILENAME)
    if found is None:
        return None, ""
    try:
        raw = found.read_text().strip()
        return (int(raw), str(found)) if raw else (None, str(found))
    except (OSError, ValueError):
        return None, str(found)


def pid_alive(pid: Optional[int]) -> bool:
    """True when *pid* names a live process this user can signal."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — alive, though not ours to manage.
        return True
    except OSError:
        return False


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True when *port* already has a listener.

    Used as a *precheck* before spawning, so a conflict can be reported
    instead of silently producing a dead child and a success message.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.75)
        return sock.connect_ex((host, port)) == 0


def probe_health(port: int, timeout: float = 2.0) -> tuple[bool, Dict[str, Any]]:
    """GET ``/health``. Returns ``(ok, payload)``; never raises."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
        return (isinstance(payload, dict), payload if isinstance(payload, dict) else {})
    except Exception:
        return False, {}


def _health_identifies_pid(payload: Dict[str, Any], pid: Optional[int]) -> Optional[bool]:
    """Whether ``/health`` claims the PID we recorded.

    ``None`` means the endpoint does not report a PID, so ownership is
    genuinely unknown — which is reported as unknown, not assumed true.
    """
    reported = payload.get("pid")
    if not isinstance(reported, int):
        return None
    if pid is None:
        return None
    return reported == pid


# -- snapshot ---------------------------------------------------------------


def snapshot(port: Optional[int] = None, *, probe: bool = True) -> LifecycleSnapshot:
    """Observe current lifecycle state once."""
    from tokenpak import _paths

    if port is None:
        try:
            port = int(os.environ.get("TOKENPAK_PORT", "8766"))
        except ValueError:
            port = 8766

    pid, source = read_pid()
    snap = LifecycleSnapshot(port=port, pid=pid, pid_source=source)
    snap.pid_alive = pid_alive(pid)

    cfg = _paths.config_read_path()
    snap.configured = cfg is not None
    snap.config_path = str(cfg) if cfg else None

    if probe:
        snap.port_in_use = port_in_use(port)
        snap.health_ok, snap.health_payload = probe_health(port)
        snap.owned = _health_identifies_pid(snap.health_payload, pid)
    snap.routed = _client_routed(port)

    if snap.foreign_listener:
        snap.reasons.append(f"port {port} has a listener that is not a TokenPak proxy we started")
    if pid is not None and not snap.pid_alive:
        snap.reasons.append(f"recorded PID {pid} is not running (stale {PID_FILENAME})")
    if not snap.configured:
        snap.reasons.append("no user config found")
    return snap


def _client_routed(port: int) -> bool:
    """Best-effort check that a supported client points at our proxy."""
    base = f"http://localhost:{port}"
    for env_var in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_API_URL"):
        if str(os.environ.get(env_var, "")).rstrip("/").endswith(f":{port}"):
            return True
    try:
        settings = Path.home() / ".claude" / "settings.json"
        if settings.exists():
            data = json.loads(settings.read_text())
            env = data.get("env") if isinstance(data, dict) else None
            if isinstance(env, dict):
                for value in env.values():
                    if isinstance(value, str) and base in value:
                        return True
    except Exception:
        pass
    return False


def write_pid(pid: int) -> Path:
    """Persist *pid* to the canonical PID file, after the caller verified it."""
    from tokenpak import _paths

    _paths.ensure_home()
    target = pid_path()
    target.write_text(str(pid))
    return target


def clear_pid() -> None:
    """Remove PID files from every known home. Never raises."""
    from tokenpak import _paths

    for base in _paths.read_candidates():
        try:
            (base / PID_FILENAME).unlink(missing_ok=True)
        except OSError:
            pass


def await_start(
    proc: Any,
    port: int,
    *,
    timeout: float = DEFAULT_START_TIMEOUT_S,
) -> LifecycleSnapshot:
    """Wait for *proc* to become a healthy proxy, or report why it did not.

    Polls the child's own exit status alongside ``/health``. If the child dies
    the loop stops immediately rather than waiting out the timeout and then
    reporting a stranger's healthy port as success.
    """
    deadline = time.monotonic() + timeout
    exit_code: Optional[int] = None
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is not None:
            break
        ok, payload = probe_health(port, timeout=1.0)
        if ok:
            snap = LifecycleSnapshot(
                port=port,
                pid=proc.pid,
                pid_source=str(pid_path()),
                pid_alive=True,
                health_ok=True,
                health_payload=payload,
                port_in_use=True,
            )
            snap.owned = _health_identifies_pid(payload, proc.pid)
            from tokenpak import _paths

            cfg = _paths.config_read_path()
            snap.configured = cfg is not None
            snap.config_path = str(cfg) if cfg else None
            snap.routed = _client_routed(port)
            return snap
        time.sleep(0.25)

    snap = snapshot(port)
    snap.pid = proc.pid
    if exit_code is not None:
        snap.pid_alive = False
        snap.reasons.insert(0, f"proxy process exited immediately with code {exit_code}")
    else:
        snap.reasons.insert(0, f"proxy did not become healthy within {timeout:.0f}s")
    return snap
