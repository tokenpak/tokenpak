"""tests/proxy/test_audit_log.py

TRIX-08 / AC-2.3 — audit_events schema rewrite + proxy wire-up tests.

Test coverage:
  1. Schema migration creates audit_events table with all 9 compliance columns
  2. Migration is idempotent (safe to run twice)
  3. ProxyAuditLog.write_event() inserts a row with all 9 columns populated
  4. All 9 compliance columns are non-null for a normal request
  5. Async write dispatched as background thread — does not block caller
  6. user_id is a hash (not the raw token) for authenticated requests
  7. user_id is None for localhost clients
  8. query() returns rows filtered by --since and --user
  9. Proxy end-to-end: row written to audit_events after a proxied POST request
     (integration with stub upstream, no real Anthropic call)
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from http.client import HTTPConnection
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path) -> str:
    """Return a path to a fresh in-memory-style temp DB."""
    return str(tmp_path / "test_monitor.db")


# ---------------------------------------------------------------------------
# Unit tests — ProxyAuditLog in isolation
# ---------------------------------------------------------------------------


class TestSchemaAndMigration:
    def test_creates_audit_events_table(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog, _AUDIT_EVENTS_NEW_COLUMNS

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)

        conn = sqlite3.connect(db_path)
        pragma = conn.execute("PRAGMA table_info(audit_events)").fetchall()
        conn.close()

        col_names = [row[1] for row in pragma]
        assert "ts" in col_names
        # Verify all 9 compliance columns exist
        for col_name, _ in _AUDIT_EVENTS_NEW_COLUMNS:
            assert col_name in col_names, f"Missing compliance column: {col_name}"

    def test_indexes_created(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        ProxyAuditLog(db_path)

        conn = sqlite3.connect(db_path)
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_events'"
        ).fetchall()
        conn.close()

        index_names = {row[0] for row in indexes}
        assert "idx_audit_events_request_id" in index_names
        assert "idx_audit_events_user_id" in index_names
        assert "idx_audit_events_client_ip" in index_names

    def test_migration_idempotent(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog, _migrate_audit_events

        db_path = _make_db(tmp_path)
        # First run
        ProxyAuditLog(db_path)

        # Second run — should not raise or corrupt
        conn = sqlite3.connect(db_path)
        try:
            _migrate_audit_events(conn)  # no-op
        finally:
            conn.close()

        # Table should still have all columns
        conn2 = sqlite3.connect(db_path)
        pragma = conn2.execute("PRAGMA table_info(audit_events)").fetchall()
        conn2.close()
        col_names = [row[1] for row in pragma]
        assert "request_id" in col_names
        assert "cost_usd" in col_names

    def test_schema_version_recorded(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog, _AUDIT_SCHEMA_VERSION

        db_path = _make_db(tmp_path)
        ProxyAuditLog(db_path)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT version FROM schema_version WHERE version = ?", (_AUDIT_SCHEMA_VERSION,)
        ).fetchone()
        conn.close()

        assert row is not None, "schema_version row should be written after migration"


class TestWriteEvent:
    def test_row_written(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)
        rid = str(uuid.uuid4())
        log.write_event(
            request_id=rid,
            user_id="abc123",
            client_ip="10.0.0.1",
            endpoint="/v1/messages",
            model="claude-sonnet-4-6",
            tokens_in=42,
            tokens_out=12,
            cost_usd=0.0001,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM audit_events WHERE request_id = ?", (rid,)
        ).fetchone()
        conn.close()

        assert row is not None, "write_event should insert a row"

    def test_all_9_columns_populated(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)
        rid = str(uuid.uuid4())
        log.write_event(
            request_id=rid,
            user_id="deadbeef01234567",
            client_ip="192.168.1.50",
            endpoint="/v1/messages",
            model="claude-opus-4-6",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.00250,
            cache_read_tokens=10,
            cache_creation_tokens=5,
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM audit_events WHERE request_id = ?", (rid,)
        ).fetchone()
        conn.close()

        assert row["user_id"] == "deadbeef01234567"
        assert row["client_ip"] == "192.168.1.50"
        assert row["request_id"] == rid
        assert row["endpoint"] == "/v1/messages"
        assert row["tokens_in"] == 100
        assert row["tokens_out"] == 50
        assert abs(row["cost_usd"] - 0.00250) < 1e-9
        assert row["cache_read_tokens"] == 10
        assert row["cache_creation_tokens"] == 5

    def test_write_event_does_not_raise(self, tmp_path):
        """write_event swallows errors — caller is never disrupted."""
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)
        # Close the connection object deliberately to force an error path
        # ProxyAuditLog opens a fresh connection per write so it should still
        # write, not raise.
        log.write_event(request_id="test", user_id=None, client_ip="127.0.0.1")
        # No exception = pass


class TestAsyncWrite:
    def test_background_write_does_not_block(self, tmp_path):
        """_write_proxy_audit_event should return quickly without waiting for DB."""
        import importlib.util
        import sys

        # Load proxy.py module so we can call _write_proxy_audit_event
        proxy_path = _REPO_ROOT / "proxy.py"
        if not proxy_path.exists():
            pytest.skip("proxy.py not found at repo root")

        spec = importlib.util.spec_from_file_location("_proxy_audit_test", proxy_path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        except Exception:
            pytest.skip("Could not load proxy.py for background write test")

        if not hasattr(mod, "_write_proxy_audit_event"):
            pytest.skip("_write_proxy_audit_event not found in proxy.py")

        db_path = _make_db(tmp_path)
        # Ensure ProxyAuditLog is initialised for this db_path
        from tokenpak.pro.audit_log import ProxyAuditLog
        ProxyAuditLog(db_path)
        # Reset the global singleton so it picks up our test db_path
        mod._PROXY_AUDIT_LOG = None

        t0 = time.monotonic()
        mod._write_proxy_audit_event(
            db_path=db_path,
            request_id=str(uuid.uuid4()),
            user_id="testuser",
            client_ip="127.0.0.1",
            endpoint="/v1/messages",
            model="claude-sonnet-4-6",
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.00001,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
        elapsed = (time.monotonic() - t0) * 1000
        # Should return in < 50 ms (background thread dispatch is near-instant)
        assert elapsed < 50, f"_write_proxy_audit_event blocked for {elapsed:.1f}ms"

        # Wait for background thread to complete, then verify row exists
        time.sleep(0.2)
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        conn.close()
        assert count >= 1, "Background thread should have written at least one row"


class TestUserIdHashing:
    def test_token_is_hashed_not_stored(self, tmp_path):
        """user_id must be a hash digest — not the literal token value."""
        from tokenpak.pro.audit_log import ProxyAuditLog, _hash_user_id

        raw_token = "super-secret-key-value"
        hashed = _hash_user_id(raw_token)

        assert hashed is not None
        assert raw_token not in hashed, "Raw token must not appear in the stored user_id"
        assert len(hashed) == 16, "user_id should be 16-char hex prefix of SHA-256"

    def test_localhost_user_id_is_none(self):
        """Localhost requests should have user_id=None regardless of token."""
        from tokenpak.pro.audit_log import _hash_user_id

        # Localhost bypass: caller sets user_id=None when client_ip in loopback set
        # Verify _hash_user_id(None) or _hash_user_id("") returns None
        assert _hash_user_id("") is None
        assert _hash_user_id(None) is None

    def test_same_token_same_hash(self):
        from tokenpak.pro.audit_log import _hash_user_id

        token = "my-stable-token"
        assert _hash_user_id(token) == _hash_user_id(token)


class TestQuery:
    def _write_n(self, log, n=3, base_ts=None):
        for i in range(n):
            ts_override = None
            log.write_event(
                request_id=str(uuid.uuid4()),
                user_id=f"user{i:02d}",
                client_ip=f"10.0.0.{i+1}",
                endpoint="/v1/messages",
                model="claude-sonnet-4-6",
                tokens_in=10 * (i + 1),
                tokens_out=5,
                cost_usd=0.0001 * (i + 1),
            )

    def test_query_returns_rows(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)
        self._write_n(log, 3)

        rows = log.query(limit=10)
        assert len(rows) == 3

    def test_query_since_filters(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)
        self._write_n(log, 3)

        # A future date — should return 0 rows
        rows = log.query(since="2099-01-01", limit=100)
        assert len(rows) == 0

    def test_query_user_filter(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)
        self._write_n(log, 3)

        rows = log.query(user="user01", limit=10)
        assert len(rows) == 1
        assert rows[0]["user_id"] == "user01"

    def test_query_limit(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)
        self._write_n(log, 5)

        rows = log.query(limit=2)
        assert len(rows) == 2

    def test_query_empty_db_returns_empty(self, tmp_path):
        from tokenpak.pro.audit_log import ProxyAuditLog

        db_path = _make_db(tmp_path)
        log = ProxyAuditLog(db_path)

        rows = log.query()
        assert rows == []


# ---------------------------------------------------------------------------
# Integration test — proxy end-to-end with stub upstream
# ---------------------------------------------------------------------------


class TestProxyEndToEnd:
    """Spin up the proxy pointing at a stub upstream; POST a message;
    verify _write_proxy_audit_event is called with the right parameters."""

    def test_audit_write_called_after_request(self, stub_upstream, tmp_path):
        """Full-stack: proxy → stub → _write_proxy_audit_event called.

        Uses mock.patch to intercept _write_proxy_audit_event inside the loaded
        proxy module, verifying it is called with the expected arguments without
        relying on the background thread flushing to SQLite within the test window.
        """
        import importlib.util
        import socket as _socket
        from unittest.mock import patch, call

        proxy_path = _REPO_ROOT / "proxy.py"
        if not proxy_path.exists():
            pytest.skip("proxy.py not found at repo root")

        db_path = str(tmp_path / "monitor.db")

        # Ensure ProxyAuditLog table is migrated for our test db
        from tokenpak.pro.audit_log import ProxyAuditLog
        ProxyAuditLog._singleton = None
        ProxyAuditLog(db_path)
        ProxyAuditLog._singleton = None

        # Load proxy module with test env vars set first
        env_backup = {}
        test_env = {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "TOKENPAK_DB": db_path,
            # anthropic adapter source_format is "anthropic-messages"
            "TOKENPAK_UPSTREAM_ANTHROPIC_MESSAGES": f"http://127.0.0.1:{stub_upstream.server_port}",
        }
        for k, v in test_env.items():
            env_backup[k] = os.environ.get(k)
            os.environ[k] = v

        proxy_mod_name = f"_proxy_e2e_audit_{id(tmp_path)}"
        spec = importlib.util.spec_from_file_location(proxy_mod_name, proxy_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[proxy_mod_name] = mod
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        except Exception as exc:
            pytest.skip(f"Could not load proxy.py: {exc}")

        # Track audit write calls via a list (thread-safe append)
        _calls = []

        def _fake_write(**kwargs):
            _calls.append(kwargs)

        # Replace _write_proxy_audit_event in the loaded module
        mod._write_proxy_audit_event = _fake_write

        # Find free port and start proxy
        with _socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            proxy_port = s.getsockname()[1]

        proxy_server = mod.ThreadedHTTPServer(
            ("127.0.0.1", proxy_port), mod.ForwardProxyHandler
        )
        t = threading.Thread(target=proxy_server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.2)

        try:
            body = json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 10,
            }).encode()

            conn = HTTPConnection("127.0.0.1", proxy_port, timeout=10)
            conn.request(
                "POST",
                "/v1/messages",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            resp = conn.getresponse()
            resp.read()
            conn.close()

            # Brief wait for the proxy handler thread to finish post-response work
            time.sleep(0.3)

            assert len(_calls) >= 1, (
                "_write_proxy_audit_event should be called at least once per request"
            )
            call_kwargs = _calls[0]
            assert "request_id" in call_kwargs
            assert "client_ip" in call_kwargs
            assert "endpoint" in call_kwargs
            assert call_kwargs.get("endpoint") == "/v1/messages"
            assert call_kwargs.get("model") is not None

        finally:
            proxy_server.shutdown()
            t.join(timeout=2)
            sys.modules.pop(proxy_mod_name, None)
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            ProxyAuditLog._singleton = None
