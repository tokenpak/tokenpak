# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.proxy.spend_guard.pending — TSG-02 acceptance."""

from __future__ import annotations

import time

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


class TestHeadersRoundTrip:
    def test_headers_preserved(self, store):
        hdrs = {"X-Api-Key": "abc123", "Content-Type": "application/json",
                "X-Custom": "value with spaces"}
        p = _store_one(store, headers=hdrs)
        out = store.consume(p.pending_id)
        assert out.raw_request_headers == hdrs
