"""tests/proxy/test_db_schema.py

CCG-02 acceptance tests: session_id column + mutation_audit table.

Coverage:
  1. Fresh DB: ensure_schema creates requests (with session_id) + mutation_audit
  2. Migration from old schema: session_id added, existing rows untouched
  3. Idempotent: calling ensure_schema twice raises no error
  4. mutation_audit insert and query round-trip
  5. Monitor._init_db on a fresh path creates mutation_audit
  6. Monitor._init_db on a legacy DB (no session_id) migrates without data loss
"""

import sqlite3
from datetime import datetime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_conn(tmp_path, name="test.db"):
    """Return an open connection to a new temp DB file (not :memory: so Monitor
    can reopen it)."""
    return sqlite3.connect(str(tmp_path / name))


def _create_legacy_requests(conn):
    """Seed a requests table that looks like the pre-CCG-02 schema (no session_id)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            model           TEXT NOT NULL,
            request_type    TEXT,
            input_tokens    INTEGER,
            output_tokens   INTEGER,
            estimated_cost  REAL,
            latency_ms      INTEGER,
            status_code     INTEGER,
            endpoint        TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO requests (timestamp, model, request_type) VALUES (?, ?, ?)",
        (datetime.now().isoformat(), "claude-sonnet-4-6", "chat"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Unit tests — db.ensure_schema in isolation
# ---------------------------------------------------------------------------


class TestEnsureSchemaFreshDB:
    def test_mutation_audit_table_created(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        # Fresh DB has no requests table yet; ensure_schema only adds session_id
        # to an existing requests table, but must still create mutation_audit.
        # Create a minimal requests table first (Monitor._init_db order).
        conn.execute(
            """CREATE TABLE IF NOT EXISTS requests (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               timestamp TEXT NOT NULL, model TEXT NOT NULL
            )"""
        )
        conn.commit()
        ensure_schema(conn)
        conn.commit()

        pragma = conn.execute("PRAGMA table_info(mutation_audit)").fetchall()
        col_names = [row[1] for row in pragma]
        assert "id" in col_names
        assert "timestamp" in col_names
        assert "session_id" in col_names
        assert "request_id" in col_names
        assert "mutation_type" in col_names
        assert "file_path" in col_names
        assert "diff_summary" in col_names
        conn.close()

    def test_session_id_added_to_requests(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        conn.execute(
            "CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT)"
        )
        conn.commit()
        ensure_schema(conn)
        conn.commit()

        col_names = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        assert "session_id" in col_names
        conn.close()

    def test_indexes_created(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT)")
        conn.commit()
        ensure_schema(conn)
        conn.commit()

        indexes = {
            row[1]
            for row in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_ma_session_id" in indexes
        assert "idx_ma_request_id" in indexes
        conn.close()


class TestEnsureSchemaIdempotent:
    def test_double_call_does_not_raise(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT)")
        conn.commit()
        ensure_schema(conn)
        conn.commit()
        # Second call must not raise
        ensure_schema(conn)
        conn.commit()
        conn.close()

    def test_schema_unchanged_after_double_call(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT)")
        conn.commit()
        ensure_schema(conn)
        conn.commit()
        ensure_schema(conn)
        conn.commit()

        col_names = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        assert col_names.count("session_id") == 1, "session_id must appear exactly once"
        conn.close()


class TestEnsureSchemaMigrationFromLegacy:
    def test_session_id_added_to_existing_table(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        _create_legacy_requests(conn)

        # Confirm session_id absent before migration
        col_names_before = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        assert "session_id" not in col_names_before

        ensure_schema(conn)
        conn.commit()

        col_names_after = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        assert "session_id" in col_names_after
        conn.close()

    def test_existing_rows_preserved_after_migration(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        _create_legacy_requests(conn)  # inserts 1 row

        row_count_before = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        assert row_count_before == 1

        ensure_schema(conn)
        conn.commit()

        row_count_after = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        assert row_count_after == 1, "existing rows must survive migration"

    def test_existing_row_session_id_is_null_after_migration(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        _create_legacy_requests(conn)
        ensure_schema(conn)
        conn.commit()

        row = conn.execute("SELECT session_id FROM requests LIMIT 1").fetchone()
        assert row is not None
        assert row[0] is None, "pre-existing rows should have NULL session_id"
        conn.close()


# ---------------------------------------------------------------------------
# mutation_audit insert / query
# ---------------------------------------------------------------------------


class TestMutationAuditInsert:
    def _setup(self, tmp_path):
        from tokenpak.proxy.db import ensure_schema

        conn = _fresh_conn(tmp_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT)")
        conn.commit()
        ensure_schema(conn)
        conn.commit()
        return conn

    def test_insert_returns_rowid(self, tmp_path):
        from tokenpak.proxy.db import insert_mutation_audit

        conn = self._setup(tmp_path)
        rowid = insert_mutation_audit(
            conn,
            timestamp=datetime.now().isoformat(),
            session_id="sess-abc",
            request_id="req-001",
            mutation_type="write",
            file_path="/repo/foo.py",
            diff_summary="+1 line",
        )
        conn.commit()
        assert isinstance(rowid, int)
        assert rowid >= 1
        conn.close()

    def test_inserted_row_queryable(self, tmp_path):
        from tokenpak.proxy.db import insert_mutation_audit

        conn = self._setup(tmp_path)
        ts = datetime.now().isoformat()
        insert_mutation_audit(
            conn,
            timestamp=ts,
            session_id="sess-xyz",
            request_id="req-002",
            mutation_type="edit",
            file_path="/repo/bar.py",
            diff_summary="-2 lines",
        )
        conn.commit()

        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM mutation_audit WHERE request_id = ?", ("req-002",)
        ).fetchone()
        assert row is not None
        assert row["session_id"] == "sess-xyz"
        assert row["mutation_type"] == "edit"
        assert row["file_path"] == "/repo/bar.py"
        assert row["diff_summary"] == "-2 lines"
        conn.close()

    def test_nullable_fields_accept_none(self, tmp_path):
        from tokenpak.proxy.db import insert_mutation_audit

        conn = self._setup(tmp_path)
        rowid = insert_mutation_audit(
            conn,
            timestamp=datetime.now().isoformat(),
        )
        conn.commit()
        row = conn.execute(
            "SELECT session_id, request_id, mutation_type, file_path, diff_summary "
            "FROM mutation_audit WHERE id = ?",
            (rowid,),
        ).fetchone()
        assert row == (None, None, None, None, None)
        conn.close()

    def test_multiple_rows_for_same_session(self, tmp_path):
        from tokenpak.proxy.db import insert_mutation_audit

        conn = self._setup(tmp_path)
        ts = datetime.now().isoformat()
        for i in range(3):
            insert_mutation_audit(
                conn,
                timestamp=ts,
                session_id="sess-multi",
                request_id=f"req-{i}",
                mutation_type="write",
                file_path=f"/repo/file{i}.py",
            )
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM mutation_audit WHERE session_id = ?", ("sess-multi",)
        ).fetchone()[0]
        assert count == 3
        conn.close()


# ---------------------------------------------------------------------------
# Integration — Monitor._init_db wires up ensure_schema
# ---------------------------------------------------------------------------


class TestMonitorInitDB:
    def test_fresh_monitor_creates_mutation_audit(self, tmp_path):
        from tokenpak.proxy.monitor import Monitor

        db_path = str(tmp_path / "monitor_fresh.db")
        Monitor(db_path)

        conn = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "mutation_audit" in tables, "Monitor._init_db must create mutation_audit"

    def test_fresh_monitor_requests_has_session_id(self, tmp_path):
        from tokenpak.proxy.monitor import Monitor

        db_path = str(tmp_path / "monitor_fresh2.db")
        Monitor(db_path)

        conn = sqlite3.connect(db_path)
        col_names = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        conn.close()
        assert "session_id" in col_names

    def test_legacy_monitor_db_migrated_without_data_loss(self, tmp_path):
        """Simulate an old monitor.db (no session_id) and verify Monitor migrates it."""
        db_path = str(tmp_path / "monitor_legacy.db")
        # Seed old-style DB with one request row
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE requests (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               timestamp TEXT NOT NULL,
               model TEXT NOT NULL,
               input_tokens INTEGER, output_tokens INTEGER,
               estimated_cost REAL, latency_ms INTEGER, status_code INTEGER,
               endpoint TEXT, compilation_mode TEXT, protected_tokens INTEGER,
               compressed_tokens INTEGER, injected_tokens INTEGER DEFAULT 0,
               injected_sources TEXT DEFAULT '', cache_read_tokens INTEGER DEFAULT 0,
               cache_creation_tokens INTEGER DEFAULT 0, would_have_saved INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT INTO requests (timestamp, model) VALUES (?, ?)",
            (datetime.now().isoformat(), "claude-sonnet-4-6"),
        )
        conn.commit()
        conn.close()

        # Monitor should migrate the existing DB
        from tokenpak.proxy.monitor import Monitor
        Monitor(db_path)

        conn2 = sqlite3.connect(db_path)
        # Data preserved
        count = conn2.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        assert count == 1
        # session_id column present
        col_names = [row[1] for row in conn2.execute("PRAGMA table_info(requests)").fetchall()]
        assert "session_id" in col_names
        # mutation_audit table present
        tables = {
            row[0]
            for row in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "mutation_audit" in tables
        conn2.close()

    def test_monitor_init_db_idempotent(self, tmp_path):
        """Calling Monitor twice on the same DB must not raise."""
        from tokenpak.proxy.monitor import Monitor

        db_path = str(tmp_path / "monitor_idem.db")
        Monitor(db_path)
        Monitor(db_path)  # second init — no-op expected
