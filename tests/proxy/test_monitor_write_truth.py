"""Write durability, alert dedupe/time-basis, and savings-truth tests for
``tokenpak.proxy.monitor``.

Covers:
  A. Write path: bounded retry on transient lock errors, dropped-row
     counter, flush/stop drain, fallback routed through the guarded
     connection, migration idiom (duplicate-column tolerated, lock raised).
  B. Budget alerts: UNIQUE(local day, period) dedupe under concurrency,
     local-time query basis for locally-stamped rows.
  C. Savings report: registry-rate formula agreement with the shared cache
     estimator, cache_origin filtering (product vs client vs unknown).
"""

from __future__ import annotations

import inspect
import sqlite3
import threading
import time as _time
from datetime import datetime, timezone

import pytest

import tokenpak.proxy.monitor as monitor_module
from tokenpak.proxy.monitor import Monitor

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_writer_state():
    """Isolate module-level writer state between tests."""
    yield
    monitor_module._stop_db_write_queue(timeout=2.0)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            try:
                monitor_module._DB_CONNECTION.close()
            except Exception:
                pass
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None


def _row_count(db_path, table="requests"):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _insert_request(
    db_path,
    *,
    timestamp=None,
    model="claude-sonnet-4-6",
    estimated_cost=0.0,
    compressed_tokens=0,
    cache_read_tokens=0,
    cache_origin="proxy",
):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, "
            "estimated_cost, compressed_tokens, cache_read_tokens, cache_origin) "
            "VALUES (?, ?, 0, 0, ?, ?, ?, ?)",
            (
                timestamp or datetime.now().isoformat(),
                model,
                estimated_cost,
                compressed_tokens,
                cache_read_tokens,
                cache_origin,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _log_minimal(mon, **kwargs):
    defaults = dict(
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
        cost=0.0,
        latency_ms=1,
        status_code=200,
        endpoint="test",
    )
    defaults.update(kwargs)
    mon.log(**defaults)


class _FlakyConnection:
    """Wraps a real connection; fails the first N execute() calls with a
    transient lock error."""

    def __init__(self, real, fail_times, error="database is locked"):
        self._real = real
        self.fails_left = fail_times
        self.error = error
        self.attempts = 0

    def execute(self, *args, **kwargs):
        self.attempts += 1
        if self.fails_left > 0:
            self.fails_left -= 1
            raise sqlite3.OperationalError(self.error)
        return self._real.execute(*args, **kwargs)

    def commit(self):
        return self._real.commit()

    def close(self):
        return self._real.close()


# ---------------------------------------------------------------------------
# A1 — writer retry + dropped-row counter
# ---------------------------------------------------------------------------


def test_write_row_retries_transient_lock_then_succeeds(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    assert mon.flush(timeout=5.0)

    real = sqlite3.connect(str(db), check_same_thread=False)
    flaky = _FlakyConnection(real, fail_times=2)
    monkeypatch.setattr(monitor_module, "_get_db_connection", lambda p: flaky)
    monkeypatch.setattr(monitor_module, "_DB_WRITE_RETRY_BACKOFF_S", 0.001)

    params = (
        datetime.now().isoformat(),
        "claude-sonnet-4-6",
        "chat",
        1,
        1,
        0.0,
        1,
        200,
        "test",
        "",
        0,
        0,
        0,
        "",
        0,
        0,
        0,
        "proxy",
        "",
        0,
        0,
        None,
        "",
        "",
        "",
        "",
        "",
    )
    before = monitor_module.get_dropped_row_count()
    monitor_module._write_row(str(db), params)

    assert flaky.attempts == 3  # 2 transient failures + 1 success
    assert monitor_module.get_dropped_row_count() == before
    real.close()
    assert _row_count(db) == 1


def test_write_row_raises_after_retries_exhausted(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    Monitor(db_path=str(db))

    class _AlwaysLocked:
        attempts = 0

        def execute(self, *a, **k):
            self.attempts += 1
            raise sqlite3.OperationalError("database is locked")

    locked = _AlwaysLocked()
    monkeypatch.setattr(monitor_module, "_get_db_connection", lambda p: locked)
    monkeypatch.setattr(monitor_module, "_DB_WRITE_RETRY_BACKOFF_S", 0.001)

    with pytest.raises(sqlite3.OperationalError):
        monitor_module._write_row(str(db), ("x",) * len(monitor_module._REQUEST_INSERT_COLUMNS))
    assert locked.attempts == monitor_module._DB_WRITE_RETRY_ATTEMPTS


def test_write_row_does_not_retry_non_transient_errors(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    Monitor(db_path=str(db))

    class _Broken:
        attempts = 0

        def execute(self, *a, **k):
            self.attempts += 1
            raise sqlite3.OperationalError("no such column: nonexistent")

    broken = _Broken()
    monkeypatch.setattr(monitor_module, "_get_db_connection", lambda p: broken)

    with pytest.raises(sqlite3.OperationalError):
        monitor_module._write_row(str(db), ("x",) * len(monitor_module._REQUEST_INSERT_COLUMNS))
    assert broken.attempts == 1


def test_async_writer_counts_dropped_rows(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    assert mon.flush(timeout=5.0)

    def _raise(_path):
        raise sqlite3.OperationalError("no such table: requests")

    before = mon.dropped_row_count()
    monkeypatch.setattr(monitor_module, "_get_db_connection", _raise)
    monkeypatch.setattr(monitor_module, "_DB_WRITE_RETRY_BACKOFF_S", 0.001)
    _log_minimal(mon)
    assert mon.flush(timeout=5.0)
    assert mon.dropped_row_count() == before + 1


# ---------------------------------------------------------------------------
# A2 — flush / stop drain the queue
# ---------------------------------------------------------------------------


def test_stop_drains_queued_rows_and_stops_worker(tmp_path):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    for _ in range(25):
        _log_minimal(mon)

    assert mon.stop(timeout=10.0) is True
    assert _row_count(db) == 25
    assert monitor_module._DB_WRITE_QUEUE is None
    assert monitor_module._DB_BACKGROUND_THREAD is None


def test_flush_waits_for_queued_rows(tmp_path):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    for _ in range(10):
        _log_minimal(mon)
    assert mon.flush(timeout=10.0) is True
    assert _row_count(db) == 10
    # Writer keeps running after flush.
    assert monitor_module._DB_BACKGROUND_THREAD.is_alive()


def test_writer_routes_interleaved_rows_to_each_monitor_database(tmp_path):
    """The process-global queue must honor each work item's database path."""
    first_db = tmp_path / "first.db"
    second_db = tmp_path / "second.db"
    first = Monitor(db_path=str(first_db))
    second = Monitor(db_path=str(second_db))

    # Construct both monitors before enqueueing.  The first row opens the
    # cached writer connection for first_db; the second row must switch that
    # connection rather than silently landing in first_db as well.
    _log_minimal(first, endpoint="first")
    _log_minimal(second, endpoint="second")

    assert second.flush(timeout=10.0) is True
    assert _row_count(first_db) == 1
    assert _row_count(second_db) == 1


def test_new_monitor_restarts_worker_after_stop(tmp_path):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    mon.stop(timeout=5.0)

    mon2 = Monitor(db_path=str(db))
    _log_minimal(mon2)
    assert mon2.flush(timeout=10.0) is True
    assert _row_count(db) == 1


# ---------------------------------------------------------------------------
# A3 — queue-full/uninit fallback goes through the guarded write path
# ---------------------------------------------------------------------------


def test_log_fallback_routes_through_guarded_write(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    mon.stop(timeout=5.0)  # queue now None -> log() must take the fallback

    calls = []
    real_write_row = monitor_module._write_row

    def _spy(db_path, params):
        calls.append(db_path)
        return real_write_row(db_path, params)

    monkeypatch.setattr(monitor_module, "_write_row", _spy)
    _log_minimal(mon)

    assert len(calls) == 1
    assert _row_count(db) == 1
    # The fallback used the persistent guarded connection with WAL enabled,
    # not a fresh PRAGMA-less connection.
    with monitor_module._DB_LOCK:
        assert monitor_module._DB_CONNECTION is not None
        mode = monitor_module._DB_CONNECTION.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_log_fallback_counts_drop_instead_of_raising(tmp_path, monkeypatch):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    mon.stop(timeout=5.0)

    def _boom(db_path, params):
        raise sqlite3.OperationalError("no such table: requests")

    monkeypatch.setattr(monitor_module, "_write_row", _boom)
    before = mon.dropped_row_count()
    _log_minimal(mon)  # must not raise
    assert mon.dropped_row_count() == before + 1


# ---------------------------------------------------------------------------
# A4 — migration idiom: duplicate column tolerated, lock errors raised
# ---------------------------------------------------------------------------


def test_apply_schema_migration_tolerates_duplicate_column(tmp_path):
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, existing TEXT)")
        # Re-adding an existing column is the idempotent no-op case.
        monitor_module._apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN existing TEXT"
        )
        # A genuinely new column is applied.
        monitor_module._apply_schema_migration(conn, "ALTER TABLE requests ADD COLUMN fresh TEXT")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)")}
        assert "fresh" in cols
    finally:
        conn.close()


def test_apply_schema_migration_raises_on_locked_database():
    class _LockedConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        monitor_module._apply_schema_migration(
            _LockedConn(), "ALTER TABLE requests ADD COLUMN anything TEXT"
        )


def test_apply_schema_migration_raises_on_other_operational_errors():
    class _BrokenConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no such table: requests")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        monitor_module._apply_schema_migration(
            _BrokenConn(), "ALTER TABLE requests ADD COLUMN anything TEXT"
        )


# ---------------------------------------------------------------------------
# B6 — budget alert dedupe (unique key + INSERT OR IGNORE)
# ---------------------------------------------------------------------------


def test_budget_alert_single_row_under_concurrent_triggers(tmp_path):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))

    n_threads = 2
    barrier = threading.Barrier(n_threads)
    errors = []

    def _fire():
        try:
            barrier.wait(timeout=5)
            mon._check_budget_alert(current_cost=100.0, _daily_limit=10.0, _threshold_pct=80.0)
        except Exception as exc:  # pragma: no cover - failure diagnostics
            errors.append(exc)

    threads = [threading.Thread(target=_fire) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert _row_count(db, "budget_alerts") == 1

    # A later same-day trigger is also deduped.
    mon._check_budget_alert(current_cost=100.0, _daily_limit=10.0, _threshold_pct=80.0)
    assert _row_count(db, "budget_alerts") == 1


def test_budget_alert_legacy_duplicates_collapsed_and_index_created(tmp_path):
    db = tmp_path / "monitor.db"
    # Seed a legacy DB with duplicate same-day alerts (old check-then-insert race).
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE budget_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            agent_id TEXT DEFAULT "",
            period TEXT DEFAULT "daily",
            budget_usd REAL,
            spent_usd REAL,
            pct_used REAL,
            triggered INTEGER DEFAULT 1
        )
        """
    )
    stamp = datetime.now().isoformat()
    for _ in range(3):
        conn.execute(
            "INSERT INTO budget_alerts (timestamp, period, budget_usd, spent_usd, pct_used, triggered) "
            "VALUES (?, 'daily', 10.0, 12.0, 120.0, 1)",
            (stamp,),
        )
    conn.commit()
    conn.close()

    Monitor(db_path=str(db))  # migration collapses duplicates + adds index

    assert _row_count(db, "budget_alerts") == 1
    conn = sqlite3.connect(str(db))
    try:
        idx = {r[1] for r in conn.execute("PRAGMA index_list(budget_alerts)")}
    finally:
        conn.close()
    assert "idx_budget_alerts_day_period" in idx


# ---------------------------------------------------------------------------
# B7 — local-time query basis for locally-stamped rows
# ---------------------------------------------------------------------------


@pytest.fixture
def _restore_tz(monkeypatch):
    """Restore process timezone state after TZ manipulation."""
    yield
    monkeypatch.undo()
    _time.tzset()


def _set_tz(monkeypatch, tz_name):
    monkeypatch.setenv("TZ", tz_name)
    _time.tzset()


def test_get_stats_windows_on_localtime(tmp_path, monkeypatch, _restore_tz):
    # Local clock 12 hours BEHIND UTC: a row 20 local-hours old is inside a
    # 24h local window but outside a 24h window anchored at UTC 'now'.
    _set_tz(monkeypatch, "Etc/GMT+12")

    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    twenty_hours_ago_local = datetime.fromtimestamp(_time.time() - 20 * 3600)
    _insert_request(db, timestamp=twenty_hours_ago_local.isoformat(), estimated_cost=1.0)

    stats = mon.get_stats(hours=24)
    assert stats["requests"] == 1
    assert stats["total_cost"] == 1.0

    # Sanity: the OLD (UTC-based) window really would have missed this row.
    conn = sqlite3.connect(str(db))
    try:
        old_basis = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE datetime(timestamp) >= datetime('now', ?)",
            ("-24 hours",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert old_basis == 0


def test_get_stats_hours_window_excludes_same_date_older_row(tmp_path, monkeypatch, _restore_tz):
    # Regression for the mixed-separator windowing bug: rows are stamped
    # 'T'-separated (datetime.now().isoformat()) but the cutoff is space-
    # separated (datetime('now','localtime',?)). A RAW string compare made
    # 'T' > ' ' true, so every SAME-DATE row counted and get_stats(hours=1)
    # behaved like a whole-day window. Force local time to mid-afternoon so a
    # 2h-old row is the same local date, then assert it is correctly excluded
    # from a 1h window by the datetime(timestamp) normalization.
    utc_hour = datetime.now(timezone.utc).hour
    offset = 14 - utc_hour  # local = utc + offset  ->  ~14:00, safely mid-day
    if offset == 0:
        _set_tz(monkeypatch, "UTC")
    elif offset > 0:
        _set_tz(monkeypatch, f"Etc/GMT-{offset}")  # Etc/GMT-N == UTC+N
    else:
        _set_tz(monkeypatch, f"Etc/GMT+{-offset}")  # Etc/GMT+N == UTC-N

    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    two_hours_ago = datetime.fromtimestamp(_time.time() - 2 * 3600).isoformat()
    thirty_min_ago = datetime.fromtimestamp(_time.time() - 30 * 60).isoformat()
    _insert_request(db, timestamp=two_hours_ago, estimated_cost=5.0)
    _insert_request(db, timestamp=thirty_min_ago, estimated_cost=3.0)

    stats = mon.get_stats(hours=1)
    assert stats["requests"] == 1  # only the 30-min-old row is in-window
    assert stats["total_cost"] == 3.0  # the 2h-old row's 5.0 is excluded


def test_budget_alert_uses_local_day_for_spend_and_dedupe(tmp_path, monkeypatch, _restore_tz):
    # Pick an offset that guarantees the LOCAL date differs from the UTC date
    # right now, whichever hemisphere of the day we are in.
    utc_hour = datetime.now(timezone.utc).hour
    _set_tz(monkeypatch, "Etc/GMT+12" if utc_hour < 11 else "Etc/GMT-14")

    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    # Row stamped with LOCAL 'now' (exactly what Monitor.log writes).
    _insert_request(db, timestamp=datetime.now().isoformat(), estimated_cost=9.0)

    conn = sqlite3.connect(str(db))
    try:
        # Fixture sanity: local date differs from UTC date, so the OLD
        # date('now') basis reads today's spend as zero...
        old_spend = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) FROM requests "
            "WHERE date(timestamp) = date('now')"
        ).fetchone()[0]
        new_spend = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) FROM requests "
            "WHERE date(timestamp) = date('now', 'localtime')"
        ).fetchone()[0]
    finally:
        conn.close()
    assert old_spend == 0.0
    assert new_spend == 9.0

    # ...and the alert path (localtime basis) sees the spend and fires.
    mon._check_budget_alert(current_cost=0.0, _daily_limit=10.0, _threshold_pct=80.0)
    assert _row_count(db, "budget_alerts") == 1

    status = mon.get_budget_alert_status(_daily_limit=10.0, _threshold_pct=80.0)
    assert status["spent_usd"] == 9.0
    assert status["alert_triggered"] is True


# ---------------------------------------------------------------------------
# C8 — savings formula agreement with the shared registry-rate estimator
# ---------------------------------------------------------------------------


def test_savings_report_agrees_with_registry_rate_estimator(tmp_path):
    from tokenpak.models import detect_provider, get_rates
    from tokenpak.proxy.cache import estimate_cache_savings

    model = "claude-opus-4-7"  # premium rates: distinct from flat $3.00/$2.70
    compressed = 2_000_000
    cache_read = 1_000_000

    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    _insert_request(
        db,
        model=model,
        compressed_tokens=compressed,
        cache_read_tokens=cache_read,
        cache_origin="proxy",
    )

    report = mon.get_savings_report()

    expected = compressed * get_rates(model)["input"] / 1_000_000
    expected += estimate_cache_savings(detect_provider(model), cache_read, model)
    old_formula = compressed * 3.00 / 1_000_000 + cache_read * 2.70 / 1_000_000

    assert report["total_requests"] == 1
    assert report["total_tokens_saved"] == compressed + cache_read
    assert report["total_cost_saved_usd"] == pytest.approx(expected, abs=1e-3)
    assert report["savings_by_model"][model]["cost_saved_usd"] == pytest.approx(expected, abs=1e-3)
    assert len(report["savings_by_date_7d"]) == 1
    assert report["savings_by_date_7d"][0]["cost_saved_usd"] == pytest.approx(expected, abs=1e-3)
    # The report must NOT reproduce the old hardcoded flat-rate math.
    assert report["total_cost_saved_usd"] != pytest.approx(old_formula, abs=1e-3)


# ---------------------------------------------------------------------------
# C9 — cache_origin filtering: only product-placed cache reads are credited
# ---------------------------------------------------------------------------


def test_savings_report_filters_cache_origin(tmp_path):
    from tokenpak.models import detect_provider
    from tokenpak.proxy.cache import estimate_cache_savings

    model = "claude-sonnet-4-6"
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    _insert_request(db, model=model, cache_read_tokens=100_000, cache_origin="proxy")
    _insert_request(db, model=model, cache_read_tokens=200_000, cache_origin="client")
    _insert_request(db, model=model, cache_read_tokens=300_000, cache_origin="unknown")

    report = mon.get_savings_report()

    expected_product = estimate_cache_savings(detect_provider(model), 100_000, model)
    expected_client = estimate_cache_savings(detect_provider(model), 200_000, model)

    # Only the product-placed (proxy-origin) reads count as savings.
    assert report["total_tokens_saved"] == 100_000
    assert report["total_cost_saved_usd"] == pytest.approx(expected_product, abs=1e-4)
    # Client and unknown-origin reads are observed on separate lines, never credited.
    assert report["client_cache_read_tokens"] == 200_000
    assert report["client_cache_read_est_usd_not_counted"] == pytest.approx(
        expected_client, abs=1e-4
    )
    assert report["unknown_origin_cache_read_tokens"] == 300_000


def test_savings_report_empty_db_keeps_contract(tmp_path):
    db = tmp_path / "monitor.db"
    mon = Monitor(db_path=str(db))
    report = mon.get_savings_report()
    assert report["total_requests"] == 0
    assert report["total_tokens_saved"] == 0
    assert report["total_cost_saved_usd"] == 0.0
    assert report["savings_by_model"] == {}
    assert report["savings_by_date_7d"] == []


# ---------------------------------------------------------------------------
# D - stop_reason capture (response-path execution truth)
# ---------------------------------------------------------------------------


def _fetch_stop_reasons(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            row[0]
            for row in conn.execute("SELECT stop_reason FROM requests ORDER BY id").fetchall()
        ]
    finally:
        conn.close()


def _create_requests_table_for_insert(db_path):
    cols = ", ".join(f"{name} TEXT" for name in monitor_module._REQUEST_INSERT_COLUMNS)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"CREATE TABLE requests (id INTEGER PRIMARY KEY, {cols})")
        conn.commit()
    finally:
        conn.close()


def _request_insert_params(*, stop_reason=""):
    return (
        datetime.now().isoformat(),
        "claude-sonnet-4-6",
        "chat",
        10,
        5,
        0.0,
        1,
        200,
        "test",
        "",
        0,
        0,
        0,
        "",
        0,
        0,
        0,
        "proxy",
        "",
        0,
        0,
        None,
        "",
        "",
        "",
        "",
        stop_reason,
    )


def test_log_persists_stop_reason(tmp_path):
    """A refusal returned as HTTP 200 must be distinguishable from success."""
    db = tmp_path / "monitor.db"
    _create_requests_table_for_insert(db)
    monitor_module._write_row(str(db), _request_insert_params(stop_reason="end_turn"))
    monitor_module._write_row(str(db), _request_insert_params(stop_reason="refusal"))
    assert _fetch_stop_reasons(db) == ["end_turn", "refusal"]


def test_log_without_stop_reason_keeps_legacy_contract(tmp_path):
    """Legacy call sites that don't pass stop_reason keep the '' sentinel."""
    signature = inspect.signature(Monitor.log)
    assert signature.parameters["stop_reason"].default == ""

    db = tmp_path / "monitor.db"
    _create_requests_table_for_insert(db)
    monitor_module._write_row(str(db), _request_insert_params(stop_reason=""))
    monitor_module._write_row(str(db), _request_insert_params(stop_reason=""))
    assert _fetch_stop_reasons(db) == ["", ""]


def test_existing_db_gains_stop_reason_column(tmp_path):
    """Additive migration: a pre-existing requests table gains stop_reason."""
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms INTEGER,
            status_code INTEGER,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER,
            compressed_tokens INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO requests (timestamp, model, input_tokens, output_tokens,"
        " estimated_cost, status_code) VALUES (?, ?, 1, 1, 0.0, 200)",
        (datetime.now().isoformat(), "claude-sonnet-4-6"),
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(db))
    try:
        monitor_module._apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN stop_reason TEXT DEFAULT ''"
        )
        conn.commit()
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
        assert "stop_reason" in cols
        # Legacy row survives the migration and reads back the '' default.
        assert conn.execute("SELECT COALESCE(stop_reason, '') FROM requests").fetchone()[0] == ""
    finally:
        conn.close()
    # The migrated column is writable on the pre-existing table.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO requests (timestamp, model, input_tokens, output_tokens,"
            " estimated_cost, status_code, stop_reason)"
            " VALUES (?, ?, 1, 1, 0.0, 200, ?)",
            (datetime.now().isoformat(), "claude-sonnet-4-6", "max_tokens"),
        )
        conn.commit()
    finally:
        conn.close()
    assert _fetch_stop_reasons(db)[-1] == "max_tokens"
