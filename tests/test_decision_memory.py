"""
Unit tests for Decision Memory Module
"""


import pytest

pytest.importorskip("tokenpak._internal.memory", reason="module not available in current build")
import os
import tempfile

import pytest

from tokenpak._internal.memory import DecisionMemoryDB, DecisionRecord


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_memory.db")
        db = DecisionMemoryDB(db_path=db_path)
        yield db
        # Cleanup
        if os.path.exists(db_path):
            os.remove(db_path)


class TestDecisionMemoryDB:
    """Test DecisionMemoryDB CRUD and search operations."""

    def test_record_basic(self, temp_db):
        """Test recording a basic decision."""
        record_id = temp_db.record(
            query="Should we use BM25?",
            decision="Yes, for <10K blocks",
            confidence=0.8
        )
        assert record_id is not None
        assert record_id.startswith("dec_")

    def test_record_with_defaults(self, temp_db):
        """Test recording with default confidence."""
        record_id = temp_db.record(
            query="Test query",
            decision="Test decision"
        )
        record = temp_db.get(record_id)
        assert record is not None
        assert record.confidence == 0.7  # default

    def test_get_existing_record(self, temp_db):
        """Test retrieving an existing record."""
        record_id = temp_db.record(
            query="Test query",
            decision="Test decision",
            confidence=0.85
        )
        record = temp_db.get(record_id)
        assert record is not None
        assert record.id == record_id
        assert record.decision == "Test decision"
        assert record.confidence == 0.85

    def test_get_nonexistent_record(self, temp_db):
        """Test retrieving a nonexistent record."""
        record = temp_db.get("nonexistent_id")
        assert record is None

    def test_retrieve_by_query(self, temp_db):
        """Test retrieving decisions by query."""
        query = "How do we search?"
        record_id_1 = temp_db.record(query=query, decision="BM25", confidence=0.8)
        record_id_2 = temp_db.record(query=query, decision="Embeddings", confidence=0.6)
        record_id_3 = temp_db.record(query="Different query", decision="Other", confidence=0.9)

        results = temp_db.retrieve(query=query)

        # Should get the two records for this query, sorted by confidence
        assert len(results) == 2
        assert results[0].confidence > results[1].confidence
        assert results[0].decision == "BM25"

    def test_retrieve_by_query_hash(self, temp_db):
        """Test retrieving by pre-computed query hash."""
        import hashlib
        query = "Test query"
        query_hash = hashlib.sha256(query.lower().encode()).hexdigest()

        record_id = temp_db.record(query=query, decision="Test decision", confidence=0.7)
        results = temp_db.retrieve(query_hash=query_hash)

        assert len(results) == 1
        assert results[0].id == record_id

    def test_retrieve_top_k(self, temp_db):
        """Test retrieve with top_k limit."""
        query = "Test"
        for i in range(10):
            temp_db.record(query=query, decision=f"Decision {i}", confidence=0.5)

        results = temp_db.retrieve(query=query, top_k=3)
        assert len(results) == 3

    def test_retrieve_empty_database(self, temp_db):
        """Test retrieve on empty database."""
        results = temp_db.retrieve(query="Nonexistent query")
        assert len(results) == 0

    def test_update_confidence(self, temp_db):
        """Test updating confidence."""
        record_id = temp_db.record(query="Test", decision="Test decision", confidence=0.5)
        success = temp_db.update_confidence(record_id, 0.9)
        assert success is True

        record = temp_db.get(record_id)
        assert record.confidence == 0.9

    def test_update_confidence_nonexistent(self, temp_db):
        """Test updating nonexistent record."""
        success = temp_db.update_confidence("nonexistent", 0.5)
        assert success is False

    def test_update_confidence_bounds(self, temp_db):
        """Test confidence bounding."""
        record_id = temp_db.record(query="Test", decision="Test decision", confidence=0.5)

        # Test upper bound
        temp_db.update_confidence(record_id, 1.5)
        record = temp_db.get(record_id)
        assert record.confidence == 1.0

        # Test lower bound
        temp_db.update_confidence(record_id, -0.5)
        record = temp_db.get(record_id)
        assert record.confidence == 0.0

    def test_record_outcome_success(self, temp_db):
        """Test recording a successful outcome."""
        record_id = temp_db.record(query="Test", decision="Test", confidence=0.7)

        success = temp_db.record_outcome(
            record_id=record_id,
            outcome="It worked!",
            success=True
        )
        assert success is True

        record = temp_db.get(record_id)
        assert record.outcome == "It worked!"
        assert record.success is True
        assert record.confidence == 0.75  # 0.7 + 0.05

    def test_record_outcome_failure(self, temp_db):
        """Test recording a failed outcome."""
        record_id = temp_db.record(query="Test", decision="Test", confidence=0.7)

        success = temp_db.record_outcome(
            record_id=record_id,
            outcome="It didn't work",
            success=False
        )
        assert success is True

        record = temp_db.get(record_id)
        assert record.outcome == "It didn't work"
        assert record.success is False
        assert record.confidence == 0.6  # 0.7 - 0.1

    def test_record_outcome_bounds(self, temp_db):
        """Test that record_outcome respects confidence bounds."""
        # Test upper bound
        record_id = temp_db.record(query="Test", decision="Test", confidence=0.98)
        temp_db.record_outcome(record_id, "Success", success=True)
        record = temp_db.get(record_id)
        assert record.confidence == 1.0  # bounded at 1.0

        # Test lower bound
        record_id = temp_db.record(query="Test", decision="Test", confidence=0.05)
        temp_db.record_outcome(record_id, "Failed", success=False)
        record = temp_db.get(record_id)
        assert record.confidence == 0.0  # bounded at 0.0

    def test_count(self, temp_db):
        """Test counting records."""
        assert temp_db.count() == 0

        for i in range(5):
            temp_db.record(query=f"Query {i}", decision=f"Decision {i}")

        assert temp_db.count() == 5

    def test_all(self, temp_db):
        """Test retrieving all records."""
        queries = ["Q1", "Q2", "Q3"]
        for q in queries:
            temp_db.record(query=q, decision=f"Decision for {q}")

        all_records = temp_db.all()
        assert len(all_records) == 3

    def test_delete(self, temp_db):
        """Test deleting a record."""
        record_id = temp_db.record(query="Test", decision="Test")
        assert temp_db.count() == 1

        success = temp_db.delete(record_id)
        assert success is True
        assert temp_db.count() == 0

    def test_delete_nonexistent(self, temp_db):
        """Test deleting nonexistent record."""
        success = temp_db.delete("nonexistent")
        assert success is False

    def test_clear(self, temp_db):
        """Test clearing all records."""
        for i in range(5):
            temp_db.record(query=f"Query {i}", decision=f"Decision {i}")

        assert temp_db.count() == 5
        temp_db.clear()
        assert temp_db.count() == 0

    def test_learning_loop(self, temp_db):
        """Test a complete learning loop."""
        # Record initial decision
        record_id = temp_db.record(
            query="Should we use BM25?",
            decision="Yes, for <10K blocks",
            confidence=0.7
        )

        # Simulate using the decision and getting good outcome
        temp_db.record_outcome(record_id, "It worked well", success=True)
        record = temp_db.get(record_id)
        assert abs(record.confidence - 0.75) < 0.001

        # Another good outcome
        temp_db.record_outcome(record_id, "Still working", success=True)
        record = temp_db.get(record_id)
        assert abs(record.confidence - 0.8) < 0.001

        # One bad outcome
        temp_db.record_outcome(record_id, "Failed once", success=False)
        record = temp_db.get(record_id)
        assert abs(record.confidence - 0.7) < 0.001

    def test_duplicate_queries(self, temp_db):
        """Test handling duplicate queries (same hash)."""
        query = "Should we use BM25?"

        # Record two different decisions for same query
        id1 = temp_db.record(query=query, decision="Yes", confidence=0.8)
        id2 = temp_db.record(query=query, decision="No", confidence=0.3)

        # Both should be retrievable by query
        results = temp_db.retrieve(query=query)
        assert len(results) == 2

        # Sorted by confidence
        assert results[0].confidence > results[1].confidence

    def test_confidence_validation(self, temp_db):
        """Test that confidence values are validated."""
        # Should clamp to 0.0-1.0
        record_id = temp_db.record(query="Test", decision="Test", confidence=1.5)
        record = temp_db.get(record_id)
        assert record.confidence == 1.0

        record_id = temp_db.record(query="Test", decision="Test", confidence=-0.5)
        record = temp_db.get(record_id)
        assert record.confidence == 0.0

    def test_persistence(self, temp_db):
        """Test that data persists across connections."""
        record_id = temp_db.record(query="Test", decision="Test", confidence=0.8)
        db_path = temp_db.db_path

        # Create new DB instance with same path
        db2 = DecisionMemoryDB(db_path=str(db_path))
        record = db2.get(record_id)

        assert record is not None
        assert record.decision == "Test"
        assert record.confidence == 0.8

    def test_notes_field(self, temp_db):
        """Test notes field in record."""
        record_id = temp_db.record(
            query="Test",
            decision="Test",
            notes="This is a test note"
        )
        record = temp_db.get(record_id)
        assert record.notes == "This is a test note"


class TestDecisionRecord:
    """Test DecisionRecord dataclass."""

    def test_record_creation(self):
        """Test creating a DecisionRecord."""
        record = DecisionRecord(
            id="test_id",
            query_hash="hash123",
            query="Test query",
            decision="Test decision",
            confidence=0.8,
            timestamp="2026-03-27T12:00:00Z"
        )
        assert record.id == "test_id"
        assert record.confidence == 0.8

    def test_record_with_outcome(self):
        """Test DecisionRecord with outcome."""
        record = DecisionRecord(
            id="test_id",
            query_hash="hash123",
            query="Test query",
            decision="Test decision",
            confidence=0.8,
            timestamp="2026-03-27T12:00:00Z",
            outcome="It worked",
            success=True
        )
        assert record.outcome == "It worked"
        assert record.success is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
