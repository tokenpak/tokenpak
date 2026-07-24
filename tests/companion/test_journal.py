# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak/companion/journal/store.py (TEST-COV-COMP-05).

Covers:
- JournalStore write / read / list operations
- Edge cases: empty store, nonexistent session, session isolation
- Entry filtering by type, limit, and ordering
- Updating session end-time
- Metadata round-trip
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tokenpak.companion.journal.store import JournalStore, SessionRecord


@pytest.fixture()
def store(tmp_path: Path) -> JournalStore:
    return JournalStore(tmp_path / "journal.db")


# ---------------------------------------------------------------------------
# Basic session lifecycle
# ---------------------------------------------------------------------------


def test_start_session_creates_record(store: JournalStore) -> None:
    store.start_session("sess-1", project_dir="/proj", model="claude-3")
    record = store.get_session("sess-1")
    assert record is not None
    assert record.session_id == "sess-1"
    assert record.project_dir == "/proj"
    assert record.model == "claude-3"
    assert record.ended_at is None


def test_end_session_sets_ended_at(store: JournalStore) -> None:
    store.start_session("sess-end", project_dir="", model="")
    before = time.time()
    store.end_session("sess-end")
    after = time.time()
    record = store.get_session("sess-end")
    assert record is not None
    assert record.ended_at is not None
    assert before <= record.ended_at <= after


def test_get_session_nonexistent_returns_none(store: JournalStore) -> None:
    result = store.get_session("does-not-exist")
    assert result is None


def test_start_session_defaults(store: JournalStore) -> None:
    store.start_session("sess-defaults")
    record = store.get_session("sess-defaults")
    assert record is not None
    assert record.project_dir == ""
    assert record.model == ""
    assert record.total_requests == 0
    assert record.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Journal entry write / read
# ---------------------------------------------------------------------------


def test_add_and_read_entry(store: JournalStore) -> None:
    store.start_session("sess-entry")
    store.add_entry("sess-entry", "auto", "first entry")
    entries = store.get_entries("sess-entry")
    assert len(entries) == 1
    assert entries[0].content == "first entry"
    assert entries[0].entry_type == "auto"


def test_entry_count_reflected_in_session_record(store: JournalStore) -> None:
    store.start_session("sess-count")
    for i in range(5):
        store.add_entry("sess-count", "auto", f"entry {i}")
    record = store.get_session("sess-count")
    assert record is not None
    assert record.entry_count == 5


def test_get_entries_empty_store(store: JournalStore) -> None:
    store.start_session("sess-empty")
    entries = store.get_entries("sess-empty")
    assert entries == []


def test_get_entries_nonexistent_session_returns_empty(store: JournalStore) -> None:
    entries = store.get_entries("ghost-session")
    assert entries == []


def test_metadata_round_trips(store: JournalStore) -> None:
    meta = {"tokens": 42, "model": "claude-3", "nested": {"x": True}}
    store.start_session("sess-meta")
    store.add_entry("sess-meta", "cost", "usage snapshot", metadata=meta)
    entries = store.get_entries("sess-meta")
    assert len(entries) == 1
    assert entries[0].metadata == meta


def test_entry_filter_by_type(store: JournalStore) -> None:
    store.start_session("sess-filter")
    store.add_entry("sess-filter", "auto", "auto note")
    store.add_entry("sess-filter", "user", "user note")
    store.add_entry("sess-filter", "milestone", "milestone note")

    auto_entries = store.get_entries("sess-filter", entry_type="auto")
    assert len(auto_entries) == 1
    assert auto_entries[0].content == "auto note"

    user_entries = store.get_entries("sess-filter", entry_type="user")
    assert len(user_entries) == 1
    assert user_entries[0].content == "user note"


def test_entry_limit_respected(store: JournalStore) -> None:
    store.start_session("sess-limit")
    for i in range(10):
        store.add_entry("sess-limit", "auto", f"entry {i}")
    entries = store.get_entries("sess-limit", limit=3)
    assert len(entries) == 3


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


def test_session_isolation_entries(store: JournalStore) -> None:
    store.start_session("sess-a")
    store.start_session("sess-b")
    store.add_entry("sess-a", "auto", "belongs to A")
    store.add_entry("sess-b", "auto", "belongs to B")

    a_entries = store.get_entries("sess-a")
    b_entries = store.get_entries("sess-b")

    assert len(a_entries) == 1
    assert a_entries[0].content == "belongs to A"
    assert len(b_entries) == 1
    assert b_entries[0].content == "belongs to B"


def test_end_session_does_not_affect_sibling(store: JournalStore) -> None:
    store.start_session("sess-x")
    store.start_session("sess-y")
    store.end_session("sess-x")

    rec_x = store.get_session("sess-x")
    rec_y = store.get_session("sess-y")
    assert rec_x is not None and rec_x.ended_at is not None
    assert rec_y is not None and rec_y.ended_at is None


# ---------------------------------------------------------------------------
# recent_sessions listing
# ---------------------------------------------------------------------------


def test_recent_sessions_empty_store(store: JournalStore) -> None:
    sessions = store.recent_sessions()
    assert sessions == []


def test_recent_sessions_ordered_newest_first(store: JournalStore) -> None:
    for name in ("oldest", "middle", "newest"):
        store.start_session(name)
        time.sleep(0.01)  # ensure distinct timestamps
    sessions = store.recent_sessions()
    assert [s.session_id for s in sessions] == ["newest", "middle", "oldest"]


def test_recent_sessions_limit(store: JournalStore) -> None:
    for i in range(5):
        store.start_session(f"sess-{i}")
    sessions = store.recent_sessions(limit=2)
    assert len(sessions) == 2


def test_recent_sessions_returns_session_records(store: JournalStore) -> None:
    store.start_session("rec-test", project_dir="/home/user", model="claude-haiku")
    sessions = store.recent_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert isinstance(s, SessionRecord)
    assert s.project_dir == "/home/user"
    assert s.model == "claude-haiku"
