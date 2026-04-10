"""Tests for tokenpak replay store (Phase 1 — task 1.9)."""

import pytest
from datetime import datetime, timedelta
from tokenpak.telemetry.replay import ReplayEntry, ReplayStore, get_replay_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_entry(**kwargs) -> ReplayEntry:
    defaults = dict(
        provider="anthropic",
        model="claude-3-haiku",
        input_tokens_raw=1000,
        input_tokens_sent=700,
        tokens_saved=300,
        cost_usd=0.001,
    )
    defaults.update(kwargs)
    return ReplayEntry.new(**defaults)


# ---------------------------------------------------------------------------
# ReplayEntry unit tests
# ---------------------------------------------------------------------------

class TestReplayEntry:
    def test_new_generates_id_and_timestamp(self):
        e = make_entry()
        assert len(e.replay_id) == 8
        assert isinstance(e.timestamp, datetime)

    def test_savings_pct(self):
        e = make_entry(input_tokens_raw=1000, tokens_saved=250)
        assert e.savings_pct == 25.0

    def test_savings_pct_zero_raw(self):
        e = make_entry(input_tokens_raw=0, tokens_saved=0)
        assert e.savings_pct == 0.0

    def test_to_dict_roundtrip(self):
        e = make_entry()
        d = e.to_dict()
        assert d["replay_id"] == e.replay_id
        assert d["provider"] == "anthropic"
        assert d["messages"] is None

    def test_summary_line_no_content(self):
        e = make_entry()
        line = e.summary_line()
        assert e.replay_id in line
        assert "anthropic/claude-3-haiku" in line

    def test_summary_line_with_content(self):
        e = make_entry(messages=[{"role": "user", "content": "hi"}])
        line = e.summary_line()
        assert "📦" in line

    def test_optional_content_fields(self):
        msgs = [{"role": "user", "content": "hello"}]
        resp = {"choices": [{"message": {"content": "world"}}]}
        e = make_entry(messages=msgs, response=resp)
        assert e.messages == msgs
        assert e.response == resp


# ---------------------------------------------------------------------------
# ReplayStore tests
# ---------------------------------------------------------------------------

class TestReplayStore:
    def setup_method(self):
        self.store = ReplayStore(":memory:")

    def test_capture_and_get(self):
        e = make_entry()
        self.store.capture(e)
        fetched = self.store.get(e.replay_id)
        assert fetched is not None
        assert fetched.replay_id == e.replay_id
        assert fetched.provider == "anthropic"

    def test_get_missing_returns_none(self):
        assert self.store.get("nope") is None

    def test_list_returns_most_recent_first(self):
        for i in range(5):
            self.store.capture(make_entry())
        entries = self.store.list(limit=10)
        assert len(entries) == 5
        # timestamps should be descending (or equal for fast inserts)
        for a, b in zip(entries, entries[1:]):
            assert a.timestamp >= b.timestamp

    def test_list_limit(self):
        for _ in range(10):
            self.store.capture(make_entry())
        assert len(self.store.list(limit=3)) == 3

    def test_list_provider_filter(self):
        self.store.capture(make_entry(provider="openai", model="gpt-4"))
        self.store.capture(make_entry(provider="anthropic", model="claude-3-haiku"))
        openai_entries = self.store.list(provider="openai")
        assert all(e.provider == "openai" for e in openai_entries)
        assert len(openai_entries) == 1

    def test_count(self):
        assert self.store.count() == 0
        self.store.capture(make_entry())
        self.store.capture(make_entry())
        assert self.store.count() == 2

    def test_delete(self):
        e = make_entry()
        self.store.capture(e)
        assert self.store.delete(e.replay_id) is True
        assert self.store.get(e.replay_id) is None

    def test_delete_missing_returns_false(self):
        assert self.store.delete("ghost") is False

    def test_prune_removes_old(self):
        old = make_entry()
        # Manually backdated
        old.timestamp = datetime(2020, 1, 1)
        self.store.capture(old)
        recent = make_entry()
        self.store.capture(recent)
        removed = self.store.prune(days=30)
        assert removed == 1
        assert self.store.get(old.replay_id) is None
        assert self.store.get(recent.replay_id) is not None

    def test_roundtrip_with_content(self):
        msgs = [{"role": "user", "content": "compress this"}]
        resp = {"choices": [{"message": {"content": "done"}}]}
        meta = {"compressed": True, "recipe": "python-imports"}
        e = make_entry(messages=msgs, response=resp, metadata=meta)
        self.store.capture(e)
        fetched = self.store.get(e.replay_id)
        assert fetched.messages == msgs
        assert fetched.response == resp
        assert fetched.metadata == meta

    def test_replace_on_duplicate_id(self):
        e = make_entry()
        self.store.capture(e)
        e.model = "claude-3-opus"  # mutate and re-insert
        self.store.capture(e)
        assert self.store.count() == 1
        assert self.store.get(e.replay_id).model == "claude-3-opus"

    def test_close_and_reopen(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            s1 = ReplayStore(path)
            e = make_entry()
            s1.capture(e)
            s1.close()
            s2 = ReplayStore(path)
            assert s2.count() == 1
            s2.close()
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------

class TestGetReplayStore:
    def test_singleton_returns_same(self):
        s1 = get_replay_store()
        s2 = get_replay_store()
        assert s1 is s2

    def test_new_path_replaces_singleton(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            s1 = get_replay_store(path)
            s2 = get_replay_store(path)
            assert s1 is s2
        finally:
            # reset singleton back to memory
            from tokenpak.telemetry import replay as _r
            _r._store = None
            _r._store_path = ":memory:"
            os.unlink(path)


# ---------------------------------------------------------------------------
# Clear tests
# ---------------------------------------------------------------------------

class TestReplayStoreClear:
    def test_clear_empty_store_returns_zero(self):
        s = ReplayStore(":memory:")
        assert s.clear() == 0

    def test_clear_removes_all_entries(self):
        s = ReplayStore(":memory:")
        s.capture(make_entry(provider="a"))
        s.capture(make_entry(provider="b"))
        s.capture(make_entry(provider="c"))
        assert s.count() == 3
        n = s.clear()
        assert n == 3
        assert s.count() == 0

    def test_clear_returns_count_removed(self):
        s = ReplayStore(":memory:")
        for _ in range(5):
            s.capture(make_entry())
        assert s.clear() == 5

    def test_clear_then_capture_works(self):
        """Store is still functional after clear."""
        s = ReplayStore(":memory:")
        s.capture(make_entry(provider="before"))
        s.clear()
        s.capture(make_entry(provider="after"))
        entries = s.list(limit=10)
        assert len(entries) == 1
        assert entries[0].provider == "after"
