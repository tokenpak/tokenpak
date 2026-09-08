"""Real native observations must not become spend permission or ledger writes."""

from __future__ import annotations

import copy
import http.client
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.proxy._proxy_subprocess import free_port
from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy import server as server_module
from tokenpak.proxy.spend_guard import rolling_caps as caps


@pytest.fixture(autouse=True)
def isolated_accounting():
    assert monitor_module._stop_db_write_queue(timeout=3)
    caps.reset_caches_for_testing()
    yield
    assert monitor_module._stop_db_write_queue(timeout=3)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            monitor_module._DB_CONNECTION.close()
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None
    caps.reset_caches_for_testing()


def _log(monitor, ticket=None, cost=8.0):
    monitor.log(
        model="test-model",
        input_tokens=80,
        output_tokens=20,
        cost=cost,
        latency_ms=1,
        status_code=200,
        endpoint="/v1/messages",
        session_id="explicit-session",
        agent_id="test-agent",
        admission_ticket=ticket,
    )


def _snapshot(db):
    return caps._capture_rolling_snapshot("explicit-session", 3600, monitor_db_path=str(db))


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    config = tmp_path / "config.yaml"
    config.write_text("spend_guard:\n  rolling_caps_enabled: true\n")
    monkeypatch.setenv("TOKENPAK_CONFIG", str(config))
    monkeypatch.setenv("TOKENPAK_PROXY_KEY", "snapshot-test-key")
    monkeypatch.setattr("tokenpak.proxy.config.MONITOR_DB", str(db))
    instance = server_module.ProxyServer(host="127.0.0.1", port=free_port())
    instance.start(blocking=False)
    try:
        yield instance
    finally:
        instance.stop()


def _post(proxy, body=None, *, headers=None, suffix=""):
    if body is None:
        body = {"session_id": "explicit-session"}
    raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
    combined = {"Content-Type": "application/json", "X-TPK-Key": "snapshot-test-key"}
    combined.update(headers or {})
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    try:
        conn.request("POST", "/tpk/v1/sessions/guard-snapshot" + suffix, raw, headers=combined)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        conn.close()


@pytest.mark.needs_proxy
def test_serving_proxy_exposes_components_without_metering_or_spend_permission(proxy):
    caps.record_session_agent("explicit-session", "test-agent")
    _log(proxy.monitor)
    assert proxy.monitor.flush(timeout=3)
    ticket = caps.admit_pending_spend("test-agent", 3.0, 25, 7)
    before = copy.deepcopy(proxy.session)
    status, headers, body = _post(proxy)
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    assert body["session_id"] == "explicit-session"
    assert body["owner_instance_id"] == proxy._guard_snapshot_owner_id
    assert body["recorded_usage"]["fleet_cost_usd"] == 8.0
    assert body["pending_projected_usage"]["fleet_cost_usd"] == 3.0
    assert body["agent_attribution_available"] is True
    assert body["components_may_overlap"] is True
    assert body["guard_evidence_eligible"] is False
    assert "pending_coverage_unverified" in body["reason_codes"]
    assert caps.get_admitted_projection(ticket) is not None
    assert proxy.session == before
    with sqlite3.connect(proxy.monitor.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
    encoded = json.dumps(body)
    for secret in (str(proxy.monitor.db_path), "test-agent", "snapshot-test-key"):
        assert secret not in encoded


@pytest.mark.needs_proxy
@pytest.mark.parametrize(
    "body,headers,suffix,status",
    [
        ({}, {}, "", 400),
        ({"session_id": " explicit-session"}, {}, "", 400),
        ({"session_id": "explicit-session", "now": "2020-01-01"}, {}, "", 400),
        ({"session_id": "explicit-session", "db_path": "/tmp/missing.db"}, {}, "", 400),
        ({"session_id": 42}, {}, "", 400),
        (b'{"session_id":"a","session_id":"b"}', {}, "", 400),
        (b"[" * 1100 + b"]" * 1100, {}, "", 400),
        (b"\xff", {}, "", 400),
        (b"x" * 4097, {}, "", 413),
        ({"session_id": "explicit-session"}, {"X-TokenPak-Session": "other"}, "", 400),
        (
            {"session_id": "explicit-session"},
            {"X-TokenPak-Session": "explicit-session", "thread-id": "other"},
            "",
            400,
        ),
        ({"session_id": "explicit-session"}, {"Content-Type": "text/plain"}, "", 400),
        ({"session_id": "explicit-session"}, {}, "?now=2020-01-01", 400),
        ({"session_id": "explicit-session"}, {"X-TPK-Key": "wrong"}, "", 401),
        ({"session_id": "explicit-session"}, {"Origin": "https://example.invalid"}, "", 403),
        ({"session_id": "explicit-session"}, {"Origin": ""}, "", 403),
    ],
)
def test_invalid_http_input_refuses_before_accounting(
    proxy, monkeypatch, body, headers, suffix, status
):
    calls = []
    monkeypatch.setattr(caps, "_capture_rolling_snapshot", lambda *a, **k: calls.append(True))
    actual, response_headers, _ = _post(proxy, body, headers=headers, suffix=suffix)
    assert actual == status
    assert response_headers["Cache-Control"] == "no-store"
    assert calls == []


@pytest.mark.needs_proxy
def test_loopback_without_configured_key_is_not_authenticated(proxy, monkeypatch):
    monkeypatch.delenv("TOKENPAK_PROXY_KEY")
    assert _post(proxy)[0] == 503


@pytest.mark.needs_proxy
def test_malformed_effective_config_is_unchanged_and_unavailable(proxy, monkeypatch, tmp_path):
    config = tmp_path / "invalid.yaml"
    raw = b"spend_guard: [\n"
    config.write_bytes(raw)
    monkeypatch.setenv("TOKENPAK_CONFIG", str(config))
    status, _, body = _post(proxy)
    assert status == 503
    assert body == {"error": "snapshot_resolution_unavailable"}
    assert config.read_bytes() == raw


def test_fresh_read_bypasses_cache_without_mutating_maps(tmp_path):
    db = tmp_path / "monitor.db"
    monitor = monitor_module.Monitor(db)
    caps.record_session_agent("explicit-session", "test-agent")
    _log(monitor)
    assert monitor.flush(timeout=3)
    assert (
        caps.compute_rolling_usage("test-agent", 3600, monitor_db_path=str(db))["fleet_cost_usd"]
        == 8.0
    )
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE requests SET estimated_cost = 12")
    ticket = caps.admit_pending_spend("test-agent", 3.0, 25, 7)
    caps._INFLIGHT[ticket] = ("test-agent", 3.0, 25, 7, time.time() - 700)
    before = copy.deepcopy((caps._INFLIGHT, caps._SESSION_AGENT, caps._USAGE_CACHE))
    result = _snapshot(db)
    assert result["recorded_usage"]["fleet_cost_usd"] == 12
    assert result["pending_projected_usage"]["fleet_cost_usd"] == 0
    assert result["expired_pending_count"] == 1
    assert (caps._INFLIGHT, caps._SESSION_AGENT, caps._USAGE_CACHE) == before


@pytest.mark.parametrize("kind", ["missing", "corrupt", "null", "negative", "infinity"])
def test_unmeasurable_ledger_never_becomes_zero(tmp_path, kind):
    db = tmp_path / "monitor.db"
    if kind == "corrupt":
        db.write_bytes(b"not SQLite")
    elif kind not in ("missing", "corrupt"):
        monitor = monitor_module.Monitor(db)
        _log(monitor)
        assert monitor.flush(timeout=3)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE requests SET estimated_cost = ?",
                ({"null": None, "negative": -1, "infinity": float("inf")}[kind],),
            )
    result = _snapshot(db)
    assert result["recorded_usage"] is None
    assert result["guard_evidence_eligible"] is False
    if kind == "missing":
        assert not db.exists()


def test_restart_and_changed_attribution_are_unavailable(tmp_path):
    db = tmp_path / "monitor.db"
    monitor = monitor_module.Monitor(db)
    _log(monitor)
    assert monitor.flush(timeout=3)
    for mapped_agent in (None, "different-agent"):
        if mapped_agent:
            caps.record_session_agent("explicit-session", mapped_agent)
        result = _snapshot(db)
        assert result["agent_attribution_available"] is False
        assert result["recorded_usage"]["agent_cost_usd"] is None
        assert result["pending_projected_usage"]["agent_cost_usd"] is None


@pytest.mark.parametrize(
    "bad_entry",
    [
        ("test-agent", -1.0, 1, 0, 0),
        ("test-agent", float("nan"), 1, 0, 0),
        ("test-agent", 1.0, -1, 0, 0),
        ("test-agent", 1.0, 1, 0, float("inf")),
    ],
)
def test_invalid_pending_entry_cannot_be_hidden_by_other_totals(tmp_path, bad_entry):
    db = tmp_path / "monitor.db"
    caps.admit_pending_spend("test-agent", 100.0, 100, 100)
    caps._INFLIGHT["invalid-test-ticket"] = (*bad_entry[:4], bad_entry[4] or time.time())
    result = _snapshot(db)
    assert result["pending_projected_usage"] is None
    assert "pending_values_invalid" in result["reason_codes"]


def test_snapshot_serializes_with_settlement_and_direct_admission(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    monitor = monitor_module.Monitor(db)
    _log(monitor)
    assert monitor.flush(timeout=3)
    ticket = caps.admit_pending_spend("test-agent", 3.0, 25, 0)
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    query = caps._query_recorded_usage

    def delayed_query(*args):
        entered.set()
        assert release.wait(3)
        return query(*args)

    def change():
        attempted.set()
        caps.settle_pending_spend(ticket)
        caps.admit_pending_spend("test-agent", 9.0, 25, 0)

    monkeypatch.setattr(caps, "_query_recorded_usage", delayed_query)
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshot = pool.submit(_snapshot, db)
        try:
            assert entered.wait(3)
            changed = pool.submit(change)
            assert attempted.wait(3)
            assert not changed.done()
        finally:
            release.set()
        assert snapshot.result(timeout=3)["pending_projected_usage"]["fleet_cost_usd"] == 3
        changed.result(timeout=3)
    assert _snapshot(db)["pending_projected_usage"]["fleet_cost_usd"] == 9


def test_commit_before_notification_is_explicitly_overlapping(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    monitor = monitor_module.Monitor(db)
    ticket = caps.admit_pending_spend("test-agent", 7.0, 25, 0)
    entered, release = threading.Event(), threading.Event()
    original = monitor_module._notify_spend_guard_commit

    def delayed_notification(admission_ticket):
        entered.set()
        assert release.wait(3)
        original(admission_ticket)

    monkeypatch.setattr(monitor_module, "_notify_spend_guard_commit", delayed_notification)
    try:
        _log(monitor, ticket, cost=8.0)
        assert entered.wait(3)
        observed = _snapshot(db)
        assert observed["recorded_usage"]["fleet_cost_usd"] == 8
        assert observed["pending_projected_usage"]["fleet_cost_usd"] == 7
        assert observed["components_may_overlap"] is True
        assert observed["guard_evidence_eligible"] is False
    finally:
        release.set()
    assert monitor.flush(timeout=3)
    assert _snapshot(db)["pending_projected_usage"]["fleet_cost_usd"] == 0


def test_snapshot_uses_only_read_sql_and_preserves_wal_commits(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    monitor = monitor_module.Monitor(db)
    _log(monitor)
    assert monitor.flush(timeout=3)
    connect = sqlite3.connect
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,
    }
    attempts = []

    def observed_connect(path, **kwargs):
        assert path.endswith("?mode=ro") and kwargs["uri"] is True
        conn = connect(path, **kwargs)

        def authorize(action, *args):
            if action not in allowed:
                attempts.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(caps.sqlite3, "connect", observed_connect)
    assert _snapshot(db)["recorded_usage"]["fleet_cost_usd"] == 8
    assert attempts == []
