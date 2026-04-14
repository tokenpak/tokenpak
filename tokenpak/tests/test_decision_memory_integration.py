"""
Integration tests for DecisionMemoryDB with real on-disk SQLite.

Tests verify:
- DB file creation and persistence
- Data survives across separate instances (simulated restarts)
- Learning loop (record_outcome) adjusts confidence correctly
- Edge cases: long queries, concurrent access
- Database integrity and schema
"""

import pytest
import sqlite3
import tempfile
import os
import time
import threading
from pathlib import Path
from tokenpak.companion.memory.decision_memory import DecisionMemoryDB, DecisionRecord


class TestDecisionMemoryIntegration:
    """Integration tests using real on-disk SQLite."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database file."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        # Cleanup
        if os.path.exists(path):
            os.remove(path)

    def test_db_file_created_in_expected_location(self, temp_db):
        """Test that DB file is created at the specified path."""
        # Remove the temp file first (mkstemp creates it empty)
        os.remove(temp_db)
        assert not os.path.exists(temp_db), "Temp DB should not exist"

        db = DecisionMemoryDB(db_path=temp_db)

        assert os.path.exists(temp_db), "DB file should be created"
        assert os.path.isfile(temp_db), "DB path should be a file"

    def test_db_schema_initialized_correctly(self, temp_db):
        """Test that schema is properly initialized."""
        db = DecisionMemoryDB(db_path=temp_db)

        # Verify schema exists
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            
            # Check decisions table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
            )
            assert cursor.fetchone() is not None, "decisions table should exist"

            # Check columns
            cursor.execute("PRAGMA table_info(decisions)")
            columns = {row[1] for row in cursor.fetchall()}
            expected = {
                "id", "query_hash", "query", "decision", "confidence",
                "timestamp", "outcome", "success", "notes"
            }
            assert expected == columns, "Schema columns should match"

            # Check indexes exist
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='decisions'"
            )
            indexes = {row[0] for row in cursor.fetchall()}
            assert "idx_query_hash" in indexes
            assert "idx_confidence" in indexes

    def test_persistence_across_instances(self, temp_db):
        """Test that records persist across separate DecisionMemoryDB instances."""
        # Write records in first instance
        db1 = DecisionMemoryDB(db_path=temp_db)
        db1.record(
            query="Should we use HTTP100?",
            decision="Yes, for SSE streams",
            confidence=0.85,
            notes="Prevents timeout"
        )
        db1.record(
            query="Max compression time?",
            decision="5000ms default",
            confidence=0.90
        )
        
        # Create new instance (simulating restart)
        db2 = DecisionMemoryDB(db_path=temp_db)
        
        # Retrieve records from second instance
        results = db2.retrieve(query="Should we use HTTP100?", top_k=1)
        
        assert len(results) == 1
        assert results[0].decision == "Yes, for SSE streams"
        assert results[0].confidence == 0.85
        assert results[0].notes == "Prevents timeout"

    def test_learning_loop_success_increases_confidence(self, temp_db):
        """Test that record_outcome() increases confidence on success."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        # Record initial decision
        record_id = db.record(
            query="Deploy proxy?",
            decision="Yes, to production",
            confidence=0.70
        )
        
        # Get initial confidence
        initial_results = db.retrieve(query="Deploy proxy?", top_k=1)
        initial_confidence = initial_results[0].confidence
        
        # Record successful outcome
        db.record_outcome(record_id, outcome="Deployment successful", success=True)
        
        # Check confidence increased
        updated_results = db.retrieve(query="Deploy proxy?", top_k=1)
        updated_confidence = updated_results[0].confidence
        
        assert updated_confidence > initial_confidence, "Success should increase confidence"
        assert abs(updated_confidence - initial_confidence) >= 0.04, "Increase should be ~5%"

    def test_learning_loop_failure_decreases_confidence(self, temp_db):
        """Test that record_outcome() decreases confidence on failure."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        # Record initial decision with high confidence
        record_id = db.record(
            query="Use aggressive mode?",
            decision="Yes, compress aggressively",
            confidence=0.95
        )
        
        # Get initial confidence
        initial_results = db.retrieve(query="Use aggressive mode?", top_k=1)
        initial_confidence = initial_results[0].confidence
        
        # Record failed outcome
        db.record_outcome(record_id, outcome="Compression failed", success=False)
        
        # Check confidence decreased
        updated_results = db.retrieve(query="Use aggressive mode?", top_k=1)
        updated_confidence = updated_results[0].confidence
        
        assert updated_confidence < initial_confidence, "Failure should decrease confidence"
        assert abs(updated_confidence - initial_confidence) >= 0.08, "Decrease should be ~10%"

    def test_retrieve_with_long_query_string(self, temp_db):
        """Test retrieve() handles very long query strings."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        # Create a very long query (4KB+)
        long_query = "What should we do? " * 200  # ~4000 chars
        
        # Record with long query
        record_id = db.record(
            query=long_query,
            decision="Process in chunks",
            confidence=0.80
        )
        
        # Retrieve using long query
        results = db.retrieve(query=long_query, top_k=5)
        
        assert len(results) > 0
        assert results[0].id == record_id
        assert results[0].decision == "Process in chunks"

    def test_concurrent_write_safety(self, temp_db):
        """Test that concurrent writes don't corrupt the database."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        errors = []
        
        def writer_thread(thread_id):
            try:
                for i in range(10):
                    db.record(
                        query=f"Query from thread {thread_id} iter {i}",
                        decision=f"Decision {thread_id}-{i}",
                        confidence=0.5 + (i / 100)
                    )
            except Exception as e:
                errors.append(e)
        
        # Spawn 3 threads writing concurrently
        threads = [threading.Thread(target=writer_thread, args=(i,)) for i in range(3)]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        assert len(errors) == 0, f"No errors during concurrent writes: {errors}"
        
        # Verify all records were written
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM decisions")
            count = cursor.fetchone()[0]
            assert count == 30, "All 30 records should be persisted"

    def test_update_confidence_persists(self, temp_db):
        """Test that update_confidence() changes are persisted."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        # Record initial decision
        record_id = db.record(
            query="Test query",
            decision="Test decision",
            confidence=0.50
        )
        
        # Update confidence
        new_confidence = 0.92
        db.update_confidence(record_id, new_confidence=new_confidence)
        
        # Create new instance and verify change persisted
        db2 = DecisionMemoryDB(db_path=temp_db)
        results = db2.retrieve(query="Test query", top_k=1)
        
        assert results[0].confidence == new_confidence

    def test_record_count_and_retrieval_statistics(self, temp_db):
        """Test database record count and retrieval statistics."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        # Record some decisions with varying confidence
        confidences = [0.5, 0.6, 0.7, 0.8, 0.9]
        for i, conf in enumerate(confidences):
            db.record(
                query=f"Query {i}",
                decision=f"Decision {i}",
                confidence=conf
            )
        
        # Verify count by querying database
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM decisions")
            total = cursor.fetchone()[0]
            assert total == 5
            
            cursor.execute("SELECT AVG(confidence) FROM decisions")
            avg_conf = cursor.fetchone()[0]
            assert 0.0 <= avg_conf <= 1.0
            assert abs(avg_conf - 0.7) < 0.01  # Average of [0.5, 0.6, 0.7, 0.8, 0.9]

    def test_empty_db_retrieve_returns_empty(self, temp_db):
        """Test that retrieve() on empty DB returns empty list."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        results = db.retrieve(query="nonexistent query", top_k=5)
        
        assert results == []

    def test_retrieve_respects_top_k_limit(self, temp_db):
        """Test that retrieve() respects the top_k parameter."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        # Record 10 decisions with same query but different confidence
        query = "Multi-record query"
        for i in range(10):
            db.record(
                query=query,
                decision=f"Decision {i}",
                confidence=0.5 + (i * 0.04)  # 0.5 to 0.86
            )
        
        # Retrieve with different top_k values
        results_3 = db.retrieve(query=query, top_k=3)
        results_5 = db.retrieve(query=query, top_k=5)
        results_all = db.retrieve(query=query, top_k=100)
        
        assert len(results_3) == 3
        assert len(results_5) == 5
        assert len(results_all) == 10
        
        # Verify sorted by confidence (descending)
        confidences = [r.confidence for r in results_all]
        assert confidences == sorted(confidences, reverse=True)

    def test_decision_record_dataclass_valid(self, temp_db):
        """Test that DecisionRecord dataclass is properly populated."""
        db = DecisionMemoryDB(db_path=temp_db)
        
        record_id = db.record(
            query="Dataclass test",
            decision="Test decision",
            confidence=0.75,
            notes="Test notes"
        )
        
        results = db.retrieve(query="Dataclass test", top_k=1)
        
        record = results[0]
        assert isinstance(record, DecisionRecord)
        assert record.id == record_id
        assert record.decision == "Test decision"
        assert record.confidence == 0.75
        assert record.notes == "Test notes"
        assert record.query_hash is not None
        assert record.timestamp is not None
        assert record.outcome is None  # Not recorded yet
        assert record.success is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
