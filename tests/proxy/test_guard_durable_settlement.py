"""Admitted spend remains visible until its real telemetry row commits."""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.proxy._proxy_subprocess import free_port
from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy import server as server_module
from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.spend_guard import rolling_caps as caps
from tokenpak.proxy.spend_guard.contracts import GuardOutcome


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


def _log(monitor, ticket=None, *, cost=8.0):
    monitor.log(
        model="test-model",
        input_tokens=80,
        output_tokens=20,
        cost=cost,
        latency_ms=1,
        status_code=200,
        endpoint="/v1/messages",
        session_id="test-session",
        admission_ticket=ticket,
    )


def _competing_admission(db):
    return caps.check_rolling_caps_and_admit(
        "test-agent",
        3.0,
        1,
        0,
        0,
        caps.RollingCapsConfig(per_fleet_max_cost_usd=10.0),
        monitor_db_path=str(db),
    )


def _count_rows(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]


@pytest.mark.needs_proxy
def test_proxy_preserves_admission_until_delayed_monitor_commit(tmp_path, monkeypatch):
    """Exercise the real forwarding, queue, commit and reservation transfer."""
    request = json.dumps(
        {
            "model": "claude-sonnet-4-6",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "synthetic settlement check"}],
        }
    ).encode()
    reply = b'{"content":[],"usage":{"input_tokens":8,"output_tokens":2}}'
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    db = tmp_path / "monitor.db"
    monkeypatch.setattr("tokenpak.proxy.config.MONITOR_DB", str(db))
    monkeypatch.setattr(server_module, "INTERCEPT_HOSTS", {"127.0.0.1"})
    monkeypatch.setenv("TOKENPAK_PASSTHROUGH", "1")
    ticket = caps.admit_pending_spend("test-agent", 8.0, 100, 0)
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard.evaluate",
        lambda body, *_args: GuardOutcome(kind="forward", body=body, admission_ticket=ticket),
    )
    entered, release = threading.Event(), threading.Event()
    original_write = monitor_module._write_row

    def delayed_write(path, params):
        entered.set()
        assert release.wait(5), "test did not release the delayed writer"
        original_write(path, params)

    monkeypatch.setattr(monitor_module, "_write_row", delayed_write)
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    proxy = server_module.ProxyServer(host="127.0.0.1", port=free_port())
    proxy.start(blocking=False)
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    try:
        conn.request(
            "POST",
            f"http://127.0.0.1:{upstream.server_port}/v1/messages",
            body=request,
            headers={"Content-Type": "application/json", "x-api-key": "test-only"},
        )
        response = conn.getresponse()
        assert response.status == 200
        assert response.read() == reply
        assert received == [request]
        assert entered.wait(3)
        assert _count_rows(db) == 0
        assert caps.get_admitted_projection(ticket) is not None
        breach, extra_ticket = _competing_admission(db)
        assert breach is not None and extra_ticket is None
        release.set()
        assert proxy.monitor.flush(timeout=3)
        assert _count_rows(db) == 1
        assert caps.get_admitted_projection(ticket) is None
    finally:
        release.set()
        conn.close()
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        worker.join(timeout=3)


@pytest.mark.parametrize("synchronous", [False, True])
def test_committed_actual_usage_replaces_reservation_and_stale_cache(tmp_path, synchronous):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db)
    if synchronous:
        assert monitor.stop(timeout=3)
    assert (
        caps.compute_rolling_usage("test-agent", 3600, monitor_db_path=str(db))["fleet_cost_usd"]
        == 0
    )
    ticket = caps.admit_pending_spend("test-agent", 8.0, 100, 0)
    _log(monitor, ticket)
    assert monitor.flush(timeout=3)
    assert _count_rows(db) == 1
    assert caps.get_admitted_projection(ticket) is None
    breach, extra_ticket = _competing_admission(db)
    assert breach is not None and extra_ticket is None
    assert breach.used == 8.0  # actual only, without a second pending charge


@pytest.mark.parametrize("synchronous", [False, True])
def test_failed_row_preserves_reservation(tmp_path, monkeypatch, synchronous):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db)
    if synchronous:
        assert monitor.stop(timeout=3)
    ticket = caps.admit_pending_spend("test-agent", 8.0, 100, 0)

    def fail_write(*_args):
        raise sqlite3.OperationalError("injected durable write failure")

    monkeypatch.setattr(monitor_module, "_write_row", fail_write)
    drops = monitor_module.get_dropped_row_count()
    _log(monitor, ticket)
    assert monitor.flush(timeout=3)
    assert _count_rows(db) == 0
    assert caps.get_admitted_projection(ticket) is not None
    assert monitor_module.get_dropped_row_count() == drops + 1
    breach, extra_ticket = _competing_admission(db)
    assert breach is not None and extra_ticket is None


def test_unreserved_commit_also_invalidates_recorded_usage(tmp_path):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db)
    assert (
        caps.compute_rolling_usage("test-agent", 3600, monitor_db_path=str(db))["fleet_cost_usd"]
        == 0
    )
    _log(monitor)
    assert monitor.flush(timeout=3)
    breach, extra_ticket = _competing_admission(db)
    assert breach is not None and extra_ticket is None
    assert breach.used == 8.0


def test_commit_notification_failure_is_not_a_dropped_or_retried_row(tmp_path, monkeypatch, capsys):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db)
    ticket = caps.admit_pending_spend("test-agent", 8.0, 100, 0)
    drops = monitor_module.get_dropped_row_count()

    def fail_notification(_ticket):
        raise RuntimeError("injected notification failure")

    monkeypatch.setattr(caps, "recorded_spend_committed", fail_notification)
    _log(monitor, ticket)
    assert monitor.flush(timeout=3)
    assert _count_rows(db) == 1
    assert caps.get_admitted_projection(ticket) is not None
    assert monitor_module.get_dropped_row_count() == drops
    assert "committed" in capsys.readouterr().err


def test_stale_concurrent_reader_cannot_repopulate_invalidated_cache(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db)
    entered, release = threading.Event(), threading.Event()
    original_connect = sqlite3.connect

    class PausedRead:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, *args):
            return self.conn.execute(*args)

        def close(self):
            entered.set()  # the SELECT already observed the old empty ledger
            assert release.wait(5)
            self.conn.close()

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        return (
            PausedRead(conn) if threading.current_thread().name.startswith("old-reader") else conn
        )

    monkeypatch.setattr(caps.sqlite3, "connect", connect)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-reader") as executor:
        future = executor.submit(caps.compute_rolling_usage, "", 3600, monitor_db_path=str(db))
        try:
            assert entered.wait(3)
            ticket = caps.admit_pending_spend("test-agent", 8.0, 100, 0)
            _log(monitor, ticket)
            assert monitor.flush(timeout=3)
            assert caps.get_admitted_projection(ticket) is None
        finally:
            release.set()
        assert future.result(timeout=3)["fleet_cost_usd"] == 0
    # Query the same cache key used by the deliberately stale reader.
    assert caps.compute_rolling_usage("", 3600, monitor_db_path=str(db))["fleet_cost_usd"] == 8


def test_direct_cap_check_cannot_mix_old_cache_with_settled_pending(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db)
    ticket = caps.admit_pending_spend("test-agent", 8.0, 100, 0)
    read_pending, release = threading.Event(), threading.Event()
    notifying = threading.Event()
    original_pending = caps._pending_spend_totals
    original_notify = caps.recorded_spend_committed

    def paused_pending(agent):
        read_pending.set()  # the cap check already read the old ledger
        assert release.wait(5)
        return original_pending(agent)

    def notify(committed_ticket):
        notifying.set()  # the real row committed; settlement may now contend
        original_notify(committed_ticket)

    monkeypatch.setattr(caps, "_pending_spend_totals", paused_pending)
    monkeypatch.setattr(caps, "recorded_spend_committed", notify)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            caps.check_rolling_caps,
            "test-agent",
            3.0,
            1,
            0,
            0,
            caps.RollingCapsConfig(per_fleet_max_cost_usd=10.0),
            monitor_db_path=str(db),
        )
        try:
            assert read_pending.wait(3)
            _log(monitor, ticket)
            assert notifying.wait(3)
            assert _count_rows(db) == 1
            assert caps.get_admitted_projection(ticket) is not None
        finally:
            release.set()
        assert future.result(timeout=3).used == 8.0
    assert monitor.flush(timeout=3)
    assert caps.get_admitted_projection(ticket) is None
