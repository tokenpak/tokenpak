"""Actual proxy entrypoints with confined HTTPX synthetic upstream transport."""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy import server as server_module
from tokenpak.proxy.spend_guard._context_window import get_model_max_context

pytestmark = pytest.mark.needs_proxy
MODEL = "claude-sonnet-4-6"
URL = "https://api.anthropic.com/v1/messages"


@pytest.fixture
def domain(tmp_path, monkeypatch):
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
        assert str(request.url) == URL
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


def send(domain, *, stream=False, headers=None, body=None, url=URL):
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
        conn.request("POST", url, json.dumps(request).encode(), combined)
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
