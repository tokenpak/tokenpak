# SPDX-License-Identifier: Apache-2.0
"""Opt-in compatibility checks against an installed native Claude Code binary.

These tests never contact a model provider.  The native client is pointed at a
loopback HTTP stub and all of its home/config/data directories live under the
pytest temporary directory.  Set ``TOKENPAK_TEST_NATIVE_CLAUDE`` to an absolute
Claude Code executable path to run them.

The ordinary hosted suite does not install Claude Code, so a skip there is not
evidence for native hook behavior.  A recorded opt-in run is the evidence.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from tokenpak.companion import launcher
from tokenpak.companion.config import CompanionConfig


def _native_claude() -> Path:
    configured = os.environ.get("TOKENPAK_TEST_NATIVE_CLAUDE")
    if not configured:
        pytest.skip(
            "set TOKENPAK_TEST_NATIVE_CLAUDE to run the native Claude Code compatibility checks"
        )
    path = Path(configured)
    if not path.is_absolute():
        pytest.fail("TOKENPAK_TEST_NATIVE_CLAUDE must be an absolute path")
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        pytest.fail(f"native Claude Code executable is unavailable: {resolved}")
    return resolved


class _OfflineClaudeServer(ThreadingHTTPServer):
    requests: list[dict[str, object]]


class _OfflineClaudeHandler(BaseHTTPRequestHandler):
    server: _OfflineClaudeServer

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        try:
            parsed: object = json.loads(body)
        except json.JSONDecodeError:
            parsed = body.decode("utf-8", errors="replace")
        self.server.requests.append({"path": self.path, "body": parsed})

        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_offline_native_hook_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "offline-ok"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        payload = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        )
        encoded = payload.encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _offline_claude_server() -> Iterator[_OfflineClaudeServer]:
    server = _OfflineClaudeServer(("127.0.0.1", 0), _OfflineClaudeHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _write_probe(path: Path) -> None:
    path.write_text("""#!/usr/bin/env python3
import json
import os
import sys

kind, log_path = sys.argv[1:]
payload = json.load(sys.stdin)
line = json.dumps({"kind": kind, "payload": payload}, sort_keys=True) + "\\n"
fd = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
try:
    os.write(fd, line.encode())
finally:
    os.close(fd)
if kind == "deny":
    print(json.dumps({
        "decision": "block",
        "reason": "native custom policy denial",
    }))
""")
    path.chmod(0o700)


def _write_user_settings(home: Path, probe: Path, log_path: Path, kind: str) -> None:
    settings_dir = home / ".claude"
    settings_dir.mkdir(parents=True)
    command = shlex.join([sys.executable, str(probe), kind, str(log_path)])
    settings_dir.joinpath("settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                    ]
                }
            }
        )
    )


def _generated_settings(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    custom_kind: str,
    python_budget_hook: bool = False,
) -> tuple[Path, Path, Path]:
    home = root / "home"
    home.mkdir()
    log_path = root / "custom-hooks.jsonl"
    probe = root / "hook-probe.py"
    _write_probe(probe)
    _write_user_settings(home, probe, log_path, custom_kind)
    monkeypatch.setenv("HOME", str(home))

    if python_budget_hook:
        package_dir = root / "fallback-package"
        hooks_dir = package_dir / "hooks"
        hooks_dir.mkdir(parents=True)
        source_hook = Path(launcher.__file__).parent / "hooks" / "pre_send.py"
        hooks_dir.joinpath("pre_send.py").symlink_to(source_hook)
        monkeypatch.setattr(launcher, "__file__", str(package_dir / "launcher.py"))

    journal_dir = root / "journal"
    run_dir = root / "run"
    run_dir.mkdir()
    config = CompanionConfig(journal_dir=journal_dir, hooks_enabled=True, mcp_enabled=False)
    settings_path = Path(launcher._write_settings(config, run_dir=run_dir))
    return settings_path, journal_dir, log_path


def _native_env(root: Path, server: _OfflineClaudeServer, journal_dir: Path) -> dict[str, str]:
    home = root / "home"
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "ANTHROPIC_API_KEY": "offline-native-hook-test",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "TOKENPAK_COMPANION_ENABLED": "1",
        "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
        "TOKENPAK_COMPANION_SHOW_COST": "0",
        # The Python fallback hook imports the exact candidate under test.
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    return env


def _run_native(
    binary: Path,
    root: Path,
    settings_path: Path,
    env: dict[str, str],
    prompt: str,
) -> subprocess.CompletedProcess[str]:
    project = root / "project"
    project.mkdir()
    mcp_config = root / "mcp.json"
    mcp_config.write_text('{"mcpServers": {}}')
    command = [
        str(binary),
        "--print",
        prompt,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--permission-mode",
        "bypassPermissions",
        "--settings",
        str(settings_path),
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--debug-file",
        str(root / "claude-debug.log"),
    ]
    return subprocess.run(
        command,
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _custom_events(log_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log_path.read_text().splitlines()]


def test_native_claude_runs_preserved_custom_hook_and_tokenpak_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = _native_claude()
    root = tmp_path / "allow"
    root.mkdir()
    settings_path, journal_dir, log_path = _generated_settings(
        monkeypatch, root, custom_kind="allow"
    )
    settings = json.loads(settings_path.read_text())
    commands = [
        hook["command"]
        for entry in settings["hooks"]["UserPromptSubmit"]
        for hook in entry["hooks"]
    ]
    assert "hook-probe.py allow" in commands[0]
    assert "tokenpak/companion/hooks/pre_send" in commands[1]

    with _offline_claude_server() as server:
        result = _run_native(
            binary,
            root,
            settings_path,
            _native_env(root, server, journal_dir),
            "Return a short offline response.",
        )

    assert result.returncode == 0, result.stderr
    assert len(server.requests) == 1
    events = _custom_events(log_path)
    assert [event["kind"] for event in events] == ["allow"]
    session_id = events[0]["payload"]["session_id"]
    assert journal_dir.joinpath("run", "current-session").read_text() == session_id
    assert json.loads(result.stdout)["result"] == "offline-ok"


def test_native_claude_custom_denial_cannot_be_overridden_by_tokenpak_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = _native_claude()
    root = tmp_path / "custom-deny"
    root.mkdir()
    settings_path, journal_dir, log_path = _generated_settings(
        monkeypatch, root, custom_kind="deny"
    )

    with _offline_claude_server() as server:
        result = _run_native(
            binary,
            root,
            settings_path,
            _native_env(root, server, journal_dir),
            "This prompt must be denied by custom policy.",
        )

    assert len(server.requests) == 0
    assert result.returncode == 0
    native_result = json.loads(result.stdout)
    assert native_result["num_turns"] == 0
    assert "native custom policy denial" in native_result["result"]
    events = _custom_events(log_path)
    session_id = events[0]["payload"]["session_id"]
    assert journal_dir.joinpath("run", "current-session").read_text() == session_id


def test_native_claude_tokenpak_budget_denial_survives_custom_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = _native_claude()
    root = tmp_path / "budget-deny"
    root.mkdir()
    settings_path, journal_dir, log_path = _generated_settings(
        monkeypatch,
        root,
        custom_kind="allow",
        python_budget_hook=True,
    )

    with _offline_claude_server() as server:
        env = _native_env(root, server, journal_dir)
        env["TOKENPAK_COMPANION_BUDGET"] = "0.000001"
        result = _run_native(binary, root, settings_path, env, "x" * 64)

    assert len(server.requests) == 0
    assert result.returncode == 0
    native_result = json.loads(result.stdout)
    assert native_result["num_turns"] == 0
    assert "tokenpak: budget exceeded" in native_result["result"]
    assert [event["kind"] for event in _custom_events(log_path)] == ["allow"]


def test_native_claude_launches_keep_generated_runs_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = _native_claude()
    roots = [tmp_path / "first", tmp_path / "second"]
    generated: list[tuple[Path, Path, Path]] = []
    for root in roots:
        root.mkdir()
        generated.append(_generated_settings(monkeypatch, root, custom_kind="allow"))

    with _offline_claude_server() as server:
        results = [
            _run_native(
                binary,
                root,
                settings_path,
                _native_env(root, server, journal_dir),
                f"Return offline response {index}.",
            )
            for index, (root, (settings_path, journal_dir, _)) in enumerate(
                zip(roots, generated, strict=True)
            )
        ]

    assert [result.returncode for result in results] == [0, 0]
    assert len(server.requests) == 2
    assert generated[0][0] != generated[1][0]
    session_ids = [
        _custom_events(log_path)[0]["payload"]["session_id"] for _, _, log_path in generated
    ]
    assert session_ids[0] != session_ids[1]
    assert all(settings_path.exists() for settings_path, _, _ in generated)
