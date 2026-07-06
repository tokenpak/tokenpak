from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tokenpak._cli_core import build_parser
from tokenpak.cli.commands import dashboard_tunnel as tunnel


def test_cli_parser_registers_dashboard_connect_target():
    parser = build_parser()

    args = parser.parse_args(
        [
            "dashboard",
            "connect",
            "dashboard.example.internal",
            "--remote-port",
            "8766",
            "--local-port",
            "auto",
            "--open",
        ]
    )

    assert args.command == "dashboard"
    assert args.dashboard_action == "connect"
    assert args.host == "dashboard.example.internal"
    assert args.remote_port == 8766
    assert args.local_port == "auto"
    assert args.open_browser is True


def test_ssh_command_construction(tmp_path: Path):
    control_socket = tmp_path / "dashboard.sock"

    cmd = tunnel.build_ssh_command(
        "user@dashboard.example.internal",
        8766,
        8766,
        control_socket,
        ssh_bin="ssh",
    )

    assert cmd == [
        "ssh",
        "-N",
        "-M",
        "-S",
        str(control_socket),
        "-o",
        "ExitOnForwardFailure=yes",
        "-L",
        "8766:127.0.0.1:8766",
        "user@dashboard.example.internal",
    ]


def test_local_port_auto_falls_back_on_collision(monkeypatch):
    def fake_available(port: int, host: str = "127.0.0.1") -> bool:
        return port == 8767

    monkeypatch.setattr(tunnel, "port_is_available", fake_available)

    assert tunnel.select_local_port("auto") == 8767


def test_tunnel_paths_create_tpk_tunnels_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / ".tpk"))

    paths = tunnel.state_paths("user@dashboard.example.internal", 8766, 8766)

    assert paths.directory == tmp_path / ".tpk" / "tunnels"
    assert paths.directory.is_dir()
    assert paths.metadata.parent == paths.directory
    assert paths.control_socket.parent == paths.directory
    assert paths.pid_file.parent == paths.directory


def test_existing_valid_tunnel_is_reused(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / ".tpk"))
    monkeypatch.setattr(tunnel, "wait_for_dashboard_health", lambda *a, **kw: True)
    monkeypatch.setattr(tunnel, "tunnel_is_alive", lambda record: True)

    def fail_start(*args, **kwargs):
        raise AssertionError("existing valid tunnel should be reused")

    monkeypatch.setattr(tunnel, "_start_ssh_tunnel", fail_start)
    monkeypatch.setattr(tunnel.webbrowser, "open", lambda url: True)

    target = "user@dashboard.example.internal"
    paths = tunnel.state_paths(target, 8766, 8766)
    record = {
        "host": "dashboard.example.internal",
        "target": target,
        "local_port": 8766,
        "remote_port": 8766,
        "url": "http://127.0.0.1:8766/dashboard",
        "metadata": str(paths.metadata),
        "control_socket": str(paths.control_socket),
        "pid_file": str(paths.pid_file),
    }
    paths.control_socket.write_text("", encoding="utf-8")
    paths.metadata.write_text(json.dumps(record), encoding="utf-8")

    result = tunnel.connect_dashboard(
        "dashboard.example.internal",
        ssh_user="user",
        local_port="auto",
        open_browser=False,
    )

    assert result.reused is True
    assert result.url == "http://127.0.0.1:8766/dashboard"


def test_health_wait_accepts_ok_json(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"ok"}'

    monkeypatch.setattr(tunnel.urllib.request, "urlopen", lambda *a, **kw: Response())

    assert tunnel.wait_for_dashboard_health(8766, timeout_seconds=0) is True


def test_health_wait_fails_when_health_never_returns_ok(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"starting"}'

    monkeypatch.setattr(tunnel.urllib.request, "urlopen", lambda *a, **kw: Response())

    assert tunnel.wait_for_dashboard_health(8766, timeout_seconds=0) is False


def test_disconnect_cleans_metadata_socket_and_pid(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / ".tpk"))
    monkeypatch.setattr(tunnel, "_run_ssh_control", lambda *a, **kw: True)

    target = "user@dashboard.example.internal"
    paths = tunnel.state_paths(target, 8766, 8766)
    record = {
        "host": "dashboard.example.internal",
        "target": target,
        "local_port": 8766,
        "remote_port": 8766,
        "url": "http://127.0.0.1:8766/dashboard",
        "metadata": str(paths.metadata),
        "control_socket": str(paths.control_socket),
        "pid_file": str(paths.pid_file),
    }
    paths.metadata.write_text(json.dumps(record), encoding="utf-8")
    paths.control_socket.write_text("", encoding="utf-8")
    paths.pid_file.write_text("12345\n", encoding="utf-8")

    result = tunnel.disconnect_dashboard("dashboard.example.internal", ssh_user="user")

    assert result.disconnected is True
    assert result.metadata_removed is True
    assert not paths.metadata.exists()
    assert not paths.control_socket.exists()
    assert not paths.pid_file.exists()


def test_connect_health_failure_cleans_started_tunnel(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / ".tpk"))
    monkeypatch.setattr(tunnel, "port_is_available", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(tunnel, "wait_for_dashboard_health", lambda *a, **kw: False)
    monkeypatch.setattr(tunnel, "_run_ssh_control", lambda *a, **kw: True)

    def fake_start(target, local_port, remote_port, paths):
        paths.control_socket.write_text("", encoding="utf-8")
        paths.pid_file.write_text("12345\n", encoding="utf-8")
        return 12345

    monkeypatch.setattr(tunnel, "_start_ssh_tunnel", fake_start)

    with pytest.raises(tunnel.DashboardTunnelError, match="health check did not return OK"):
        tunnel.connect_dashboard(
            "dashboard.example.internal",
            ssh_user="user",
            local_port="auto",
            open_browser=False,
            health_timeout=0,
        )

    paths = tunnel.state_paths("user@dashboard.example.internal", 8766, 8766)
    assert not paths.metadata.exists()
    assert not paths.control_socket.exists()
    assert not paths.pid_file.exists()


def test_cmd_dashboard_tunnel_connect_json(monkeypatch, capsys):
    result = tunnel.DashboardTunnelResult(
        host="dashboard.example.internal",
        target="user@dashboard.example.internal",
        local_port=8766,
        remote_port=8766,
        url="http://127.0.0.1:8766/dashboard",
        reused=False,
        control_socket=Path("/tmp/dashboard.sock"),
        pid_file=Path("/tmp/dashboard.pid"),
    )
    monkeypatch.setattr(tunnel, "connect_dashboard", lambda *a, **kw: result)

    rc = tunnel.cmd_dashboard_tunnel(
        argparse.Namespace(
            dashboard_action="connect",
            host="dashboard.example.internal",
            remote_port=8766,
            local_port="auto",
            ssh_user="user",
            open_browser=True,
            health_timeout=20.0,
            json_output=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["url"] == "http://127.0.0.1:8766/dashboard"


def test_cmd_dashboard_tunnel_disconnect_json_is_pure(monkeypatch, capsys):
    result = tunnel.DisconnectResult(
        host="dashboard.example.internal",
        target="user@dashboard.example.internal",
        disconnected=False,
        metadata_removed=False,
    )

    def fake_disconnect(*args, **kwargs):
        assert kwargs["quiet"] is True
        return result

    monkeypatch.setattr(tunnel, "disconnect_dashboard", fake_disconnect)

    rc = tunnel.cmd_dashboard_tunnel(
        argparse.Namespace(
            dashboard_action="disconnect",
            host="dashboard.example.internal",
            ssh_user="user",
            json_output=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload == {
        "disconnected": False,
        "host": "dashboard.example.internal",
        "metadata_removed": False,
        "ok": True,
        "target": "user@dashboard.example.internal",
    }
