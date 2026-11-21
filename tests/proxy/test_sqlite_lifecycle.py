"""SQLite connections opened on proxy request and background paths close at once.

A ``sqlite3.Connection`` that is dropped without ``close()`` sits in a
reference cycle (its statement cache) and keeps its file descriptor until the
cyclic garbage collector runs. Each test here disables the collector and
counts this process's open descriptors on the test database, so a connection
left for the collector shows up as a leaked descriptor.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="counts descriptors via /proc/self/fd"
)


def _open_fds(path: Path) -> int:
    target = str(path.resolve())
    count = 0
    for name in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{name}") == target:
                count += 1
        except OSError:
            pass
    return count


def _run_threads(n: int, target) -> None:
    threads = [threading.Thread(target=target) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


@pytest.fixture()
def no_gc():
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()


def _requests_db(path: Path) -> Path:
    from tokenpak.proxy.monitor import Monitor

    Monitor(str(path))
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO requests (timestamp, model, status_code, session_id) "
            "VALUES ('2026-10-01T00:00:00', 'claude-opus-4-8', 200, 's1')"
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_session_filter_closes_its_connections(tmp_path, no_gc):
    from tokenpak.dashboard.session_filter import FilterParams, SessionFilter

    db = _requests_db(tmp_path / "monitor.db")
    sf = SessionFilter(db_path=db)
    for _ in range(20):
        sf.query(FilterParams())
        sf.distinct_models()
    assert _open_fds(db) == 0


def test_cache_invalidator_write_closes_when_table_is_missing(tmp_path, no_gc):
    from tokenpak.proxy.cache_invalidator import (
        CacheInvalidatorEvent,
        _write_cache_invalidator_events,
    )

    db = tmp_path / "bare.db"
    sqlite3.connect(str(db)).close()
    events = [CacheInvalidatorEvent("tools_changed", "a", "b")]
    for _ in range(10):
        _write_cache_invalidator_events(str(db), None, "s1", events)
    assert _open_fds(db) == 0


def test_metrics_dashboard_style_reads_close_on_error(tmp_path, no_gc):
    from tokenpak.proxy.cache_stats import _get_cache_stats_by_window

    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()  # no requests table: the query raises
    for _ in range(10):
        stats = _get_cache_stats_by_window(24, str(db))
        assert "error" in stats
    assert _open_fds(db) == 0


def test_monitor_db_resolver_probe_closes(tmp_path, no_gc):
    from tokenpak import _paths

    db = _requests_db(tmp_path / "monitor.db")
    for _ in range(10):
        assert _paths._is_valid_monitor_db(db)
    assert _open_fds(db) == 0
    # A candidate that is not a database fails the probe's query; the probe
    # must still close what it opened.
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a sqlite database " * 16)
    for _ in range(10):
        assert not _paths._is_valid_monitor_db(junk)
    assert _open_fds(junk) == 0


def test_vault_sqlite_search_closes_its_connection(tmp_path, no_gc):
    from tokenpak.vault.sqlite_backend import SQLiteRetrievalBackend

    backend = SQLiteRetrievalBackend(str(tmp_path))
    conn = sqlite3.connect(str(backend.db_path))
    try:
        backend._ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()
    backend._initialized = True
    backend._block_count = 1  # search returns early on an empty index
    for _ in range(10):
        backend.search("proxy ledger stream", top_k=3)
    assert _open_fds(backend.db_path) == 0


@pytest.mark.parametrize(
    "factory",
    [
        "tokenpak.telemetry.storage:TelemetryDB",
        "tokenpak.telemetry.storage_base:TelemetryDBBase",
    ],
)
def test_telemetry_db_thread_connections_close_when_threads_exit(tmp_path, no_gc, factory):
    module_name, cls_name = factory.split(":")
    module = __import__(module_name, fromlist=[cls_name])
    db_path = tmp_path / "telemetry.db"
    db = getattr(module, cls_name)(db_path)
    try:
        baseline = _open_fds(db_path)
        counts = []
        for _ in range(3):
            _run_threads(12, lambda: db._conn.execute("SELECT 1").fetchone())
            # Only the keeper connection remains; finished threads hold nothing.
            assert len(db._all_conns) == 1
            counts.append(_open_fds(db_path))
        # While the keeper holds its WAL lock SQLite parks closed descriptors
        # for reuse, so the count is bounded by one round's concurrency and
        # never by the number of threads that have come and gone.
        assert max(counts) <= baseline + 12, counts
    finally:
        db.close()


@pytest.mark.parametrize(
    "factory",
    [
        "tokenpak.telemetry.budget:BudgetTracker",
        "tokenpak.telemetry.cost_tracker:CostTracker",
        "tokenpak.telemetry.proxy_storage:TelemetryStorage",
        "tokenpak.telemetry.replay:ReplayStore",
    ],
)
def test_per_thread_caches_close_when_threads_exit(tmp_path, no_gc, factory):
    module_name, cls_name = factory.split(":")
    module = __import__(module_name, fromlist=[cls_name])
    db_path = tmp_path / "store.db"
    store = getattr(module, cls_name)(db_path=str(db_path))
    opened: list[sqlite3.Connection] = []
    lock = threading.Lock()

    def touch() -> None:
        conn = store._conn()
        conn.execute("SELECT 1").fetchone()
        with lock:
            opened.append(conn)

    baseline = _open_fds(db_path)
    counts = []
    for _ in range(3):
        _run_threads(12, touch)
        counts.append(_open_fds(db_path))
    # Bounded by one round's concurrency, not by the 36 threads that ran, and
    # every finished thread's connection is already closed.
    assert max(counts) <= baseline + 12, counts
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_companion_stores_release_their_write_connection(tmp_path, no_gc):
    from tokenpak.companion.budget.tracker import BudgetTracker
    from tokenpak.companion.journal.store import JournalStore

    journal = JournalStore(db_path=tmp_path / "journal-test.db")
    journal.add_entry(session_id="s1", entry_type="user", content="note", metadata={})
    assert _open_fds(tmp_path / "journal-test.db") >= 1
    journal.close()
    assert _open_fds(tmp_path / "journal-test.db") == 0

    budget = BudgetTracker(db_path=tmp_path / "budget-test.db")
    budget.record(input_tokens=10, output_tokens=5, model="claude-opus-4-8", session_id="s1")
    budget.close()
    assert _open_fds(tmp_path / "budget-test.db") == 0


def test_proxy_journal_endpoint_closes_per_request_store(tmp_path, monkeypatch, no_gc):
    from tokenpak.companion.journal.store import JournalStore
    from tokenpak.proxy import app_endpoints

    db = tmp_path / "journal.db"
    monkeypatch.setattr(app_endpoints, "_get_journal_store", lambda: JournalStore(db_path=db))

    class _Handler:
        def __init__(self) -> None:
            self.status = None
            self.headers = {}

        def send_response(self, code: int) -> None:
            self.status = code

        def send_header(self, *_: object) -> None:
            pass

        def end_headers(self) -> None:
            pass

        class wfile:  # noqa: N801 - mimics BaseHTTPRequestHandler.wfile
            @staticmethod
            def write(_: bytes) -> None:
                pass

    for i in range(10):
        handler = _Handler()
        app_endpoints._handle_journal_post(
            handler, "s1", {"entry_type": "user", "content": f"entry {i}", "metadata": {}}
        )
        assert handler.status == 200
    assert _open_fds(db) == 0
