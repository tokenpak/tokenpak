# SPDX-License-Identifier: Apache-2.0
"""Offline contract tests for the Pak-aware journal extension (Std 32 §10).

Per Std 32 §4.4 the journal is auto-capture; promotion to a MultiPak
Interaction Pak is opt-in via the Pro daemon. Phase 1 ships the OSS-side
read+marker surface — these tests assert the marker round-trips through
the existing :class:`JournalStore` schema additively (no breaking change),
the listing helpers respect filtering, and the stub Pak conforms to the
contract in :mod:`tokenpak.tip.pak`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenpak.companion.journal.pak_aware import (
    KEY_IS_PROMOTION_CANDIDATE,
    KEY_PROMOTED_PAK_ID,
    JournalEntryRow,
    _authority_for_entry_type,
    count_promotion_candidates,
    journal_entry_to_pak_stub,
    list_promotion_candidates,
    mark_promotion_candidate,
)
from tokenpak.companion.journal.store import JournalStore
from tokenpak.tip.pak import (
    Pak,
    PakAuthority,
    PakConfidence,
    PakRetention,
    PakStatus,
    PakSubtype,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def journal(tmp_path: Path) -> tuple[JournalStore, Path]:
    """A live JournalStore with two sessions and a few entries."""
    db = tmp_path / "journal.db"
    store = JournalStore(db)
    store.start_session("s1", project_dir="/tmp/p1", model="opus-4-7")
    store.start_session("s2", project_dir="/tmp/p2", model="sonnet-4-6")
    store.add_entry("s1", "auto", "auto1", {"foo": "bar"})
    store.add_entry("s1", "user", "user1", {})
    store.add_entry("s1", "milestone", "ms1", {})
    store.add_entry("s2", "auto", "auto2", {})
    return store, db


def _entry_ids(db: Path, session_id: str) -> list[int]:
    """Read entry ids for a session in insertion order."""
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT id FROM entries WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _entry_meta(db: Path, entry_id: int) -> dict:
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT metadata_json FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else {}


# ---------------------------------------------------------------------------
# mark_promotion_candidate
# ---------------------------------------------------------------------------


def test_mark_returns_true_for_existing_entry(journal):
    _, db = journal
    eid = _entry_ids(db, "s1")[0]
    assert mark_promotion_candidate(db, eid) is True


def test_mark_returns_false_for_missing_entry(journal):
    _, db = journal
    assert mark_promotion_candidate(db, 999_999) is False


def test_mark_sets_metadata_flag(journal):
    _, db = journal
    eid = _entry_ids(db, "s1")[0]
    mark_promotion_candidate(db, eid)
    meta = _entry_meta(db, eid)
    assert meta[KEY_IS_PROMOTION_CANDIDATE] is True
    assert meta.get("foo") == "bar", "existing metadata must be preserved"


def test_mark_off_clears_flag_and_pak_id(journal):
    _, db = journal
    eid = _entry_ids(db, "s1")[0]
    mark_promotion_candidate(db, eid, on=True, promoted_pak_id="journal:s1:1")
    meta_before = _entry_meta(db, eid)
    assert meta_before[KEY_PROMOTED_PAK_ID] == "journal:s1:1"

    mark_promotion_candidate(db, eid, on=False)
    meta_after = _entry_meta(db, eid)
    assert meta_after[KEY_IS_PROMOTION_CANDIDATE] is False
    assert KEY_PROMOTED_PAK_ID not in meta_after


def test_mark_idempotent(journal):
    _, db = journal
    eid = _entry_ids(db, "s1")[0]
    mark_promotion_candidate(db, eid)
    mark_promotion_candidate(db, eid)
    mark_promotion_candidate(db, eid)
    candidates = list_promotion_candidates(db)
    assert sum(1 for c in candidates if c.entry_id == eid) == 1


# ---------------------------------------------------------------------------
# list_promotion_candidates
# ---------------------------------------------------------------------------


def test_list_returns_only_marked_entries(journal):
    _, db = journal
    s1_ids = _entry_ids(db, "s1")
    mark_promotion_candidate(db, s1_ids[0])  # auto1
    mark_promotion_candidate(db, s1_ids[2])  # ms1
    candidates = list_promotion_candidates(db)
    assert {c.entry_id for c in candidates} == {s1_ids[0], s1_ids[2]}


def test_list_filters_by_session(journal):
    _, db = journal
    s1_ids = _entry_ids(db, "s1")
    s2_ids = _entry_ids(db, "s2")
    mark_promotion_candidate(db, s1_ids[0])
    mark_promotion_candidate(db, s2_ids[0])
    s1_only = list_promotion_candidates(db, session_id="s1")
    assert {c.entry_id for c in s1_only} == {s1_ids[0]}


def test_list_orders_newest_first(journal):
    _, db = journal
    s1_ids = _entry_ids(db, "s1")
    for eid in s1_ids:
        mark_promotion_candidate(db, eid)
    candidates = list_promotion_candidates(db, session_id="s1")
    timestamps = [c.timestamp for c in candidates]
    assert timestamps == sorted(timestamps, reverse=True)


def test_list_respects_limit(journal):
    _, db = journal
    for eid in _entry_ids(db, "s1"):
        mark_promotion_candidate(db, eid)
    candidates = list_promotion_candidates(db, limit=2)
    assert len(candidates) == 2


def test_list_empty_when_no_candidates(journal):
    _, db = journal
    assert list_promotion_candidates(db) == []


def test_list_does_not_match_false_or_other_truthy_strings(journal):
    """The marker is a JSON boolean ``true`` — entries with the literal string
    ``"true"`` or with the flag set to false MUST NOT match. This is the
    structural-disjointness guard."""
    _, db = journal
    eid = _entry_ids(db, "s1")[0]
    # Manually inject an off-state row + a confusing string row
    import sqlite3

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE entries SET metadata_json = ? WHERE id = ?",
        (json.dumps({"is_promotion_candidate": False}), eid),
    )
    eid2 = _entry_ids(db, "s1")[1]
    conn.execute(
        "UPDATE entries SET metadata_json = ? WHERE id = ?",
        (json.dumps({"note": "is_promotion_candidate: true (text)"}), eid2),
    )
    conn.commit()
    conn.close()
    candidates = list_promotion_candidates(db)
    # Neither row should match the strict-true LIKE pattern.
    assert all(c.entry_id not in (eid, eid2) for c in candidates)


# ---------------------------------------------------------------------------
# count_promotion_candidates
# ---------------------------------------------------------------------------


def test_count_zero_when_none_marked(journal):
    _, db = journal
    assert count_promotion_candidates(db) == 0


def test_count_matches_list_size(journal):
    _, db = journal
    for eid in _entry_ids(db, "s1")[:2]:
        mark_promotion_candidate(db, eid)
    assert count_promotion_candidates(db) == 2


def test_count_filters_by_session(journal):
    _, db = journal
    s1_ids = _entry_ids(db, "s1")
    s2_ids = _entry_ids(db, "s2")
    mark_promotion_candidate(db, s1_ids[0])
    mark_promotion_candidate(db, s2_ids[0])
    assert count_promotion_candidates(db, session_id="s1") == 1


# ---------------------------------------------------------------------------
# journal_entry_to_pak_stub
# ---------------------------------------------------------------------------


def _make_row(entry_type: str = "auto", content: str = "hello") -> JournalEntryRow:
    return JournalEntryRow(
        entry_id=42,
        session_id="s1",
        timestamp=1_700_000_000.0,
        entry_type=entry_type,
        content=content,
        metadata={},
    )


def test_stub_is_pak_instance():
    pak = journal_entry_to_pak_stub(_make_row())
    assert isinstance(pak, Pak)


def test_stub_subtype_is_interaction():
    """Std 32 §2.2 — journal-derived Paks are Interaction Paks."""
    pak = journal_entry_to_pak_stub(_make_row())
    assert pak.pak_type is PakSubtype.INTERACTION


def test_stub_status_is_proposed():
    pak = journal_entry_to_pak_stub(_make_row())
    assert pak.status is PakStatus.PROPOSED


def test_stub_id_carries_session_and_entry_id():
    pak = journal_entry_to_pak_stub(_make_row())
    assert pak.pak_id == "journal:s1:42"


def test_stub_authority_milestone_to_tool_result():
    pak = journal_entry_to_pak_stub(_make_row(entry_type="milestone"))
    assert pak.authority is PakAuthority.TOOL_RESULT


def test_stub_authority_unknown_to_llm_generated():
    """Std 31 §2 graceful fallback for unknown entry types."""
    pak = journal_entry_to_pak_stub(_make_row(entry_type="exotic_type"))
    assert pak.authority is PakAuthority.LLM_GENERATED


def test_stub_default_retention_is_180_days():
    """Std 32 §8 — Interaction Paks default to 180-day retention."""
    pak = journal_entry_to_pak_stub(_make_row())
    assert pak.retention.ttl is PakRetention.DAYS_180


def test_stub_default_confidence_is_low():
    """Stubs are unverified — daemon may upgrade on promotion."""
    pak = journal_entry_to_pak_stub(_make_row())
    assert pak.confidence is PakConfidence.LOW


def test_stub_summary_truncates_long_content():
    pak = journal_entry_to_pak_stub(_make_row(content="x" * 500))
    assert len(pak.summary) <= 250  # 240 chars + ellipsis


def test_stub_summary_handles_empty_content():
    pak = journal_entry_to_pak_stub(_make_row(content=""))
    assert "Empty journal entry" in pak.summary


def test_stub_round_trips_through_dict():
    pak = journal_entry_to_pak_stub(_make_row())
    pak2 = Pak.from_dict(pak.to_dict())
    assert pak2 == pak


def test_authority_table_covers_known_entry_types():
    """Sanity check on the dynamic-discovery table."""
    for et in ("auto", "user", "milestone", "cost", "capsule", "companion_savings"):
        # Any return value is acceptable; we just verify no exception and
        # a PakAuthority instance is returned.
        assert isinstance(_authority_for_entry_type(et), PakAuthority)
