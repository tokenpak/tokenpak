"""Actual proxy entrypoints with confined HTTPX synthetic upstream transport."""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from tokenpak.proxy import circuit_breaker as breaker_module
from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy import server as server_module
from tokenpak.proxy.spend_guard._context_window import get_model_max_context

pytestmark = pytest.mark.needs_proxy
MODEL = "claude-sonnet-4-6"
URL = "https://api.anthropic.com/v1/messages"


@pytest.fixture
def domain(tmp_path, monkeypatch):
    # Each synthetic server owns its provider health; one refusal fixture must
    # not pre-open the next server's circuit. Multi-request behavior is tested
    # explicitly within a single domain below.
    monkeypatch.setattr(breaker_module, "_registry", breaker_module.CircuitBreakerRegistry())
    assert monitor_module._stop_db_write_queue(timeout=3)
    audit, db, config = tmp_path / "guard.db", tmp_path / "monitor.db", tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "tip_spend_guard": {
                    "enabled": True,
                    "reservations_enabled": True,
                    "accounting_basis": "provider_tokens",
                    "audit_db_path": str(audit),
                    "rolling_caps_per_agent_max_cost_usd": 0,
                    "rolling_caps_per_fleet_max_cost_usd": 0,
                    "rolling_caps_per_fleet_max_tokens_total": get_model_max_context(MODEL) + 32,
                    "rolling_caps_per_agent_max_tokens_total": get_model_max_context(MODEL) + 32,
                }
            }
        )
    )
    for key, value in {
        "TOKENPAK_CONFIG": str(config),
        "TOKENPAK_PROXY_KEY": "local-test-key",
        "TOKENPAK_HOME": str(tmp_path / "state"),
        "TOKENPAK_PASSTHROUGH": "1",
        "TOKENPAK_CAPSULE_BUILDER": "0",
        "TOKENPAK_UPSTREAM_RETRIES": "1",
        "TOKENPAK_CREDS_ROUTER_ENABLED": "0",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("tokenpak.proxy.config.MONITOR_DB", str(db))
    received = []
    entered, release = threading.Event(), threading.Event()
    release.set()
    behavior = {}

    def transport(request):
        assert str(request.url) in (URL, URL + "?beta=true")
        received.append((bytes(request.content), dict(request.headers)))
        entered.set()
        assert release.wait(10)
        if behavior.get("status"):
            return httpx.Response(behavior["status"], json={"error": "synthetic refusal"})
        usage = {
            "input_tokens": 8,
            "output_tokens": 2,
            "cache_read_input_tokens": 4,
            "cache_creation_input_tokens": 3,
        }
        if behavior.get("missing_usage"):
            usage.pop("cache_creation_input_tokens")
        if json.loads(request.content).get("stream"):
            events = [
                {
                    "type": "message_start",
                    "message": {"model": MODEL, "usage": {**usage, "output_tokens": 0}},
                },
                {"type": "message_delta", "usage": {"output_tokens": 2}},
            ]
            if not behavior.get("partial"):
                events.append({"type": "message_stop"})
            content = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
            return httpx.Response(
                200, stream=httpx.ByteStream(content), headers={"Content-Type": "text/event-stream"}
            )
        return httpx.Response(200, json={"model": MODEL, "content": [], "usage": usage})

    proxy = server_module.ProxyServer(host="127.0.0.1", port=0)
    proxy._connection_pool._make_client = lambda: httpx.Client(
        transport=httpx.MockTransport(transport)
    )
    allowed_ports = set()
    original_connect = socket.socket.connect

    def confined_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert address[0] in ("127.0.0.1", "::1") and address[1] in allowed_ports, (
                "unexpected real network destination"
            )
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", confined_connect)
    proxy.start(blocking=False)
    allowed_ports.add(proxy.port)
    yield proxy, audit, config, received, entered, release, behavior
    release.set()
    proxy.stop()
    assert monitor_module._stop_db_write_queue(timeout=3)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            monitor_module._DB_CONNECTION.close()
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None


def send(domain, *, stream=False, headers=None, body=None, url=URL, raw_body=None):
    request = body or {
        "model": MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "synthetic work"}],
        "stream": stream,
    }
    combined = {
        "Content-Type": "application/json",
        "Authorization": "Bearer synthetic-private",
        "X-TokenPak-Session": "session-a",
        "X-TokenPak-Agent": "agent-a",
    }
    combined.update(headers or {})
    conn = http.client.HTTPConnection("127.0.0.1", domain[0].port, timeout=15)
    try:
        conn.request(
            "POST", url, json.dumps(request).encode() if raw_body is None else raw_body, combined
        )
        result = conn.getresponse()
        return result.status, result.read()
    finally:
        conn.close()


def snapshot(domain, *, endpoint="token-snapshot", payload=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", domain[0].port, timeout=5)
    try:
        conn.request(
            "POST",
            "/tpk/v1/sessions/" + endpoint,
            json.dumps({"session_id": "session-a"} if payload is None else payload).encode(),
            {"Content-Type": "application/json", "X-TPK-Key": "local-test-key", **(headers or {})},
        )
        result = conn.getresponse()
        return result.status, dict(result.headers), json.loads(result.read())
    finally:
        conn.close()


def settled(domain):
    assert domain[0].monitor.flush(timeout=3)
    until = time.monotonic() + 3
    while time.monotonic() < until:
        result = snapshot(domain)
        if result[0] == 200 and result[2]["token_coverage_complete"]:
            return result
        time.sleep(0.02)
    return result


@pytest.mark.parametrize("stream", [False, True])
def test_actual_forward_and_commit_yield_observed_tokens_with_null_money(domain, stream):
    assert send(domain, stream=stream)[0] == 200
    status, headers, result = settled(domain)
    assert status == 200 and result["token_coverage_complete"], result
    assert headers["Cache-Control"] == "no-store"
    observation = result["token_observation"]
    assert observation["available"], observation
    facts = observation["observation"]
    assert (facts["input_tokens"], facts["output_tokens"]) == (15, 2)
    assert facts["cache_creation_tokens"] == 3
    assert facts["actual_cost_usd"] is None
    assert facts["authentication_kind"] == "forwarded_bearer_unclassified"
    assert facts["comparison_reason_codes"] == ["feature_profile_unclassified"]
    import hashlib

    assert facts["body_sha256"] == hashlib.sha256(domain[3][0][0]).hexdigest()
    assert snapshot(domain, endpoint="guard-snapshot")[0] == 503
    assert snapshot(domain, endpoint="workload-snapshot")[0] == 503
    for secret in ("synthetic-private", "synthetic work", URL, str(domain[1])):
        assert secret not in json.dumps(result)
    with sqlite3.connect(domain[1]) as conn:
        assert conn.execute(
            "SELECT status, actual_cost_usd FROM budget_reservations"
        ).fetchone() == ("settled", None)


@pytest.mark.parametrize(
    "headers,status", [({"X-TPK-Key": "wrong"}, 401), ({"Origin": "https://browser.invalid"}, 403)]
)
def test_authentication_refuses_before_store_or_provider(domain, headers, status):
    assert snapshot(domain, headers=headers)[0] == status
    assert not domain[1].exists()
    assert domain[3] == []


def test_missing_key_and_invalid_explicit_session_are_read_only(domain, monkeypatch):
    assert snapshot(domain, payload={"session_id": "session-a", "now": 1})[0] == 400
    assert snapshot(domain, headers={"thread-id": "other"})[0] == 400
    monkeypatch.delenv("TOKENPAK_PROXY_KEY")
    assert snapshot(domain)[0] == 503
    assert not domain[1].exists() and domain[3] == []


def test_unavailable_native_usage_stays_unsettled(domain):
    domain[6]["missing_usage"] = True
    assert send(domain)[0] == 200
    assert domain[0].monitor.flush(timeout=3)
    result = snapshot(domain)
    assert result[0] == 503 or not result[2]["token_coverage_complete"]
    with sqlite3.connect(domain[1]) as conn:
        assert conn.execute(
            "SELECT status, actual_cost_usd FROM budget_reservations"
        ).fetchone() == ("active", None)


def test_conservative_context_reservation_blocks_concurrent_send(domain):
    domain[5].clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, domain)
        assert domain[4].wait(8)
        second = pool.submit(send, domain)
        assert second.result(timeout=8)[0] == 402
        assert len(domain[3]) == 1
        domain[5].set()
        assert first.result(timeout=8)[0] == 200
    assert settled(domain)[2]["token_coverage_complete"]


def test_unpriced_mode_cannot_silently_use_engaged_dollar_cap(domain):
    path = Path(domain[2])
    data = json.loads(path.read_text())
    data["tip_spend_guard"]["rolling_caps_per_fleet_max_cost_usd"] = 1
    path.write_text(json.dumps(data))
    assert send(domain)[0] >= 400
    assert domain[3] == []


def test_opaque_or_custom_routes_never_send_in_token_mode(domain):
    assert send(domain, url="https://custom.invalid/v1/messages")[0] >= 400
    assert domain[3] == []


def test_retry_budget_does_not_reuse_one_token_reservation(domain, monkeypatch):
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRIES", "2")
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRY_BASE_WAIT", "0")
    domain[6]["status"] = 503
    assert send(domain)[0] >= 400
    assert len(domain[3]) == 1
    result = snapshot(domain)
    assert result[0] == 503 or not result[2]["token_coverage_complete"]


def test_partial_native_stream_cannot_complete_token_coverage(domain):
    domain[6]["partial"] = True
    assert send(domain, stream=True)[0] == 200
    assert domain[0].monitor.flush(timeout=3)
    result = snapshot(domain)
    assert result[0] == 503 or not result[2]["token_coverage_complete"]


@pytest.mark.parametrize("directive", ["[TIP: bypass]", "[TIP: max=$1]"])
def test_token_mode_refuses_bypass_and_request_money_before_upstream(domain, directive):
    body = {
        "model": MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": directive + " synthetic work"}],
    }
    assert send(domain, body=body)[0] >= 400
    assert domain[3] == []


@pytest.mark.parametrize("failure", [RuntimeError, ImportError])
def test_token_mode_proxy_hook_failure_never_sends(domain, monkeypatch, failure):
    from tokenpak.proxy.spend_guard.request_accounting import RequestAccounting

    def fail(*_):
        raise failure("synthetic preflight failure")

    monkeypatch.setattr(RequestAccounting, "observe_directive", fail)
    assert send(domain)[0] == 402
    assert domain[3] == []


def test_coverage_writer_contention_refuses_before_upstream(domain):
    from tokenpak.proxy.spend_guard.reservation import ReservationStore

    store = ReservationStore(
        domain[1], domain[0].monitor.db_path, accounting_basis="provider_tokens"
    )
    seed = store.begin_request("seed", "seed-owner")
    store.finish_request(seed)
    with sqlite3.connect(domain[1]) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        status, raw = send(domain)
        payload = json.loads(raw)
        assert status == 402
        assert payload["error"]["type"] == "tokenpak_spend_guard_blocked"
        assert payload["error"]["failure_kind"] == "spend_guard_internal_error"
        assert domain[3] == []
        assert blocker.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
        blocker.rollback()


@pytest.mark.parametrize("stream", [False, True])
def test_reconstructed_native_no_tools_shape_preserves_final_bytes(domain, stream):
    import hashlib

    from tokenpak.proxy.passthrough import _classify_route

    from .spend_guard.native_text_fixtures import BETA, native_text_body

    # The captured output bound is larger than the minimal fixture's cap.
    # Raise only this test domain's explicit cap to fit that conservative hold.
    policy_path = Path(domain[2])
    policy = json.loads(policy_path.read_text())
    for scope in ("agent", "fleet"):
        policy["tip_spend_guard"][f"rolling_caps_per_{scope}_max_tokens_total"] = (
            get_model_max_context(MODEL) + 32000
        )
    policy_path.write_text(json.dumps(policy))
    body = native_text_body(stream=stream)
    raw = json.dumps(body, indent=1).encode() + b"\n"
    native_headers = {"anthropic-beta": BETA, "X-Claude-Code-Session-Id": "session-a"}
    assert _classify_route(URL, native_headers) == "claude-code"
    assert (
        send(
            domain,
            body=body,
            raw_body=raw,
            url=URL + "?beta=true",
            headers=native_headers,
        )[0]
        == 200
    )
    assert len(domain[3]) == 1 and domain[3][0][0] == raw
    assert domain[3][0][1]["x-claude-code-session-id"] == "session-a"
    facts = settled(domain)[2]["token_observation"]["observation"]
    assert facts["token_usage_complete"] and facts["response_complete"]
    assert facts["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (facts["input_tokens"], facts["output_tokens"]) == (15, 2)
    assert facts["feature_profile_status"] == "unclassified"
    assert facts["comparison_reason_codes"] == ["feature_profile_unclassified"]
    assert facts["actual_cost_usd"] is None and facts["billing_semantics"] == "unestablished"


@pytest.mark.parametrize("change", ["context", "tools", "block", "query"])
def test_native_shape_mutations_refuse_before_upstream(domain, change):
    from .spend_guard.native_text_fixtures import BETA, native_text_body

    body = native_text_body(max_tokens=16)
    url = URL + "?beta=true"
    if change == "context":
        body["context_management"]["edits"][0]["keep"] = True
    elif change == "tools":
        body["tools"] = [{"name": "synthetic", "input_schema": {"type": "object"}}]
    elif change == "block":
        body["messages"][0]["content"] = [{"type": "image", "source": {}}]
    else:
        url += "&beta=true"
    assert (
        send(
            domain,
            body=body,
            url=url,
            headers={"anthropic-beta": BETA, "X-Claude-Code-Session-Id": "session-a"},
        )[0]
        >= 400
    )
    assert domain[3] == []


def test_native_no_tools_shape_cannot_expand_the_token_cap(domain):
    from .spend_guard.native_text_fixtures import BETA, native_text_body

    assert (
        send(
            domain,
            body=native_text_body(),
            url=URL + "?beta=true",
            headers={"anthropic-beta": BETA, "X-Claude-Code-Session-Id": "session-a"},
        )[0]
        == 402
    )
    assert domain[3] == []


def test_repeated_local_native_refusals_do_not_mark_provider_unhealthy(domain):
    from .spend_guard.native_text_fixtures import BETA, native_text_body

    body = native_text_body(max_tokens=16)
    body["context_management"] = None
    native_headers = {"anthropic-beta": BETA, "X-Claude-Code-Session-Id": "session-a"}
    registry = breaker_module.get_circuit_breaker_registry()
    for _ in range(6):
        status, raw = send(domain, body=body, url=URL + "?beta=true", headers=native_headers)
        assert status == 402
        assert json.loads(raw)["error"]["failure_kind"] == "spend_guard_internal_error"
        assert domain[3] == []
        health = registry.all_statuses()["anthropic"]
        assert health["total_failures"] == 0 and health["state"] == "closed"
    assert (
        send(
            domain,
            body=native_text_body(max_tokens=16),
            url=URL + "?beta=true",
            headers=native_headers,
        )[0]
        == 200
    )
    assert len(domain[3]) == 1
    assert settled(domain)[2]["token_coverage_complete"]


def test_actual_provider_failure_still_counts_for_token_requests(domain):
    domain[6]["status"] = 503
    assert send(domain)[0] == 503
    assert len(domain[3]) == 1
    health = breaker_module.get_circuit_breaker_registry().all_statuses()["anthropic"]
    assert health["total_failures"] == 1 and health["total_successes"] == 0


@pytest.mark.parametrize("refusal", ["invalid_shape", "cap"])
def test_local_refusal_releases_own_half_open_probe_for_next_request(domain, monkeypatch, refusal):
    from .spend_guard.native_text_fixtures import BETA, native_text_body

    registry = breaker_module.CircuitBreakerRegistry(
        breaker_module.CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0)
    )
    monkeypatch.setattr(breaker_module, "_registry", registry)
    registry.record_failure("anthropic")  # Synthetic health setup; no provider request.
    body = native_text_body(max_tokens=32000 if refusal == "cap" else 16)
    if refusal == "invalid_shape":
        body["context_management"] = None
    headers = {"anthropic-beta": BETA, "X-Claude-Code-Session-Id": "session-a"}
    assert send(domain, body=body, url=URL + "?beta=true", headers=headers)[0] == 402
    assert domain[3] == []
    health = registry.all_statuses()["anthropic"]
    assert health["state"] == "half_open" and health["total_failures"] == 1
    assert (
        send(domain, body=native_text_body(max_tokens=16), url=URL + "?beta=true", headers=headers)[
            0
        ]
        == 200
    )
    assert len(domain[3]) == 1 and settled(domain)[2]["token_coverage_complete"]
    health = registry.all_statuses()["anthropic"]
    assert health["state"] == "closed"
    assert (health["total_failures"], health["total_successes"]) == (1, 1)


def test_client_disconnect_after_attempt_keeps_hold_and_releases_only_probe(domain, monkeypatch):
    from tokenpak.proxy.spend_guard.request_accounting import RequestAccounting

    from .spend_guard.native_text_fixtures import BETA, native_text_body

    registry = breaker_module.CircuitBreakerRegistry(
        breaker_module.CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0)
    )
    monkeypatch.setattr(breaker_module, "_registry", registry)
    registry.record_failure("anthropic")  # Synthetic health setup only.
    finished, terminal = threading.Event(), threading.Event()
    original_finish = RequestAccounting.finish

    def finish(accounting):
        try:
            original_finish(accounting)
        finally:
            finished.set()

    def log_request(**kwargs):
        if kwargs.get("extra", {}).get("outcome") == "client_disconnect":
            terminal.set()

    monkeypatch.setattr(RequestAccounting, "finish", finish)
    monkeypatch.setattr(server_module, "log_request", log_request)
    domain[5].clear()
    raw_body = json.dumps(native_text_body(stream=False, max_tokens=16)).encode()
    request = (
        f"POST {URL}?beta=true HTTP/1.1\r\n"
        "Host: api.anthropic.com\r\n"
        "Content-Type: application/json\r\n"
        "Authorization: Bearer synthetic-private\r\n"
        "X-TokenPak-Session: session-a\r\n"
        "X-TokenPak-Agent: agent-a\r\n"
        "X-Claude-Code-Session-Id: session-a\r\n"
        f"anthropic-beta: {BETA}\r\n"
        f"Content-Length: {len(raw_body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + raw_body
    with socket.create_connection(("127.0.0.1", domain[0].port), timeout=10) as client:
        client.sendall(request)
        assert domain[4].wait(8), "synthetic upstream was not attempted"
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    domain[5].set()
    assert terminal.wait(10) and finished.wait(10)
    assert len(domain[3]) == 1
    health = registry.all_statuses()["anthropic"]
    assert (health["total_failures"], health["total_successes"]) == (1, 0)
    assert health["state"] == "half_open"
    owner = object()
    assert registry.allow_request("anthropic", probe_owner=owner)
    assert registry.release_probe("anthropic", owner)
    with sqlite3.connect(domain[1]) as conn:
        assert conn.execute(
            "SELECT attempts, state, ended_at IS NOT NULL FROM budget_guard_coverage"
        ).fetchone() == (1, "awaiting_usage", 1)
        assert conn.execute(
            "SELECT status, actual_cost_usd FROM budget_reservations"
        ).fetchone() == ("active", None)


def test_cancel_pending_token_request_does_not_mark_provider_failure(domain):
    from tokenpak.proxy.spend_guard.pending import PendingStore

    from .spend_guard.native_text_fixtures import BETA, native_text_body

    body = native_text_body(max_tokens=16)
    pending = PendingStore(str(domain[1]))
    pending.store(
        session_id="session-a",
        body=json.dumps(body).encode(),
        headers={},
        target_url=URL,
        provider="anthropic",
        model=MODEL,
        projected_tokens=16,
        projected_cost_usd=0,
    )
    registry = breaker_module.get_circuit_breaker_registry()
    registry.get_state("anthropic")
    body["messages"][0]["content"][0]["text"] = "[TIP: cancel] synthetic work"
    assert (
        send(
            domain,
            body=body,
            url=URL + "?beta=true",
            headers={"anthropic-beta": BETA, "X-Claude-Code-Session-Id": "session-a"},
        )[0]
        == 200
    )
    assert pending.get_by_session("session-a") is None and domain[3] == []
    assert registry.all_statuses()["anthropic"]["total_failures"] == 0
    with sqlite3.connect(domain[1]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
