# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.proxy.spend_guard.pending."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from tokenpak.proxy.spend_guard.pending import PendingStore, hash_request


@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "spend_guard.db")
    return PendingStore(db)


def _store_one(store, **overrides):
    base = dict(
        session_id="sess-A",
        body=b'{"model":"claude-opus-4-7","messages":[{"role":"user","content":"hi"}]}',
        headers={"x-api-key": "test", "content-type": "application/json"},
        target_url="https://api.anthropic.com/v1/messages",
        provider="anthropic",
        model="claude-opus-4-7",
        projected_tokens=600_000,
        projected_cost_usd=12.50,
        ttl_seconds=600,
    )
    base.update(overrides)
    return store.store(**base)


class TestStoreCycle:
    def test_store_returns_pending_id(self, store):
        p = _store_one(store)
        assert p.pending_id.startswith("tpg_")
        assert p.status == "pending"

    def test_get_by_session_returns_most_recent(self, store):
        p1 = _store_one(store)
        time.sleep(0.01)
        p2 = _store_one(store)
        got = store.get_by_session("sess-A")
        assert got is not None
        assert got.pending_id == p2.pending_id

    def test_get_by_session_returns_none_for_unknown(self, store):
        _store_one(store)
        assert store.get_by_session("sess-Z") is None

    def test_consume_returns_blob_once(self, store):
        p = _store_one(store)
        consumed = store.consume(p.pending_id)
        assert consumed is not None
        assert consumed.raw_request_blob == p.raw_request_blob
        # Second call returns None (atomic)
        assert store.consume(p.pending_id) is None

    def test_consume_preserves_bytes_exactly(self, store):
        # Byte-preservation invariant — replay must yield exact original.
        original = b"raw bytes \x00\xff with weird stuff"
        p = _store_one(store, body=original)
        out = store.consume(p.pending_id)
        assert out.raw_request_blob == original

    def test_discard_marks_status(self, store):
        p = _store_one(store)
        assert store.discard(p.pending_id) is True
        # After discard, get_by_session returns None
        assert store.get_by_session("sess-A") is None

    def test_discard_idempotent(self, store):
        p = _store_one(store)
        assert store.discard(p.pending_id) is True
        assert store.discard(p.pending_id) is False  # already discarded


class TestTTL:
    def test_expired_pending_not_returned(self, store):
        # Store with a 0-second TTL so it expires immediately.
        p = _store_one(store, ttl_seconds=0)
        time.sleep(0.05)
        assert store.get_by_session("sess-A") is None

    def test_expire_old_returns_count(self, store):
        _store_one(store, ttl_seconds=0)
        _store_one(store, session_id="sess-B", ttl_seconds=0)
        _store_one(store, session_id="sess-C", ttl_seconds=600)  # not expired
        time.sleep(0.05)
        n = store.expire_old()
        assert n == 2


class TestAntiLoop:
    def test_recent_block_by_hash_finds(self, store):
        body = b'{"model":"opus","messages":[{"role":"user","content":"X"}]}'
        h = hash_request(body, "claude-opus-4-7")
        p = _store_one(store, body=body)
        recent = store.recent_block_by_hash(h, within_seconds=10.0)
        assert recent is not None
        assert recent.pending_id == p.pending_id

    def test_recent_block_outside_window_misses(self, store):
        body = b'{"model":"opus","messages":[{"role":"user","content":"Y"}]}'
        h = hash_request(body, "claude-opus-4-7")
        _store_one(store, body=body)
        # Window of 0 seconds — should miss.
        recent = store.recent_block_by_hash(h, within_seconds=0.0)
        # NB: the row's created_at == now, so created_at > now-0 is False.
        assert recent is None

    def test_hash_stable_for_same_input(self):
        b = b"identical"
        assert hash_request(b, "model") == hash_request(b, "model")

    def test_hash_changes_with_model(self):
        b = b"identical"
        assert hash_request(b, "modelA") != hash_request(b, "modelB")


class TestHeaderRedaction:
    """Credential headers must never be persisted to disk.

    The held request is replayed with the live approving request's own auth,
    so dropping credential headers from storage is safe.
    """

    def test_non_credential_headers_preserved(self, store):
        hdrs = {"Content-Type": "application/json", "X-Custom": "value with spaces",
                "anthropic-version": "2023-06-01"}
        p = _store_one(store, headers=hdrs)
        out = store.consume(p.pending_id)
        assert out.raw_request_headers == hdrs

    def test_credential_headers_dropped_on_store(self, store):
        hdrs = {"Authorization": "Bearer CRED-SENTINEL-A", "x-api-key": "CRED-SENTINEL-B",
                "Cookie": "session=abc", "Content-Type": "application/json"}
        p = _store_one(store, headers=hdrs)
        # Returned object is already redacted.
        assert p.raw_request_headers == {"Content-Type": "application/json"}

    def test_consume_yields_redacted_headers(self, store):
        p = _store_one(store, headers={"authorization": "Bearer x", "x-foo": "1"})
        out = store.consume(p.pending_id)
        assert "authorization" not in out.raw_request_headers
        assert out.raw_request_headers == {"x-foo": "1"}

    def test_raw_secret_absent_from_db_bytes(self, store):
        # The literal secret must not appear anywhere in the db file.
        _store_one(store, headers={"Authorization": "Bearer LEAK-SENTINEL-AAAA1111",
                                   "x-api-key": "LEAK-SENTINEL-BBBB2222"})
        raw = Path(store.path).read_bytes()
        assert b"LEAK-SENTINEL-AAAA1111" not in raw
        assert b"LEAK-SENTINEL-BBBB2222" not in raw


class TestDbPermissions:
    def test_db_file_is_owner_only(self, store):
        _store_one(store)
        mode = stat.S_IMODE(os.stat(store.path).st_mode)
        assert mode == 0o600


class TestRedactionMigration:
    def test_existing_raw_rows_redacted_on_open(self, tmp_path):
        # Simulate an OLD db that persisted raw creds, then open it with the
        # current code and confirm the one-time migration redacts in place
        # without deleting the row.
        import json as _json
        import sqlite3
        dbp = tmp_path / "spend_guard.db"
        conn = sqlite3.connect(str(dbp))
        conn.execute(
            """CREATE TABLE pending_requests (
                   pending_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                   created_at REAL NOT NULL, expires_at REAL NOT NULL,
                   request_hash TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
                   model TEXT NOT NULL DEFAULT '',
                   projected_tokens INTEGER NOT NULL DEFAULT 0,
                   projected_cost_usd REAL NOT NULL DEFAULT 0.0,
                   raw_request_blob BLOB NOT NULL,
                   raw_request_headers TEXT NOT NULL DEFAULT '{}',
                   target_url TEXT NOT NULL DEFAULT '',
                   status TEXT NOT NULL DEFAULT 'pending')"""
        )
        raw = _json.dumps({"Authorization": "Bearer OLD-LEAK-SENTINEL",
                           "Content-Type": "application/json"})
        conn.execute(
            "INSERT INTO pending_requests (pending_id, session_id, created_at, "
            "expires_at, request_hash, raw_request_blob, raw_request_headers, status) "
            "VALUES ('tpg_old', 'sess-old', 0, 9e18, 'h', X'00', ?, 'pending')",
            (raw,),
        )
        conn.commit()
        conn.close()
        # Any connecting method triggers _ensure_schema → the one-time migration.
        s = PendingStore(str(dbp))
        assert s.get_by_session("no-such-session") is None
        # Verify in-place: row preserved (not deleted), headers redacted.
        conn2 = sqlite3.connect(str(dbp))
        row = conn2.execute(
            "SELECT raw_request_headers FROM pending_requests WHERE pending_id='tpg_old'"
        ).fetchone()
        conn2.close()
        assert row is not None  # row preserved, not deleted
        assert _json.loads(row[0]) == {"Content-Type": "application/json"}
        assert b"OLD-LEAK-SENTINEL" not in Path(dbp).read_bytes()
