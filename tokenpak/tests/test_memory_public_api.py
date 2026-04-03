"""Integration tests for tokenpak.agent.memory public API."""

import pytest
from tokenpak.agent.memory import (
    REQUIRED_CAPSULE_SECTIONS,
    build_session_capsule,
    capsule_retrieval_score,
    score_capsule_sections,
    serialize_capsule,
    DecisionMemoryDB,
    DecisionRecord,
)


class TestMemoryPublicAPIImports:
    """Verify all public API symbols are importable and usable."""

    def test_required_capsule_sections_constant(self):
        """REQUIRED_CAPSULE_SECTIONS is a tuple/list of section names."""
        assert REQUIRED_CAPSULE_SECTIONS is not None
        assert isinstance(REQUIRED_CAPSULE_SECTIONS, (list, tuple))
        assert len(REQUIRED_CAPSULE_SECTIONS) > 0

    def test_build_session_capsule_callable(self):
        """build_session_capsule is callable."""
        assert callable(build_session_capsule)

    def test_capsule_retrieval_score_callable(self):
        """capsule_retrieval_score is callable."""
        assert callable(capsule_retrieval_score)

    def test_score_capsule_sections_callable(self):
        """score_capsule_sections is callable."""
        assert callable(score_capsule_sections)

    def test_serialize_capsule_callable(self):
        """serialize_capsule is callable."""
        assert callable(serialize_capsule)

    def test_decision_memory_db_class(self):
        """DecisionMemoryDB is a class."""
        assert isinstance(DecisionMemoryDB, type)

    def test_decision_record_class(self):
        """DecisionRecord is a class."""
        assert isinstance(DecisionRecord, type)


class TestMemoryCapsuleRoundTrip:
    """Test building, serializing, and deserializing capsules."""

    def test_build_capsule_basic(self):
        """Build a basic session capsule from markdown text."""
        raw_text = """---
session_id: test-001
---

## Decisions Made
- Decision 1: Use BM25 for search

## Artifacts Created
- artifact.json: search configuration

## Action Items
- Review search results

## Insights
- Discovered BM25 is faster
"""
        capsule = build_session_capsule(raw_text, source_path="test.md")
        assert capsule is not None
        assert isinstance(capsule, dict)
        assert "decisions_made" in capsule

    def test_serialize_capsule_basic(self):
        """Serialize a capsule to JSON string."""
        raw_text = """---
version: 1.0
---

## Decisions Made
- Use Anthropic API

## Artifacts Created
- api_client.py
"""
        capsule = build_session_capsule(raw_text, source_path="api_test.md")
        serialized = serialize_capsule(capsule)
        assert serialized is not None
        assert isinstance(serialized, str)
        # Verify it's valid JSON
        import json
        parsed = json.loads(serialized)
        assert parsed is not None

    def test_score_capsule_with_scoring_function(self):
        """Score capsule sections."""
        raw_text = """## Decisions Made
- Decision A
- Decision B

## Artifacts Created
- file1.py
- file2.py

## Action Items
- Item 1
"""
        capsule = build_session_capsule(raw_text)
        scores = score_capsule_sections(capsule)
        assert isinstance(scores, dict)
        assert "decisions_made" in scores
        assert all(isinstance(v, (int, float)) for v in scores.values())


class TestDecisionMemoryRoundTrip:
    """Test creating, storing, and retrieving decision records."""

    def test_decision_record_creation(self):
        """Create a DecisionRecord with required fields."""
        record = DecisionRecord(
            id="rec-001",
            query_hash="abc123",
            query="test query",
            decision="test decision",
            confidence=0.85,
            timestamp="2026-03-27T00:00:00Z",
        )
        assert record is not None
        assert record.id == "rec-001"
        assert record.confidence == 0.85

    def test_decision_record_with_outcome(self):
        """Create a DecisionRecord with outcome."""
        record = DecisionRecord(
            id="rec-002",
            query_hash="def456",
            query="another query",
            decision="another decision",
            confidence=0.75,
            timestamp="2026-03-27T01:00:00Z",
            outcome="success",
            success=True,
        )
        assert record.outcome == "success"
        assert record.success is True

    def test_decision_memory_db_creation(self):
        """Instantiate DecisionMemoryDB."""
        db = DecisionMemoryDB()
        assert db is not None
        assert hasattr(db, "db_path")

    def test_decision_memory_db_has_methods(self):
        """Verify DecisionMemoryDB has expected methods."""
        db = DecisionMemoryDB()
        assert hasattr(db, "record") or hasattr(db, "store")
        assert hasattr(db, "retrieve") or hasattr(db, "query")


class TestDecisionMemoryDBStoreRetrieve:
    """Full store → retrieve round-trip using DecisionMemoryDB."""

    def test_store_and_retrieve_by_query(self, tmp_path):
        """Record a decision then retrieve it by the original query."""
        db = DecisionMemoryDB(db_path=str(tmp_path / "test_memory.db"))
        record_id = db.record(
            query="Should we use BM25?",
            decision="Yes, for <10K blocks",
            confidence=0.8,
        )
        assert record_id is not None

        results = db.retrieve(query="Should we use BM25?", top_k=5)
        assert len(results) >= 1
        assert results[0].decision == "Yes, for <10K blocks"
        assert abs(results[0].confidence - 0.8) < 0.01

    def test_store_retrieve_update_outcome(self, tmp_path):
        """Record → retrieve → record outcome, confidence adjusts."""
        db = DecisionMemoryDB(db_path=str(tmp_path / "test_outcome.db"))
        record_id = db.record(
            query="Use streaming response?",
            decision="Yes, for large payloads",
            confidence=0.7,
        )

        db.record_outcome(record_id, outcome="worked well", success=True)
        updated = db.get(record_id)
        assert updated is not None
        assert updated.success is True
        # Confidence should have increased after success
        assert updated.confidence >= 0.7

    def test_capsule_retrieval_score_with_real_capsule(self):
        """capsule_retrieval_score boosts score for high-signal capsules."""
        raw_text = """## Decisions Made
- Decision A: Use async IO
- Decision B: Cache responses

## Artifacts Created
- async_client.py

## Action Items
- Review benchmarks
- Write docs

## Insights
- Async improves throughput by 3x
"""
        capsule = build_session_capsule(raw_text)
        base_score = 5.0
        boosted = capsule_retrieval_score(base_score, capsule)
        assert boosted >= base_score, "High-signal capsule should boost score"

    def test_capsule_round_trip_serialize_deserialize(self):
        """Build → serialize → JSON.loads → verify fields survive."""
        import json
        raw_text = """---
session_id: rt-001
---

## Decisions Made
- Final decision: deploy on Fridays only

## Artifacts Created
- deploy_policy.yaml

## Action Items
- Schedule deploy window

## Insights
- Friday deploys reduce rollback risk
"""
        capsule = build_session_capsule(raw_text, source_path="rt-001.md")
        serialized = serialize_capsule(capsule)
        deserialized = json.loads(serialized)

        assert deserialized["decisions_made"] == capsule["decisions_made"]
        assert deserialized["artifacts_created"] == capsule["artifacts_created"]
        assert deserialized["action_items"] == capsule["action_items"]
        assert deserialized["insights"] == capsule["insights"]
        assert "raw_transcript_reference" in deserialized


class TestMemoryPublicAPINoInternalLeakage:
    """Ensure public API doesn't expose internal implementation details."""

    def test_imports_cleanly(self):
        """Importing from public API doesn't raise errors."""
        from tokenpak.agent import memory
        assert memory is not None

    def test_all_exports_in_all(self):
        """__all__ matches actual exports."""
        from tokenpak import agent
        memory_module = agent.memory
        assert hasattr(memory_module, "__all__")
        all_items = memory_module.__all__
        for item in all_items:
            assert hasattr(memory_module, item), f"{item} listed in __all__ but not exported"
