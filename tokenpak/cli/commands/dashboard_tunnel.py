# SPDX-License-Identifier: Apache-2.0
"""SSH tunnel lifecycle for ``tokenpak dashboard connect``."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenpak import _paths

__all__: list[str] = []

DEFAULT_LOCAL_PORT = 8766
DEFAULT_REMOTE_PORT = 8766
DEFAULT_HEALTH_TIMEOUT_SECONDS = 20.0
HEALTH_PATH = "/health"
DASHBOARD_PATH = "/dashboard"


class DashboardTunnelError(RuntimeError):
    """User-facing dashboard tunnel failure."""


@dataclass(frozen=True)
class TunnelPaths:
    directory: Path
    metadata: Path
    control_socket: Path
    pid_file: Path


@dataclass(frozen=True)
class DashboardTunnelResult:
    host: str
    target: str
    local_port: int
    remote_port: int
    url: str
    reused: bool
    control_socket: Path
    pid_file: Path


@dataclass(frozen=True)
class DisconnectResult:
    host: str
    target: str
    disconnected: bool
    metadata_removed: bool


def tunnel_dir() -> Path:
    """Return and create the dashboard tunnel state directory."""
    _paths.ensure_home()
    path = _paths.under("tunnels")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _safe_label(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    if not base:
        base = "host"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{base[:40]}-{digest}"


def build_ssh_target(host: str, ssh_user: str | None = None) -> str:
    """Return the OpenSSH target, accepting either ``host`` or ``user@host``."""
    raw_host = (host or "").strip()
    if not raw_host:
        raise DashboardTunnelError("Host is required.")
    if "@" in raw_host:
        return raw_host
    user = (ssh_user or os.environ.get("TOKENPAK_DASHBOARD_SSH_USER") or getpass.getuser()).strip()
    if not user:
        return raw_host
    return f"{user}@{raw_host}"


def state_paths(target: str, local_port: int, remote_port: int) -> TunnelPaths:
    directory = tunnel_dir()
    label = _safe_label(target)
    socket_name = f"dashboard-{label}-{local_port}-{remote_port}.sock"
    pid_name = f"dashboard-{label}-{local_port}-{remote_port}.pid"
    meta_name = f"dashboard-{label}.json"
    return TunnelPaths(
        directory=directory,
        metadata=directory / meta_name,
        control_socket=directory / socket_name,
        pid_file=directory / pid_name,
    )


def port_is_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def select_local_port(requested: str | int, *, preferred: int = DEFAULT_LOCAL_PORT) -> int:
    if isinstance(requested, int):
        if not port_is_available(requested):
            raise DashboardTunnelError(
                f"Local port {requested} is already in use. Use --local-port auto or choose another port."
            )
        return requested

    value = str(requested).strip().lower()
    if value != "auto":
        try:
            port = int(value)
        except ValueError as exc:
            raise DashboardTunnelError("--local-port must be an integer port or 'auto'.") from exc
        if not port_is_available(port):
            raise DashboardTunnelError(
                f"Local port {port} is already in use. Use --local-port auto or choose another port."
            )
        return port

    for port in range(int(preferred), 65536):
        if port_is_available(port):
            return port
    raise DashboardTunnelError("No available local TCP port found for dashboard tunnel.")


def build_ssh_command(
    target: str,
    local_port: int,
    remote_port: int,
    control_socket: Path,
    *,
    ssh_bin: str | None = None,
) -> list[str]:
    binary = ssh_bin or os.environ.get("TOKENPAK_SSH_BIN", "ssh")
    return [
        binary,
        "-N",
        "-M",
        "-S",
        str(control_socket),
        "-o",
        "ExitOnForwardFailure=yes",
        "-L",
        f"{local_port}:127.0.0.1:{remote_port}",
        target,
    ]


def _write_metadata(paths: TunnelPaths, payload: dict[str, Any]) -> None:
    paths.metadata.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_metadata(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _record_paths(record: dict[str, Any]) -> tuple[Path | None, Path | None, Path | None]:
    metadata = record.get("metadata")
    control = record.get("control_socket")
    pid_file = record.get("pid_file")
    return (
        Path(metadata) if metadata else None,
        Path(control) if control else None,
        Path(pid_file) if pid_file else None,
    )


def _unlink_if_present(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _cleanup_record(record: dict[str, Any]) -> None:
    metadata, control_socket, pid_file = _record_paths(record)
    _unlink_if_present(metadata)
    _unlink_if_present(control_socket)
    _unlink_if_present(pid_file)


def _run_ssh_control(target: str, control_socket: Path, operation: str) -> bool:
    if not control_socket.exists():
        return False
    cmd = [
        os.environ.get("TOKENPAK_SSH_BIN", "ssh"),
        "-S",
        str(control_socket),
        "-O",
        operation,
        target,
    ]
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


def tunnel_is_alive(record: dict[str, Any]) -> bool:
    target = str(record.get("target") or "")
    control_socket = Path(str(record.get("control_socket") or ""))
    if not target or not control_socket.exists():
        return False
    return _run_ssh_control(target, control_socket, "check")


def _health_url(local_port: int) -> str:
    return f"http://127.0.0.1:{local_port}{HEALTH_PATH}"


def _dashboard_url(local_port: int) -> str:
    return f"http://127.0.0.1:{local_port}{DASHBOARD_PATH}"


def _health_response_ok(raw: bytes) -> bool:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text.upper() == "OK"
    return str(data.get("status", "")).lower() == "ok"


def wait_for_dashboard_health(
    local_port: int,
    *,
    timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
    interval_seconds: float = 0.25,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        try:
            with urllib.request.urlopen(_health_url(local_port), timeout=1) as response:
                if _health_response_ok(response.read()):
                    return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_seconds)


def _start_ssh_tunnel(
    target: str,
    local_port: int,
    remote_port: int,
    paths: TunnelPaths,
) -> int:
    cmd = build_ssh_command(target, local_port, remote_port, paths.control_socket)
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise DashboardTunnelError("OpenSSH client not found. Install ssh or set TOKENPAK_SSH_BIN.") from exc

    time.sleep(0.25)
    if process.poll() is not None:
        error = ""
        if process.stderr is not None:
            try:
                error = process.stderr.read().strip()
            except Exception:
                error = ""
        detail = f": {error}" if error else "."
        raise DashboardTunnelError(f"SSH tunnel failed to start{detail}")

    paths.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return int(process.pid)


def _result_from_record(record: dict[str, Any], *, reused: bool) -> DashboardTunnelResult:
    return DashboardTunnelResult(
        host=str(record["host"]),
        target=str(record["target"]),
        local_port=int(record["local_port"]),
        remote_port=int(record["remote_port"]),
        url=str(record["url"]),
        reused=reused,
        control_socket=Path(str(record["control_socket"])),
        pid_file=Path(str(record["pid_file"])),
    )


def connect_dashboard(
    host: str,
    *,
    remote_port: int = DEFAULT_REMOTE_PORT,
    local_port: str | int = "auto",
    ssh_user: str | None = None,
    open_browser: bool = True,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> DashboardTunnelResult:
    target = build_ssh_target(host, ssh_user)

    probe_paths = state_paths(target, DEFAULT_LOCAL_PORT, int(remote_port))
    existing = _load_metadata(probe_paths.metadata)
    if existing is not None:
        requested_auto = str(local_port).strip().lower() == "auto"
        requested_matches = False
        if not requested_auto:
            try:
                requested_matches = int(existing.get("local_port", -1)) == int(local_port)
            except (TypeError, ValueError):
                requested_matches = False
        remote_matches = int(existing.get("remote_port", -1)) == int(remote_port)
        if remote_matches and (requested_auto or requested_matches):
            if tunnel_is_alive(existing) and wait_for_dashboard_health(
                int(existing["local_port"]),
                timeout_seconds=health_timeout,
            ):
                result = _result_from_record(existing, reused=True)
                if open_browser:
                    webbrowser.open(result.url)
                return result
            _cleanup_record(existing)

    chosen_local = select_local_port(local_port, preferred=DEFAULT_LOCAL_PORT)
    paths = state_paths(target, chosen_local, int(remote_port))
    url = _dashboard_url(chosen_local)

    pid = _start_ssh_tunnel(target, chosen_local, int(remote_port), paths)
    record = {
        "host": host,
        "target": target,
        "local_port": chosen_local,
        "remote_port": int(remote_port),
        "url": url,
        "metadata": str(paths.metadata),
        "control_socket": str(paths.control_socket),
        "pid_file": str(paths.pid_file),
        "pid": pid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_metadata(paths, record)

    if not wait_for_dashboard_health(chosen_local, timeout_seconds=health_timeout):
        disconnect_dashboard(host, ssh_user=ssh_user, quiet=True)
        raise DashboardTunnelError(
            f"Dashboard health check did not return OK at {_health_url(chosen_local)} "
            f"within {health_timeout:g}s. Confirm the remote dashboard is running on port {remote_port}."
        )

    result = _result_from_record(record, reused=False)
    if open_browser:
        webbrowser.open(result.url)
    return result


def disconnect_dashboard(
    host: str,
    *,
    ssh_user: str | None = None,
    quiet: bool = False,
) -> DisconnectResult:
    target = build_ssh_target(host, ssh_user)
    paths = state_paths(target, DEFAULT_LOCAL_PORT, DEFAULT_REMOTE_PORT)
    record = _load_metadata(paths.metadata)
    if record is None:
        if not quiet:
            print(f"No dashboard tunnel recorded for {host}.")
        return DisconnectResult(host=host, target=target, disconnected=False, metadata_removed=False)

    control_socket = Path(str(record.get("control_socket") or ""))
    disconnected = _run_ssh_control(str(record.get("target") or target), control_socket, "exit")
    _cleanup_record(record)
    return DisconnectResult(host=host, target=target, disconnected=disconnected, metadata_removed=True)


def _result_payload(result: DashboardTunnelResult) -> dict[str, Any]:
    return {
        "ok": True,
        "host": result.host,
        "target": result.target,
        "local_port": result.local_port,
        "remote_port": result.remote_port,
        "url": result.url,
        "reused": result.reused,
        "control_socket": str(result.control_socket),
        "pid_file": str(result.pid_file),
    }


def _disconnect_payload(result: DisconnectResult) -> dict[str, Any]:
    return {
        "ok": True,
        "host": result.host,
        "target": result.target,
        "disconnected": result.disconnected,
        "metadata_removed": result.metadata_removed,
    }


def cmd_dashboard_tunnel(args) -> int:
    action = getattr(args, "dashboard_action", None)
    json_output = bool(getattr(args, "json_output", False) or getattr(args, "json_export", False))
    try:
        if action == "connect":
            result = connect_dashboard(
                args.host,
                remote_port=int(getattr(args, "remote_port", DEFAULT_REMOTE_PORT)),
                local_port=getattr(args, "local_port", "auto"),
                ssh_user=getattr(args, "ssh_user", None),
                open_browser=bool(getattr(args, "open_browser", True)),
                health_timeout=float(getattr(args, "health_timeout", DEFAULT_HEALTH_TIMEOUT_SECONDS)),
            )
            if json_output:
                print(json.dumps(_result_payload(result), indent=2, sort_keys=True))
            else:
                verb = "already connected" if result.reused else "connected"
                print(f"Dashboard tunnel {verb}.")
                print(f"URL: {result.url}")
                print(
                    f"Forward: 127.0.0.1:{result.local_port} -> "
                    f"{result.target}:127.0.0.1:{result.remote_port}"
                )
                print(f"Control socket: {result.control_socket}")
                print(f"Disconnect: tokenpak dashboard disconnect {result.host}")
            return 0
        if action == "disconnect":
            result = disconnect_dashboard(
                args.host,
                ssh_user=getattr(args, "ssh_user", None),
                quiet=json_output,
            )
            if json_output:
                print(json.dumps(_disconnect_payload(result), indent=2, sort_keys=True))
            elif result.metadata_removed:
                print(f"Dashboard tunnel disconnected for {result.host}.")
            return 0
    except DashboardTunnelError as exc:
        if json_output:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("Error: unknown dashboard tunnel action.", file=sys.stderr)
    return 2
