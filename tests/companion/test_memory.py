"""
Unit tests for tokenpak/companion/memory/ module.

Covers:
- session_capsules: build_session_capsule, serialize_capsule,
  score_capsule_sections, capsule_retrieval_score
- decision_memory: DecisionMemoryDB store / retrieve / list / edge cases
"""

import json

import pytest

from tokenpak.companion.memory.decision_memory import DecisionMemoryDB, DecisionRecord
from tokenpak.companion.memory.session_capsules import (
    REQUIRED_CAPSULE_SECTIONS,
    build_session_capsule,
    capsule_retrieval_score,
    score_capsule_sections,
    serialize_capsule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """Return a fresh in-memory (temp-file) DecisionMemoryDB."""
    db_path = str(tmp_path / "test_memory.db")
    return DecisionMemoryDB(db_path=db_path)


_SAMPLE_MARKDOWN = """\
---
session_id: s-001
date: 2026-04-14
---

# Session Notes

## Decisions Made
- Use SQLite for the journal store
- Prefer WAL mode for concurrent writes

## Artifacts Created
- tests/companion/test_journal.py
- tokenpak/companion/journal/store.py

## Action Items
- Enable WAL mode on journal SQLite
- Add retry logic on SQLITE_BUSY

## Insights
- WAL mode prevents most concurrent-write deadlocks

## Raw Transcript Reference
see session-s-001.log
"""


# ---------------------------------------------------------------------------
# session_capsules tests
# ---------------------------------------------------------------------------

class TestBuildSessionCapsule:
    def test_decisions_extracted(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN, source_path="test.md")
        assert len(capsule["decisions_made"]) == 2
        assert "Use SQLite for the journal store" in capsule["decisions_made"]

    def test_artifacts_extracted(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        assert len(capsule["artifacts_created"]) == 2
        assert "tests/companion/test_journal.py" in capsule["artifacts_created"]

    def test_action_items_extracted(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        assert len(capsule["action_items"]) == 2
        assert "Enable WAL mode on journal SQLite" in capsule["action_items"]

    def test_insights_extracted(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        assert len(capsule["insights"]) == 1
        assert "WAL mode" in capsule["insights"][0]

    def test_all_required_sections_present(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        for section in REQUIRED_CAPSULE_SECTIONS:
            assert section in capsule, f"Missing section: {section}"

    def test_empty_markdown_gives_empty_lists(self):
        capsule = build_session_capsule("")
        assert capsule["decisions_made"] == []
        assert capsule["artifacts_created"] == []
        assert capsule["action_items"] == []
        assert capsule["insights"] == []

    def test_metadata_includes_source_path(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN, source_path="/tmp/foo.md")
        assert capsule["session_metadata"]["source_path"] == "/tmp/foo.md"

    def test_metadata_sha256_is_hex(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        sha = capsule["session_metadata"]["sha256"]
        assert len(sha) == 64
        int(sha, 16)  # raises if not valid hex


class TestSerializeCapsule:
    def test_returns_valid_json(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        raw = serialize_capsule(capsule)
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_json_is_sorted_keys(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        raw = serialize_capsule(capsule)
        parsed = json.loads(raw)
        assert list(parsed.keys()) == sorted(parsed.keys())


class TestScoreCapsuleSections:
    def test_decisions_made_scores_highest(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        scores = score_capsule_sections(capsule)
        assert scores["decisions_made"] > scores["insights"]
        assert scores["decisions_made"] > scores["raw_transcript_reference"]

    def test_empty_capsule_scores_zero_for_lists(self):
        capsule = build_session_capsule("")
        scores = score_capsule_sections(capsule)
        assert scores["decisions_made"] == 0.0
        assert scores["artifacts_created"] == 0.0

    def test_all_sections_present_in_scores(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        scores = score_capsule_sections(capsule)
        for section in REQUIRED_CAPSULE_SECTIONS:
            assert section in scores


class TestCapsuleRetrievalScore:
    def test_no_boost_for_none_capsule(self):
        result = capsule_retrieval_score(5.0, None)
        assert result == 5.0

    def test_boost_for_rich_capsule(self):
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        boosted = capsule_retrieval_score(0.0, capsule)
        assert boosted > 0.0

    def test_boost_capped_at_five(self):
        # Even a very rich capsule cannot add more than 5.0
        capsule = build_session_capsule(_SAMPLE_MARKDOWN)
        result = capsule_retrieval_score(0.0, capsule)
        assert result <= 5.0

    def test_boost_for_empty_capsule_is_zero_or_positive(self):
        capsule = build_session_capsule("")
        result = capsule_retrieval_score(2.0, capsule)
        assert result >= 2.0


# ---------------------------------------------------------------------------
# DecisionMemoryDB tests
# ---------------------------------------------------------------------------

class TestDecisionMemoryStore:
    def test_record_returns_id(self, tmp_path):
        db = _make_db(tmp_path)
        record_id = db.record("Should we use BM25?", "Yes", confidence=0.8)
        assert record_id.startswith("dec_")

    def test_retrieve_stored_decision(self, tmp_path):
        db = _make_db(tmp_path)
        db.record("Should we use BM25?", "Yes — for <10K blocks", confidence=0.8)
        results = db.retrieve(query="Should we use BM25?")
        assert len(results) == 1
        assert results[0].decision == "Yes — for <10K blocks"
        assert isinstance(results[0], DecisionRecord)

    def test_empty_store_returns_empty_list(self, tmp_path):
        db = _make_db(tmp_path)
        results = db.retrieve(query="anything")
        assert results == []

    def test_missing_key_returns_empty_list(self, tmp_path):
        db = _make_db(tmp_path)
        db.record("query A", "decision A")
        results = db.retrieve(query="query B")
        assert results == []

    def test_key_overwrite_creates_two_records(self, tmp_path):
        """record() is append-only; same query creates multiple records."""
        db = _make_db(tmp_path)
        db.record("same query", "decision v1", confidence=0.6)
        db.record("same query", "decision v2", confidence=0.9)
        results = db.retrieve(query="same query")
        assert len(results) == 2
        # Results sorted by confidence descending
        assert results[0].confidence >= results[1].confidence

    def test_confidence_clamped_below_zero(self, tmp_path):
        db = _make_db(tmp_path)
        db.record("q", "d", confidence=-5.0)
        results = db.retrieve(query="q")
        assert results[0].confidence == 0.0

    def test_confidence_clamped_above_one(self, tmp_path):
        db = _make_db(tmp_path)
        db.record("q", "d", confidence=99.9)
        results = db.retrieve(query="q")
        assert results[0].confidence == 1.0


class TestDecisionMemoryList:
    def test_count_empty(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.count() == 0

    def test_count_after_inserts(self, tmp_path):
        db = _make_db(tmp_path)
        db.record("q1", "d1")
        db.record("q2", "d2")
        db.record("q3", "d3")
        assert db.count() == 3

    def test_all_returns_all_records(self, tmp_path):
        db = _make_db(tmp_path)
        db.record("q1", "d1")
        db.record("q2", "d2")
        records = db.all()
        assert len(records) == 2

    def test_get_by_id(self, tmp_path):
        db = _make_db(tmp_path)
        record_id = db.record("find me", "the answer")
        found = db.get(record_id)
        assert found is not None
        assert found.decision == "the answer"
        assert found.id == record_id

    def test_get_missing_id_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get("dec_doesnotexist") is None

    def test_delete_removes_record(self, tmp_path):
        db = _make_db(tmp_path)
        record_id = db.record("to delete", "d")
        assert db.delete(record_id) is True
        assert db.get(record_id) is None
        assert db.count() == 0

    def test_delete_missing_returns_false(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.delete("dec_missing") is False

    def test_clear_empties_store(self, tmp_path):
        db = _make_db(tmp_path)
        db.record("q1", "d1")
        db.record("q2", "d2")
        db.clear()
        assert db.count() == 0


class TestDecisionMemoryMutations:
    def test_update_confidence(self, tmp_path):
        db = _make_db(tmp_path)
        record_id = db.record("q", "d", confidence=0.5)
        assert db.update_confidence(record_id, 0.95) is True
        updated = db.get(record_id)
        assert abs(updated.confidence - 0.95) < 1e-9

    def test_update_confidence_missing_id_returns_false(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.update_confidence("dec_none", 0.5) is False

    def test_record_outcome_success_boosts_confidence(self, tmp_path):
        db = _make_db(tmp_path)
        record_id = db.record("q", "d", confidence=0.5)
        db.record_outcome(record_id, outcome="worked", success=True)
        updated = db.get(record_id)
        assert updated.confidence > 0.5
        assert updated.success is True

    def test_record_outcome_failure_lowers_confidence(self, tmp_path):
        db = _make_db(tmp_path)
        record_id = db.record("q", "d", confidence=0.5)
        db.record_outcome(record_id, outcome="failed", success=False)
        updated = db.get(record_id)
        assert updated.confidence < 0.5
        assert updated.success is False

    def test_record_outcome_missing_id_returns_false(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.record_outcome("dec_gone", "x", success=True) is False

    def test_retrieve_requires_query_or_hash(self, tmp_path):
        db = _make_db(tmp_path)
        with pytest.raises(ValueError):
            db.retrieve()  # neither query nor query_hash
