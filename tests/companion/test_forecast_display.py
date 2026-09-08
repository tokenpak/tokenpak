# SPDX-License-Identifier: Apache-2.0
"""Session isolation, truthful fallbacks, and real shell reader coverage."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.session_economics_fixtures import available_payload, learning_payload
from tokenpak.companion.statusline import launch
from tokenpak.core.contracts.session_economics import SessionEconomics
from tokenpak.status import binding as session_binding
from tokenpak.status import snapshot, worker
from tokenpak.status.display import WIDTHS, render

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "tokenpak/companion/statusline/native.sh"
BIND = ROOT / "tokenpak/companion/hooks/session_bind.sh"


def observed(session="sess-fixture-1"):
    data = learning_payload()
    data["session"]["id"] = session
    data["as_of"] = datetime.now(timezone.utc).isoformat()
    return SessionEconomics.from_dict(data)


def cache(monkeypatch, directory, session="sess-fixture-1"):
    monkeypatch.setenv(session_binding.ENV, str(directory))
    directory.mkdir(exist_ok=True)
    session_binding.write_session(session)
    fact = snapshot.StatusSnapshot(session, time.time(), "proxy", observed(session), "")
    monkeypatch.setattr(worker, "fetch_snapshot", lambda *a, **kw: fact)
    worker.refresh(directory, "http://127.0.0.1:8766", "proxy")
    return fact


def native(directory, payload, *, columns=100, path=None):
    env = {**os.environ, session_binding.ENV: str(directory), "COLUMNS": str(columns)}
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["/bin/bash", str(NATIVE)],
        input=payload,
        env=env,
        text=True,
        capture_output=True,
        timeout=3,
    )


@pytest.mark.parametrize("value", ["../secret", "a.b", "", "a" * 65, "a\n", "a/secret", "$(id)"])
def test_rejects_non_session_path_keys(value, tmp_path, monkeypatch):
    monkeypatch.setenv(session_binding.ENV, str(tmp_path))
    session_binding.write_session(value)
    assert not (tmp_path / "current-session").exists()


def test_bound_launch_never_reads_global_marker(tmp_path, monkeypatch):
    from tokenpak import _paths
    from tokenpak.companion.mcp.tools import current_session_id

    (tmp_path / "current-session").write_text("other-session")
    monkeypatch.setattr(_paths, "companion_run_dir", lambda: tmp_path)
    monkeypatch.setenv(session_binding.ENV, str(tmp_path / "fresh"))
    assert current_session_id() == ""
    session_binding.write_session("this-session")
    assert current_session_id() == "this-session"
    assert (tmp_path / "current-session").read_text() == "other-session"


def test_native_reader_separates_sessions_and_clear(tmp_path, monkeypatch):
    cache(monkeypatch, tmp_path / "first", "session-one")
    cache(monkeypatch, tmp_path / "second", "session-two")
    got = native(tmp_path / "first", json.dumps({"session_id": "session-one"}))
    assert got.returncode == 0 and not got.stderr
    assert "session-" in got.stdout and "forecast learning" in got.stdout
    assert "spent ~$1.23 est" in got.stdout
    changed = native(tmp_path / "first", json.dumps({"session_id": "session-two"}))
    assert "waiting for data" in changed.stdout and "1.23" not in changed.stdout


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "null",
        "{bad",
        '{"session_id":"../../secret"}',
        '{"session_id":"\\u001b[2J"}',
        "x" * 100000,
    ],
)
def test_native_bad_input_is_bounded_unknown(tmp_path, payload):
    got = native(tmp_path, payload)
    assert got.returncode == 0 and got.stdout == "TokenPak | status unavailable"
    assert not got.stderr


@pytest.mark.parametrize("columns", [12, 24, 32, 40, 64, 80, 120])
def test_native_and_ascii_control_obey_terminal_width(tmp_path, monkeypatch, columns):
    fact = cache(monkeypatch, tmp_path)
    got = native(tmp_path, '{"session_id":"sess-fixture-1"}', columns=columns)
    assert len(got.stdout) <= columns - 4
    assert all(32 <= ord(c) < 127 for c in got.stdout)
    assert len("x" * len(got.stdout)) == len(got.stdout)
    for width in WIDTHS:
        line = render(fact, width)
        assert len(line) <= width and "guard allow" in line


def test_expired_cache_cannot_render_numbers(tmp_path, monkeypatch):
    cache(monkeypatch, tmp_path)
    path = tmp_path / "status/sess-fixture-1.line"
    lines = path.read_text().splitlines()
    lines[0] = "1"
    path.write_text("\n".join(lines))
    got = native(tmp_path, '{"session_id":"sess-fixture-1"}')
    assert "stale" in got.stdout and "1.23" not in got.stdout


def test_missing_jq_has_fixed_fallback(tmp_path):
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ["cat", "dirname", "stty"]:
        resolved = shutil.which(name)
        assert resolved
        (bins / name).symlink_to(resolved)
    got = native(tmp_path, '{"session_id":"sess-fixture-1"}', path=str(bins))
    assert got.stdout == "TokenPak | status unavailable" and not got.stderr


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_session_start_binds_quietly_before_first_request(tmp_path, source):
    env = {**os.environ, session_binding.ENV: str(tmp_path)}
    got = subprocess.run(
        ["bash", str(BIND)],
        input=json.dumps({"session_id": "new-id", "source": source}),
        env=env,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert got.returncode == 0 and not got.stdout and not got.stderr
    assert (tmp_path / "current-session").read_text().strip() == "new-id"
    assert (tmp_path / "current-session").stat().st_mode & 0o777 == 0o600


def test_snapshot_does_not_follow_other_session_or_stale_response(monkeypatch):
    data = observed("another-session").to_dict()
    opener = Mock()
    monkeypatch.setattr(snapshot, "build_opener", lambda *a: opener)
    opener.open.return_value = io.BytesIO(json.dumps(data).encode())
    result = snapshot.fetch_snapshot("wanted-session", "http://127.0.0.1:8766")
    assert result.economics is None and result.reason == "session mismatch"
    req = opener.open.call_args.args[0]
    assert json.loads(req.data) == {"session_id": "wanted-session"}
    data["session"]["id"] = "wanted-session"
    data["as_of"] = "2000-01-01T00:00:00Z"
    opener.open.return_value = io.BytesIO(json.dumps(data).encode())
    assert snapshot.fetch_snapshot("wanted-session", "http://localhost:8766").reason == "stale"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "http://user@localhost",
        "http://127.0.0.1/secret",
        "http://127.0.0.1?secret=1",
    ],
)
def test_remote_or_credentialed_endpoint_never_opened(monkeypatch, url):
    opener = Mock()
    monkeypatch.setattr(snapshot, "build_opener", opener)
    assert snapshot.fetch_snapshot("a-session", url).economics is None
    opener.assert_not_called()


def test_snapshot_cache_is_private_and_retains_field_sources(tmp_path, monkeypatch):
    cache(monkeypatch, tmp_path)
    doc = json.loads((tmp_path / "status/sess-fixture-1.json").read_text())
    assert doc["session_cost"]["value"]["state"] == "estimated"
    assert doc["session_cost"]["source"] == "monitor_db"
    assert doc["routing_mode"] == "proxy"
    assert doc["session_budget"]["value"] is None
    assert (tmp_path / "status").stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (tmp_path / "status").iterdir())


def test_surface_selection_keeps_native_arguments_and_explicit_off():
    assert launch.options(["--status-surface=off", "--model", "selected"], "codex") == (
        ["--model", "selected"],
        "off",
    )
    assert launch.options(["--", "--status-surface=off"], "codex")[0] == [
        "--",
        "--status-surface=off",
    ]
    with pytest.raises(ValueError, match="no custom native"):
        launch.options(["--status-surface=native"], "codex")


def test_available_forecast_preserves_currency_precision_and_intervals():
    economics = SessionEconomics.from_dict(available_payload())
    fact = snapshot.StatusSnapshot("sess-fixture-1", time.time(), "proxy", economics, "")
    line = render(fact, 240)
    assert "remain est 0.41-1.64 USD (50%)" in line
    assert "90% ceiling ~3.28 est USD" in line
    assert "spent ~$1.23 est" in line
    assert "guard limit ~14 turns est" in line


def test_surface_detection_does_not_interpret_model_or_config_values(monkeypatch):
    monkeypatch.setattr(launch.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(launch.sys.stdout, "isatty", lambda: True)
    assert launch.interactive(["--model", "review", "--config", "exec"])
    assert launch.interactive(["resume", "previous-session"])
    assert not launch.interactive(["--model", "local", "exec", "task"])
    assert not launch.interactive(["--print", "task"])


def test_clear_removes_only_previous_disposable_cache(tmp_path, monkeypatch):
    cache(monkeypatch, tmp_path, "before-clear")
    (tmp_path / "journal-history").write_text("preserved")
    cache(monkeypatch, tmp_path, "after-clear")
    assert {p.name for p in (tmp_path / "status").iterdir()} == {
        "after-clear.line",
        "after-clear.json",
    }
    assert (tmp_path / "journal-history").read_text() == "preserved"


def test_claude_overlay_refreshes_when_idle_and_preserves_user_file(tmp_path, monkeypatch):
    from tokenpak.companion import launcher
    from tokenpak.companion.config import CompanionConfig

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    user = tmp_path / ".claude/settings.json"
    user.parent.mkdir()
    original = '{"statusLine":{"type":"command","command":"echo custom"}}'
    user.write_text(original)
    assert launch.user_statusline([])
    config = CompanionConfig(journal_dir=tmp_path / "journal")
    directory = session_binding.create_launch_dir(tmp_path / "run")
    overlay = json.loads(
        Path(launcher._write_settings(config, run_dir=directory, status_line=True)).read_text()
    )
    assert overlay["statusLine"]["refreshInterval"] == 2
    assert "native.sh" in overlay["statusLine"]["command"]
    assert "SessionStart" in overlay["hooks"]
    assert user.read_text() == original


def test_real_local_endpoint_writer_cli_and_shutdown(tmp_path, monkeypatch):
    """Exercise installed-style subprocesses against a local fixture, never a provider."""
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            payload = observed(body["session_id"]).to_dict()
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    directory = session_binding.create_launch_dir(tmp_path)
    monkeypatch.setenv(session_binding.ENV, str(directory))
    session_binding.write_session("exact-session")
    port = str(server.server_port)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "TOKENPAK_PORT": port,
        "TOKENPAK_FORECAST_PROXY": "http://127.0.0.1:" + port,
    }
    process = None
    try:
        process = launch.start_writer(env)
        deadline = time.monotonic() + 8
        while (
            not (directory / "status/exact-session.line").exists() and time.monotonic() < deadline
        ):
            assert process.poll() is None, "writer exited before caching"
            time.sleep(0.05)
        got = native(directory, '{"session_id":"exact-session"}')
        assert "spent ~$1.23 est" in got.stdout
        cli = subprocess.run(
            [sys.executable, "-m", "tokenpak", "status", "--line", "--session", "explicit-session"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert cli.returncode == 0, cli.stderr
        assert "TP explicit" in cli.stdout and "forecast learning" in cli.stdout
        assert ("/v1/messages/session-economics", {"session_id": "explicit-session"}) in requests
        assert all(path == "/v1/messages/session-economics" for path, _ in requests)
    finally:
        launch.stop_writer(process)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert process is not None and process.poll() is not None
