"""Linux loopback proof that session churn cannot grow half-closed sockets.

The stub deliberately half-closes otherwise keep-alive upstream connections.
That puts every still-owned client socket into CLOSE-WAIT without contacting a
real provider. Churning beyond the session-client cap must close each displaced
client promptly, so both CLOSE-WAIT and FD counts plateau instead of growing
one-for-one with requests.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.proxy._proxy_subprocess import ProxyProc

pytestmark = [
    pytest.mark.needs_proxy,
    pytest.mark.timeout(120),
    pytest.mark.skipif(
        not Path("/proc/self/fd").is_dir(),
        reason="socket ownership assertion requires Linux procfs",
    ),
]

_CAP = 32
_ROUNDS = 3


class _HalfCloseUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        with self.server.connections_lock:  # type: ignore[attr-defined]
            self.server.connections.add(self.connection)  # type: ignore[attr-defined]
        body = json.dumps(
            {
                "id": "msg_loopback",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "model": "claude-sonnet-4-5",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


def _post(proxy: ProxyProc, index: int) -> int:
    body = json.dumps(
        {
            "model": "claude-sonnet-4-5",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": f"loopback-{index}"}],
        }
    ).encode()
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=15)
    try:
        conn.putrequest("POST", "/v1/messages")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("x-api-key", "offline-test-key")
        conn.putheader("x-tokenpak-session-id", f"half-close-{index}")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def _half_close_all(server: ThreadingHTTPServer) -> None:
    with server.connections_lock:  # type: ignore[attr-defined]
        connections = list(server.connections)  # type: ignore[attr-defined]
    for connection in connections:
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _owned_tcp_states(pid: int, remote_port: int | None = None) -> dict[str, int]:
    socket_inodes: set[str] = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:["):
            socket_inodes.add(target[8:-1])

    states: dict[str, int] = {}
    for raw in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]:
        fields = raw.split()
        if len(fields) < 10 or fields[9] not in socket_inodes:
            continue
        if remote_port is not None:
            observed_port = int(fields[2].rsplit(":", 1)[1], 16)
            if observed_port != remote_port:
                continue
        state = fields[3]
        states[state] = states.get(state, 0) + 1
    return states


def _health(proxy: ProxyProc) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=3)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 200
        return json.loads(response.read())
    finally:
        conn.close()


def _wait_for_close_wait(pid: int, remote_port: int, maximum: int, timeout: float = 2.0) -> int:
    deadline = time.monotonic() + timeout
    count = 0
    while time.monotonic() < deadline:
        count = _owned_tcp_states(pid, remote_port).get("08", 0)  # Linux TCP_CLOSE_WAIT
        if 0 < count <= maximum:
            return count
        time.sleep(0.01)
    return count


def _wait_for_live_drain(proxy: ProxyProc, remote_port: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        health = _health(proxy)
        metrics = health.get("connection_pool", health.get("pool", {}))
        close_wait = _owned_tcp_states(proxy.proc.pid, remote_port).get("08", 0)
        last = {
            "close_wait": close_wait,
            "cleanup_pending_close": metrics.get("cleanup_pending_close"),
            "retired_pending_close": metrics.get("retired_pending_close"),
        }
        if last == {
            "close_wait": 0,
            "cleanup_pending_close": 0,
            "retired_pending_close": 0,
        }:
            return health
        time.sleep(0.02)
    pytest.fail(f"proxy did not drain half-closed sockets while live: {last}")


def test_half_closed_upstream_sockets_and_fds_plateau_at_session_cap():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _HalfCloseUpstreamHandler)
    upstream.connections = set()  # type: ignore[attr-defined]
    upstream.connections_lock = threading.Lock()  # type: ignore[attr-defined]
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    stub_url = f"http://127.0.0.1:{upstream.server_port}"
    assert stub_url.startswith("http://127.0.0.1:")

    proxy = ProxyProc(stub_url)
    samples: list[tuple[int, int]] = []
    try:
        proxy.wait_ready()
        baseline_fds = len(list(Path(f"/proc/{proxy.proc.pid}/fd").iterdir()))

        for round_index in range(_ROUNDS):
            start = round_index * _CAP
            statuses = [_post(proxy, index) for index in range(start, start + _CAP)]
            assert statuses == [200] * _CAP
            _half_close_all(upstream)
            close_wait = _wait_for_close_wait(proxy.proc.pid, upstream.server_port, _CAP)
            fds = len(list(Path(f"/proc/{proxy.proc.pid}/fd").iterdir()))
            samples.append((close_wait, fds))

        close_wait_counts = [sample[0] for sample in samples]
        fd_counts = [sample[1] for sample in samples]
        assert all(0 < count <= _CAP for count in close_wait_counts), samples
        assert max(close_wait_counts) - min(close_wait_counts) <= 1, samples
        assert max(fd_counts) - min(fd_counts) <= 2, samples
        # Session sockets plus the monitor DB's WAL/SHM descriptors. The
        # plateau assertion above is the primary leak guard; this absolute
        # ceiling prevents a stable but unexpectedly large offset.
        assert max(fd_counts) <= baseline_fds + _CAP + 4, samples

        health = _health(proxy)
        metrics = health.get("connection_pool", health.get("pool", {}))
        assert metrics["retired_pending_close"] == 0
        assert metrics["cleanup_pending_close"] == 0
        assert metrics["client_slots_used"] == _CAP
        assert metrics["client_capacity_rejections_total"] == 0

        # Keep the proxy alive and replace the entire final half-closed
        # generation with fresh sessions. Do not half-close these replacements:
        # LRU cleanup must make both kernel CLOSE-WAIT and user-space pending
        # ownership converge to zero without relying on process termination.
        drain_start = _ROUNDS * _CAP
        drain_statuses = [_post(proxy, index) for index in range(drain_start, drain_start + _CAP)]
        assert drain_statuses == [200] * _CAP
        drained_health = _wait_for_live_drain(proxy, upstream.server_port)
        assert proxy.proc.poll() is None
        assert drained_health["status"] == "ok"
        drained_metrics = drained_health.get("connection_pool", drained_health.get("pool", {}))
        assert drained_metrics["client_slots_used"] == _CAP

        started = time.monotonic()
        proxy.proc.terminate()
        assert proxy.proc.wait(timeout=5) == 0
        assert time.monotonic() - started < 5.0
    finally:
        proxy.cleanup()
        with upstream.connections_lock:  # type: ignore[attr-defined]
            connections = list(upstream.connections)  # type: ignore[attr-defined]
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
