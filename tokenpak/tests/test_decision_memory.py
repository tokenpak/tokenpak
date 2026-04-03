"""
Tests for DecisionMemoryDB.

Tests cover:
- Recording decisions
- Retrieving by query and query_hash
- Updating confidence
- Recording outcomes
- All CRUD operations
"""

import pytest
import tempfile
import os
from pathlib import Path
from tokenpak.agent.memory import DecisionMemoryDB, DecisionRecord


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    db = DecisionMemoryDB(db_path=db_path)
    yield db
    
    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


class TestDecisionMemoryDB:
    """Test suite for DecisionMemoryDB."""
    
    def test_init_creates_database(self, temp_db):
        """Test that initialization creates the database and schema."""
        assert temp_db.db_path.exists()
        assert temp_db.count() == 0
    
    def test_record_decision(self, temp_db):
        """Test recording a decision."""
        record_id = temp_db.record(
            query="Should we use BM25?",
            decision="Yes, for <10K blocks",
            confidence=0.8
        )
        
        assert record_id is not None
        assert record_id.startswith("dec_")
        assert temp_db.count() == 1
    
    def test_record_decision_defaults(self, temp_db):
        """Test recording with default confidence."""
        record_id = temp_db.record(
            query="Test query",
            decision="Test decision"
        )
        
        record = temp_db.get(record_id)
        assert record.confidence == 0.7  # default
    
    def test_record_decision_clamps_confidence(self, temp_db):
        """Test that confidence is clamped to [0.0, 1.0]."""
        # Over 1.0
        id1 = temp_db.record("q1", "d1", confidence=1.5)
        assert temp_db.get(id1).confidence == 1.0
        
        # Under 0.0
        id2 = temp_db.record("q2", "d2", confidence=-0.5)
        assert temp_db.get(id2).confidence == 0.0
    
    def test_retrieve_by_query(self, temp_db):
        """Test retrieving decisions by query."""
        id1 = temp_db.record("What is AI?", "It's machine learning", confidence=0.9)
        id2 = temp_db.record("What is AI?", "It's artificial intelligence", confidence=0.95)
        id3 = temp_db.record("What is ML?", "It's a subset of AI", confidence=0.8)
        
        # Retrieve for first query
        results = temp_db.retrieve(query="What is AI?", top_k=10)
        assert len(results) == 2
        assert results[0].confidence >= results[1].confidence  # sorted by confidence
    
    def test_retrieve_by_query_hash(self, temp_db):
        """Test retrieving by pre-computed query hash."""
        import hashlib
        
        query = "What is AI?"
        query_hash = hashlib.sha256(query.lower().encode()).hexdigest()
        
        id1 = temp_db.record(query, "Decision 1", confidence=0.9)
        
        results = temp_db.retrieve(query_hash=query_hash)
        assert len(results) == 1
        assert results[0].id == id1
    
    def test_retrieve_respects_top_k(self, temp_db):
        """Test that retrieve respects top_k limit."""
        query = "Test query"
        for i in range(10):
            temp_db.record(query, f"Decision {i}", confidence=0.5 + i * 0.01)
        
        results = temp_db.retrieve(query=query, top_k=3)
        assert len(results) == 3
    
    def test_retrieve_empty(self, temp_db):
        """Test retrieving for non-existent query."""
        import hashlib
        
        query = "This query has no records"
        query_hash = hashlib.sha256(query.lower().encode()).hexdigest()
        
        results = temp_db.retrieve(query_hash=query_hash)
        assert len(results) == 0
    
    def test_update_confidence(self, temp_db):
        """Test updating confidence."""
        record_id = temp_db.record("Test", "Decision", confidence=0.5)
        
        success = temp_db.update_confidence(record_id, 0.9)
        assert success is True
        assert temp_db.get(record_id).confidence == 0.9
    
    def test_update_confidence_nonexistent(self, temp_db):
        """Test updating non-existent record."""
        success = temp_db.update_confidence("fake_id", 0.9)
        assert success is False
    
    def test_record_outcome_success(self, temp_db):
        """Test recording a successful outcome."""
        record_id = temp_db.record("Test", "Decision", confidence=0.7)
        
        success = temp_db.record_outcome(
            record_id,
            outcome="Good result",
            success=True,
            notes="Worked as expected"
        )
        
        assert success is True
        record = temp_db.get(record_id)
        assert record.outcome == "Good result"
        assert record.success is True
        assert record.notes == "Worked as expected"
        assert record.confidence == 0.75  # increased by 0.05
    
    def test_record_outcome_failure(self, temp_db):
        """Test recording a failed outcome."""
        record_id = temp_db.record("Test", "Decision", confidence=0.7)
        
        success = temp_db.record_outcome(
            record_id,
            outcome="Bad result",
            success=False,
            notes="Didn't work"
        )
        
        assert success is True
        record = temp_db.get(record_id)
        assert record.success is False
        assert record.confidence == 0.6  # decreased by 0.1
    
    def test_get_record(self, temp_db):
        """Test retrieving a specific record by ID."""
        record_id = temp_db.record("Test", "Decision", confidence=0.8)
        
        record = temp_db.get(record_id)
        assert record is not None
        assert record.id == record_id
        assert record.decision == "Decision"
        assert record.confidence == 0.8
    
    def test_get_nonexistent(self, temp_db):
        """Test getting a non-existent record."""
        record = temp_db.get("fake_id")
        assert record is None
    
    def test_all_records(self, temp_db):
        """Test retrieving all records."""
        for i in range(5):
            temp_db.record(f"Query {i}", f"Decision {i}")
        
        all_records = temp_db.all()
        assert len(all_records) == 5
    
    def test_all_records_ordered(self, temp_db):
        """Test that all() respects order_by parameter."""
        import time
        
        id1 = temp_db.record("Q1", "D1")
        time.sleep(0.01)
        id2 = temp_db.record("Q2", "D2")
        
        results = temp_db.all(order_by="timestamp DESC")
        assert results[0].id == id2
        assert results[1].id == id1
    
    def test_count(self, temp_db):
        """Test record counting."""
        assert temp_db.count() == 0
        
        for i in range(5):
            temp_db.record(f"Query {i}", f"Decision {i}")
        
        assert temp_db.count() == 5
    
    def test_delete_record(self, temp_db):
        """Test deleting a record."""
        record_id = temp_db.record("Test", "Decision")
        assert temp_db.count() == 1
        
        success = temp_db.delete(record_id)
        assert success is True
        assert temp_db.count() == 0
        assert temp_db.get(record_id) is None
    
    def test_delete_nonexistent(self, temp_db):
        """Test deleting a non-existent record."""
        success = temp_db.delete("fake_id")
        assert success is False
    
    def test_clear(self, temp_db):
        """Test clearing all records."""
        for i in range(5):
            temp_db.record(f"Query {i}", f"Decision {i}")
        
        assert temp_db.count() == 5
        
        temp_db.clear()
        assert temp_db.count() == 0
    
    def test_decision_record_dataclass(self):
        """Test DecisionRecord dataclass."""
        record = DecisionRecord(
            id="dec_123",
            query_hash="hash123",
            query="Test query",
            decision="Test decision",
            confidence=0.8,
            timestamp="2026-03-27T00:00:00Z",
            outcome="Good",
            success=True,
            notes="Test note"
        )
        
        assert record.id == "dec_123"
        assert record.confidence == 0.8
        assert record.success is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
