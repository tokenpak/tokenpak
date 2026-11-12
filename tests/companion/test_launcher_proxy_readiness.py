# SPDX-License-Identifier: Apache-2.0
"""``tokenpak claude`` waits a bounded time for the proxy, then fails closed.

A missed health probe used to start Claude directly, unproxied, with no error:
no spend guard and no telemetry for the whole session. The launcher now
retries until a deadline and then exits with ``EXIT_RUNTIME_UNAVAILABLE`` and
one line, unless ``TOKENPAK_ALLOW_DIRECT`` explicitly allows a direct start.
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from tokenpak.cli.exit_codes import EXIT_RUNTIME_UNAVAILABLE
from tokenpak.companion import launcher
from tokenpak.companion.config import CompanionConfig

PROXY = "http://127.0.0.1:59999"


class _Healthy:
    status_code = 200


@pytest.fixture
def launch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated launch: own journal/run dirs, a fixed proxy URL, short waits."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("TOKENPAK_PROXY_URL", PROXY)
    monkeypatch.setenv("TOKENPAK_PROXY_WAIT_SECONDS", "0.3")
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", "0.05")
    monkeypatch.delenv("TOKENPAK_ALLOW_DIRECT", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(CompanionConfig, "run_dir", property(lambda self: run_dir))
    return run_dir


def _run(args: list[str] | None = None) -> tuple[object, dict[str, str] | None]:
    captured: dict[str, str] = {}
    with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
        mock_exec.side_effect = lambda cmd, argv, env: captured.update(env)
        rc = launcher.main(args or [])
    return rc, (captured if mock_exec.called else None)


def test_missed_probe_retries_then_refuses_with_one_line(launch_env, capsys):
    calls = []

    def refuse(url, timeout):
        calls.append(url)
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx, "get", side_effect=refuse):
        rc, child_env = _run()

    assert rc == EXIT_RUNTIME_UNAVAILABLE
    assert child_env is None, "Claude must not start without the proxy"
    assert len(calls) > 1, "a missed probe is retried before giving up"
    err_lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(err_lines) == 1
    assert PROXY in err_lines[0]
    assert "TOKENPAK_ALLOW_DIRECT=1" in err_lines[0]
    # Refused before anything was generated.
    assert list(launch_env.glob("launch-*")) == []


def test_proxy_that_comes_up_inside_the_wait_routes_claude(launch_env, monkeypatch):
    monkeypatch.setenv("TOKENPAK_PROXY_WAIT_SECONDS", "5")
    answers = iter([httpx.ConnectError("refused"), httpx.ConnectError("refused")])

    def late(url, timeout):
        try:
            raise next(answers)
        except StopIteration:
            return _Healthy()

    with patch.object(httpx, "get", side_effect=late):
        _, child_env = _run()

    assert child_env is not None
    assert child_env["ANTHROPIC_BASE_URL"] == PROXY


def test_direct_start_switch_starts_claude_unproxied_and_says_so(launch_env, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_ALLOW_DIRECT", "1")
    calls = []

    def refuse(url, timeout):
        calls.append(url)
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx, "get", side_effect=refuse):
        _, child_env = _run()

    assert child_env is not None
    assert "ANTHROPIC_BASE_URL" not in child_env
    assert len(calls) == 1, "an allowed direct start does not hold the launch"
    assert "starting Claude directly" in capsys.readouterr().err


def test_inherited_route_is_not_held_or_refused(launch_env, monkeypatch, capsys):
    inherited = "http://127.0.0.1:9999"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", inherited)
    calls = []

    def refuse(url, timeout):
        calls.append(url)
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx, "get", side_effect=refuse):
        _, child_env = _run()

    assert child_env is not None
    assert child_env["ANTHROPIC_BASE_URL"] == inherited
    assert len(calls) == 1
    assert "not selected by TokenPak" in capsys.readouterr().err


def test_wait_is_bounded_by_the_deadline():
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    with patch.object(launcher, "_proxy_healthy", return_value=False) as probe:
        ok = launcher._wait_for_proxy(
            PROXY,
            wait_s=2.0,
            probe_timeout_s=1.0,
            interval_s=0.5,
            clock=lambda: now[0],
            sleep=sleep,
        )

    assert ok is False
    assert sum(slept) == pytest.approx(2.0)
    # t=0, 0.5, 1.0, 1.5: no probe starts once the deadline (t=2.0) is reached.
    assert probe.call_count == 4


def test_zero_wait_probes_once():
    with patch.object(launcher, "_proxy_healthy", return_value=False) as probe:
        assert (
            launcher._wait_for_proxy(
                PROXY, wait_s=0, probe_timeout_s=1.0, interval_s=0.5, sleep=lambda s: None
            )
            is False
        )
    assert probe.call_count == 1


@pytest.mark.parametrize("raw", ["", "abc", "-1", "nan", "inf"])
def test_invalid_durations_fall_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("TOKENPAK_PROXY_WAIT_SECONDS", raw)
    assert launcher._env_seconds("TOKENPAK_PROXY_WAIT_SECONDS", 10.0) == 10.0


def test_zero_probe_timeout_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", "0")
    assert (
        launcher._env_seconds("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", 1.0, allow_zero=False) == 1.0
    )


def test_default_proxy_url_honours_the_configured_port(monkeypatch):
    monkeypatch.delenv("TOKENPAK_PROXY_URL", raising=False)
    monkeypatch.setenv("TOKENPAK_PORT", "9123")
    assert launcher._default_proxy_url() == "http://127.0.0.1:9123"


def test_cli_exits_with_the_launcher_refusal_status(monkeypatch):
    from tokenpak import _cli_core

    monkeypatch.setattr(_cli_core, "_require_companion_client", lambda name: None)
    monkeypatch.delenv("TOKENPAK_COMPANION_BUDGET", raising=False)
    with patch("tokenpak.companion.launch", return_value=EXIT_RUNTIME_UNAVAILABLE):
        with pytest.raises(SystemExit) as stopped:
            _cli_core.cmd_claude(argparse.Namespace(args=[], budget=None))
    assert stopped.value.code == EXIT_RUNTIME_UNAVAILABLE
    assert os.environ.get("TOKENPAK_COMPANION_BUDGET") is None


def test_refusal_code_matches_the_documented_exit_code():
    assert launcher._EXIT_RUNTIME_UNAVAILABLE == EXIT_RUNTIME_UNAVAILABLE


# ---------------------------------------------------------------------------
# The total wait holds however the proxy answers
#
# The probe client's timeout is per operation, so a reply that arrives a few
# bytes at a time never tripped it, and the first probe ran with the full
# per-probe timeout even when the configured total wait was shorter.
# ---------------------------------------------------------------------------

# Allowance for thread start-up and scheduling on a loaded test host; far
# below the seconds a misbehaving proxy used to hold a launch.
_SCHEDULING_SLACK_S = 0.5
# How long the misbehaving endpoints keep a probe busy if nothing cuts it off.
_MISBEHAVE_S = 3.0


def test_first_probe_respects_total_wait_budget():
    now = [0.0]
    timeouts: list[float] = []

    def probe(url: str, timeout: float) -> bool:
        timeouts.append(timeout)
        now[0] += timeout
        return False

    with patch.object(launcher, "_proxy_healthy", side_effect=probe):
        assert not launcher._wait_for_proxy(
            PROXY,
            wait_s=0.1,
            probe_timeout_s=2,
            interval_s=0.05,
            clock=lambda: now[0],
            sleep=lambda s: now.__setitem__(0, now[0] + s),
        )
    assert now[0] <= 0.1, f"elapsed={now[0]}; configured total=0.1; probe timeouts={timeouts}"


def test_no_probe_begins_at_expired_deadline():
    now = [0.0]
    starts: list[float] = []

    def probe(url: str, timeout: float) -> bool:
        starts.append(now[0])
        return False

    with patch.object(launcher, "_proxy_healthy", side_effect=probe):
        assert not launcher._wait_for_proxy(
            PROXY,
            wait_s=0.1,
            probe_timeout_s=0.1,
            interval_s=0.1,
            clock=lambda: now[0],
            sleep=lambda s: now.__setitem__(0, now[0] + s),
        )
    assert all(t < 0.1 for t in starts), f"probe starts={starts}"


def test_slow_health_body_cannot_extend_total_wait():
    chunks = int(_MISBEHAVE_S / 0.02)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(chunks):
                    self.wfile.write(b"1\r\nx\r\n")
                    self.wfile.flush()
                    time.sleep(0.02)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        start = time.monotonic()
        result = launcher._wait_for_proxy(
            f"http://127.0.0.1:{server.server_port}",
            wait_s=0.1,
            probe_timeout_s=0.05,
            interval_s=0.01,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 0.1 + 0.05 + _SCHEDULING_SLACK_S, (
            f"configured wait=0.1, probe timeout=0.05, elapsed={elapsed:.3f}, accepted={result}"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def _misbehaving_proxy(mode: str) -> Iterator[str]:
    """A local health endpoint that refuses, stays silent, or answers slowly."""
    stop = threading.Event()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    if mode == "refused":
        listener.close()
        yield url
        return
    listener.listen(16)
    listener.settimeout(0.05)
    accepted: list[socket.socket] = []

    def dribble(conn: socket.socket, head: bytes, piece: bytes, tail: bytes) -> None:
        conn.sendall(head)
        end = time.monotonic() + _MISBEHAVE_S
        while not stop.is_set() and time.monotonic() < end:
            conn.sendall(piece)
            stop.wait(0.02)
        conn.sendall(tail)

    def answer(conn: socket.socket) -> None:
        try:
            conn.settimeout(_MISBEHAVE_S)
            conn.recv(65536)
            if mode == "silent":
                stop.wait(_MISBEHAVE_S)
            elif mode == "slow-headers":
                dribble(conn, b"HTTP/1.1 200 OK\r\n", b"X-Pad: a\r\n", b"Content-Length: 0\r\n\r\n")
            elif mode == "slow-body":
                head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                dribble(conn, head, b"1\r\nx\r\n", b"0\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            accepted.append(conn)
            threading.Thread(target=answer, args=(conn,), daemon=True).start()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    try:
        yield url
    finally:
        stop.set()
        server.join(timeout=2)
        listener.close()
        for conn in accepted:
            conn.close()


@pytest.mark.parametrize("mode", ["refused", "silent", "slow-headers", "slow-body"])
def test_total_wait_holds_however_the_proxy_answers(mode):
    wait_s, probe_s = 0.3, 0.1
    with _misbehaving_proxy(mode) as url:
        started = time.monotonic()
        ok = launcher._wait_for_proxy(url, wait_s=wait_s, probe_timeout_s=probe_s, interval_s=0.05)
        elapsed = time.monotonic() - started
    assert ok is False
    assert elapsed <= wait_s + probe_s + _SCHEDULING_SLACK_S, f"{mode}: elapsed={elapsed:.3f}"


def test_launch_decision_holds_against_a_slow_proxy(launch_env, monkeypatch):
    """End to end: the refusal comes within the configured wait, not when the proxy finishes."""
    with _misbehaving_proxy("slow-headers") as url:
        monkeypatch.setenv("TOKENPAK_PROXY_URL", url)
        started = time.monotonic()
        rc, child_env = _run()
        elapsed = time.monotonic() - started
    assert rc == EXIT_RUNTIME_UNAVAILABLE
    assert child_env is None
    wait_s = float(os.environ["TOKENPAK_PROXY_WAIT_SECONDS"])
    probe_s = float(os.environ["TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS"])
    # main() also builds its configuration before probing; allow for it.
    assert elapsed <= wait_s + probe_s + 2 * _SCHEDULING_SLACK_S, f"elapsed={elapsed:.3f}"


def test_a_late_health_answer_is_not_accepted_after_the_probe_budget():
    def slow(url: str, timeout: float) -> object:
        time.sleep(1.0)
        return type("Healthy", (), {"status_code": 200})()

    started = time.monotonic()
    with patch.object(httpx, "get", side_effect=slow):
        assert launcher._proxy_healthy(PROXY, 0.05) is False
    assert time.monotonic() - started < 0.05 + _SCHEDULING_SLACK_S
