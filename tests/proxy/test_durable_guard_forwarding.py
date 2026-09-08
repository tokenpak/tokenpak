"""Exercise durable guard accounting through real HTTP forwarding and snapshots."""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy import server as server_module
from tokenpak.proxy.spend_guard.request_accounting import RequestAccounting
from tokenpak.proxy.spend_guard.reservation import ReservationStore

pytestmark = pytest.mark.needs_proxy


@pytest.fixture
def domain(tmp_path, monkeypatch):
    assert monitor_module._stop_db_write_queue(timeout=3)
    db = tmp_path / "monitor.db"
    audit = tmp_path / "spend_guard.db"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "spend_guard": {
                    "enabled": True,
                    "reservations_enabled": True,
                    "audit_db_path": str(audit),
                    "rolling_caps_per_agent_max_cost_usd": 0,
                    "rolling_caps_per_fleet_max_cost_usd": 0.0004,
                }
            }
        )
    )
    monkeypatch.setenv("TOKENPAK_CONFIG", str(config))
    monkeypatch.setenv("TOKENPAK_SPEND_GUARD_ENABLED", "1")
    monkeypatch.setenv("TOKENPAK_SPEND_GUARD_RESERVATIONS_ENABLED", "1")
    monkeypatch.setenv("TOKENPAK_PROXY_KEY", "accounting-test-key")
    monkeypatch.setenv("TOKENPAK_PASSTHROUGH", "1")
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")
    monkeypatch.setattr("tokenpak.proxy.config.MONITOR_DB", str(db))
    monkeypatch.setattr(server_module, "INTERCEPT_HOSTS", {"127.0.0.1"})
    received = []
    entered, release = threading.Event(), threading.Event()
    release.set()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(raw)
            entered.set()
            assert release.wait(20)
            if self.path.endswith("/retry") and len(received) == 1:
                self.send_response(503)
                self.send_header("Retry-After", "0")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            usage = {
                "input_tokens": 8,
                "output_tokens": 2,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
            if self.path.endswith("/missing-cache"):
                usage.pop("cache_creation_input_tokens")
            if json.loads(raw).get("stream"):
                reply = (
                    "event: message_start\ndata: "
                    + json.dumps({"type": "message_start", "message": {"usage": usage}})
                    + "\n\nevent: message_stop\ndata: "
                    + json.dumps({"type": "message_stop"})
                    + "\n\n"
                ).encode()
                if self.path.endswith("/partial"):
                    reply = (
                        "event: message_start\ndata: "
                        + json.dumps({"type": "message_start", "message": {"usage": usage}})
                        + "\n\n"
                    ).encode()
                content_type = "text/event-stream"
            else:
                reply = json.dumps({"content": [], "usage": usage}).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    proxy = server_module.ProxyServer(host="127.0.0.1", port=0)
    allowed_ports = {upstream.server_port}
    connect = socket.socket.connect

    def confined_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert address[0] in ("127.0.0.1", "::1") and address[1] in allowed_ports, (
                "unexpected network destination"
            )
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", confined_connect)
    upstream_base = f"http://127.0.0.1:{upstream.server_port}"
    proxy.router = server_module.ProviderRouter(
        custom_urls={"anthropic": upstream_base},
        custom_hosts={upstream_base: "anthropic"},
    )
    proxy.start(blocking=False)
    allowed_ports.add(proxy.port)
    yield proxy, upstream, ReservationStore(audit, db), received, entered, release
    release.set()
    proxy.stop()
    upstream.shutdown()
    upstream.server_close()
    worker.join(timeout=3)
    assert monitor_module._stop_db_write_queue(timeout=3)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            monitor_module._DB_CONNECTION.close()
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None


def _send(
    domain,
    *,
    headers=None,
    stream=False,
    text="synthetic accounting check",
    endpoint="/v1/messages",
):
    proxy, upstream, *_ = domain
    request = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": text}],
        "stream": stream,
    }
    combined = {
        "Content-Type": "application/json",
        "x-api-key": "test-only",
        "X-TokenPak-Session": "explicit-session",
        "X-TokenPak-Agent": "test-agent",
    }
    combined.update(headers or {})
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=30)
    try:
        conn.request(
            "POST",
            f"http://127.0.0.1:{upstream.server_port}{endpoint}",
            json.dumps(request).encode(),
            combined,
        )
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _snapshot(domain, *, workload=False, headers=None):
    proxy = domain[0]
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    try:
        conn.request(
            "POST",
            "/tpk/v1/sessions/workload-snapshot" if workload else "/tpk/v1/sessions/guard-snapshot",
            json.dumps({"session_id": "explicit-session"}).encode(),
            {
                "Content-Type": "application/json",
                "X-TPK-Key": "accounting-test-key",
                **(headers or {}),
            },
        )
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def _settled_snapshot(domain):
    assert domain[0].monitor.flush(timeout=3)
    deadline = time.monotonic() + 3
    while True:
        status, payload = _snapshot(domain)
        if status == 200 and "request_in_progress" not in payload["reason_codes"]:
            return status, payload
        if time.monotonic() > deadline:
            pytest.fail(
                f"accounting did not finish: status={status}, reasons={payload.get('reason_codes')}"
            )
        time.sleep(0.02)


@pytest.mark.parametrize("stream", [False, True])
def test_complete_provider_usage_can_produce_eligible_native_evidence(domain, stream):
    status, _ = _send(domain, stream=stream)
    assert status == 200
    status, snapshot = _settled_snapshot(domain)
    assert status == 200 and snapshot["guard_evidence_eligible"], snapshot.get("reason_codes")
    assert snapshot["schema_version"] == "native-guard-snapshot/2"
    assert snapshot["session_id"] == "explicit-session"
    assert snapshot["components_may_overlap"] is False
    assert snapshot["pending_projected_usage"]["fleet_cost_usd"] == 0
    assert snapshot["recorded_usage"]["fleet_tokens_total"] == 10
    assert snapshot["recorded_usage"]["fleet_cost_usd"] > 0
    assert len(domain[3]) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_final_forwarded_bytes_are_bound_without_promoting_a_custom_gateway(domain, stream):
    import hashlib

    assert _send(domain, stream=stream)[0] == 200
    assert _settled_snapshot(domain)[0] == 200
    status, snapshot = _snapshot(domain, workload=True)
    assert status == 200, snapshot
    assert snapshot["schema_version"] == "native-workload-snapshot/1"
    evidence = snapshot["workload_observation"]
    assert evidence["workload"]["body_sha256"] == hashlib.sha256(domain[3][0]).hexdigest()
    assert not evidence["available"]
    assert "route_unsupported" in evidence["reason_codes"]
    raw = json.dumps(snapshot)
    for forbidden in (
        "synthetic accounting check",
        "test-only",
        "/v1/messages",
        "127.0.0.1",
        "spend_guard.db",
    ):
        assert forbidden not in raw
    assert _snapshot(domain)[1]["schema_version"] == "native-guard-snapshot/2"


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"X-TPK-Key": "wrong"}, 401),
        ({"Origin": "http://localhost"}, 403),
        ({"X-TokenPak-Session": "another-session"}, 400),
    ],
)
def test_workload_endpoint_reuses_auth_and_explicit_identity_checks(domain, headers, status):
    assert _snapshot(domain, workload=True, headers=headers)[0] == status
    assert not domain[3]


def test_busy_accounting_store_returns_local_state_refusal_before_provider_send(domain):
    _, _, store, received, *_ = domain
    setup = store.begin_request("setup-session", "setup-instance")
    store.finish_request(setup)
    with sqlite3.connect(store.path) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        status, raw = _send(domain)
        writer.rollback()
    assert status == 402
    error = json.loads(raw)["error"]
    assert error["reason"] == "spend_guard_state_unavailable"
    assert error["approval_prompt_available"] is False
    assert not received
    with sqlite3.connect(store.path) as reader:
        assert reader.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_concurrent_request_is_denied_before_provider_or_reservation_insert(domain, monkeypatch):
    _, _, store, received, entered, release = domain
    denial_finished = threading.Event()
    finish = RequestAccounting.finish

    def observed_finish(accounting):
        finish(accounting)
        if accounting.attempts == 0:
            denial_finished.set()

    monkeypatch.setattr(RequestAccounting, "finish", observed_finish)
    release.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_send, domain)
        try:
            assert entered.wait(10)
            status, body = _send(domain)
            assert status == 402, body
            error = json.loads(body)["error"]
            assert error["type"] == "tokenpak_spend_guard_reservation_blocked"
            assert error["settled_used"] == 0 and error["reserved_active"] > 0
            assert len(received) == 1
            with sqlite3.connect(store.path) as conn:
                assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1
            # The 402 response reaches the client before the handler's finally
            # records completion. Observe after that write; a snapshot racing
            # it must correctly return 503 under the generation fence.
            assert denial_finished.wait(5), "denied request accounting did not finish"
            status, snapshot = _snapshot(domain)
            assert status == 200 and not snapshot["guard_evidence_eligible"], snapshot
            assert "request_in_progress" in snapshot["reason_codes"]
        finally:
            release.set()
        assert pending.result(timeout=5)[0] == 200


def test_untagged_request_still_obeys_overall_cap(domain):
    _, _, _, received, entered, release = domain
    release.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_send, domain, headers={"X-TokenPak-Agent": ""})
        try:
            assert entered.wait(10)
            assert _send(domain, headers={"X-TokenPak-Agent": ""})[0] == 402
            assert len(received) == 1
        finally:
            release.set()
        assert pending.result(timeout=5)[0] == 200


def test_force_cannot_cross_context_hard_stop(domain, monkeypatch):
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda _: 1
    )
    status, body = _send(domain, text="[TIP: bypass=on] synthetic hard-stop check")
    assert status == 402, body
    assert not domain[3]
    with sqlite3.connect(domain[2].path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_unmetered_forward_invalidates_otherwise_complete_observation(domain):
    assert _send(domain)[0] == 200
    assert _settled_snapshot(domain)[1]["guard_evidence_eligible"]
    assert _send(domain, endpoint="/unmetered")[0] == 200
    status, snapshot = _settled_snapshot(domain)
    assert status == 200 and not snapshot["guard_evidence_eligible"]
    assert "forward_usage_unresolved" in snapshot["reason_codes"]


def test_alternate_backend_is_covered_before_delegation(domain, monkeypatch):
    called = []

    def alternate(handler, body):
        called.append(body)
        handler.send_response(200)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    monkeypatch.setattr(server_module._ProxyHandler, "_handle_claude_code_backend", alternate)
    assert _send(domain, headers={"X-TokenPak-Backend": "claude-code"})[0] == 200
    assert called and not domain[3]
    assert "forward_usage_unresolved" in _settled_snapshot(domain)[1]["reason_codes"]


def test_retries_cannot_be_mistaken_for_one_fully_measured_send(domain, monkeypatch):
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRIES", "2")
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRY_BASE_WAIT", "0")
    assert _send(domain, endpoint="/v1/messages/retry")[0] == 200
    assert len(domain[3]) == 2
    assert domain[0].monitor.flush(timeout=3)
    status, payload = _snapshot(domain)
    assert status == 503 and payload["error"] == "snapshot_resolution_unavailable"
    with sqlite3.connect(domain[2].path) as conn:
        row = conn.execute("SELECT attempts FROM budget_guard_coverage").fetchone()
        assert row[0] == 2


def test_missing_cache_measurement_does_not_become_zero(domain):
    assert _send(domain, endpoint="/v1/messages/missing-cache")[0] == 200
    assert domain[0].monitor.flush(timeout=3)
    status, payload = _snapshot(domain)
    assert status == 503 and payload["error"] == "snapshot_resolution_unavailable"


def test_partial_stream_usage_is_not_a_complete_result(domain):
    assert _send(domain, endpoint="/v1/messages/partial", stream=True)[0] == 200
    assert domain[0].monitor.flush(timeout=3)
    assert _snapshot(domain)[0] == 503


def test_connect_tunnel_records_unmeasurable_coverage(domain):
    proxy, upstream, store, *_ = domain
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    try:
        conn.request(
            "CONNECT",
            f"127.0.0.1:{upstream.server_port}",
            headers={"X-TokenPak-Session": "explicit-session"},
        )
        assert conn.getresponse().status == 200
    finally:
        conn.close()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with sqlite3.connect(store.path) as ledger:
            row = ledger.execute("SELECT attempts, ended_at FROM budget_guard_coverage").fetchone()
        if row and row[1] is not None:
            break
        time.sleep(0.02)
    assert row[0] == 1 and row[1] is not None
    assert "forward_usage_unresolved" in _snapshot(domain)[1]["reason_codes"]
