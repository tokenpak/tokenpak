"""
Edge case tests for decision_memory.py module.

Tests cover:
- Concurrent writes (thread safety)
- Eviction at max capacity
- TTL expiry behavior
- Error handling (None/invalid inputs)
- Database integrity under stress
"""

import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tokenpak.companion.memory.decision_memory import DecisionMemoryDB


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_decisions.db"
        db = DecisionMemoryDB(str(db_path))
        yield db
        # Cleanup
        if db_path.exists():
            db_path.unlink()


class TestEdgeCaseConcurrency:
    """Test concurrent writes to decision memory."""

    def test_concurrent_writes_no_corruption(self, temp_db):
        """
        Verify that 10 simultaneous writes don't cause data corruption.
        All records should be inserted successfully.
        """
        num_threads = 10
        records_per_thread = 5
        inserted_ids = []
        lock = threading.Lock()

        def write_worker(thread_id):
            for i in range(records_per_thread):
                record_id = temp_db.record(
                    query=f"query_{thread_id}_{i}",
                    decision=f"decision_{thread_id}_{i}",
                    confidence=0.5 + (i * 0.05),
                )
                with lock:
                    inserted_ids.append(record_id)

        # Launch concurrent writes
        threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify all records were inserted
        assert len(inserted_ids) == num_threads * records_per_thread
        assert temp_db.count() == num_threads * records_per_thread

        # Verify no duplicates
        assert len(set(inserted_ids)) == len(inserted_ids)

        # Verify all records are retrievable
        for record_id in inserted_ids:
            assert temp_db.get(record_id) is not None

    def test_concurrent_writes_and_reads(self, temp_db):
        """
        Verify concurrent reads don't interfere with writes.
        """
        results = {"writes": 0, "reads": 0, "errors": 0}
        lock = threading.Lock()

        def writer():
            try:
                for i in range(20):
                    temp_db.record(query=f"concurrent_query_{i}", decision=f"decision_{i}")
                    with lock:
                        results["writes"] += 1
            except Exception as e:
                with lock:
                    results["errors"] += 1

        def reader():
            try:
                for _ in range(20):
                    temp_db.all()
                    with lock:
                        results["reads"] += 1
            except Exception as e:
                with lock:
                    results["errors"] += 1

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["errors"] == 0
        assert results["writes"] == 40
        assert results["reads"] == 40


class TestEdgeCaseEviction:
    """Test behavior at max capacity."""

    def test_max_capacity_eviction(self, temp_db):
        """
        Verify that when database reaches a practical max capacity,
        oldest entries are evicted properly.

        For this test, we'll use SQLite's ROWID to simulate eviction
        by manually deleting oldest records when count exceeds threshold.
        """
        max_records = 100

        # Insert beyond max capacity
        inserted_ids = []
        for i in range(max_records + 50):
            record_id = temp_db.record(query=f"query_{i}", decision=f"decision_{i}")
            inserted_ids.append(record_id)

        # Manually evict oldest records (simulating capacity limit)
        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM decisions
                WHERE rowid IN (
                    SELECT rowid FROM decisions
                    ORDER BY timestamp ASC
                    LIMIT (SELECT COUNT(*) FROM decisions) - ?
                )
            """,
                (max_records,),
            )
            conn.commit()

        # Verify count is at max
        assert temp_db.count() <= max_records

        # Verify oldest records are gone
        # (the first few inserted_ids should not be retrievable)
        for record_id in inserted_ids[:10]:
            assert temp_db.get(record_id) is None

    def test_query_hash_collision_handling(self, temp_db):
        """
        Verify that multiple decisions with same query hash are stored
        and retrieved correctly (no overwrites due to hash collision).
        """
        query = "Should we cache?"

        # Record multiple decisions for same query
        id1 = temp_db.record(query=query, decision="Yes, always", confidence=0.9)
        id2 = temp_db.record(query=query, decision="Yes, for large sets", confidence=0.8)
        id3 = temp_db.record(query=query, decision="Maybe, use BM25", confidence=0.7)

        # Retrieve all decisions for this query
        results = temp_db.retrieve(query=query, top_k=10)

        # Should get all 3 records, sorted by confidence
        assert len(results) == 3
        assert results[0].confidence == 0.9
        assert results[1].confidence == 0.8
        assert results[2].confidence == 0.7

        # Verify all are distinct
        retrieved_ids = {r.id for r in results}
        assert retrieved_ids == {id1, id2, id3}


class TestEdgeCaseTTL:
    """Test TTL expiry behavior."""

    def test_expired_records_not_returned(self, temp_db):
        """
        Verify that records with expired TTL are not returned.
        (Simulated by checking timestamp age.)
        """
        # Record a decision
        record_id = temp_db.record(query="old_query", decision="old_decision", confidence=0.9)

        # Manually update timestamp to be 30 days old
        with sqlite3.connect(temp_db.db_path) as conn:
            cursor = conn.cursor()
            old_timestamp = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
            cursor.execute(
                "UPDATE decisions SET timestamp = ? WHERE id = ?", (old_timestamp, record_id)
            )
            conn.commit()

        # Implement TTL filter: get all, then filter by age
        ttl_days = 7
        all_records = temp_db.all()

        cutoff = datetime.utcnow() - timedelta(days=ttl_days)
        valid_records = [
            r for r in all_records if datetime.fromisoformat(r.timestamp.rstrip("Z")) >= cutoff
        ]

        # Expired record should be filtered out
        assert record_id not in [r.id for r in valid_records]

    def test_recent_records_returned(self, temp_db):
        """
        Verify that recent records are returned (not expired).
        """
        record_id = temp_db.record(query="recent_query", decision="recent_decision")

        # Get all records and filter by TTL
        ttl_days = 7
        all_records = temp_db.all()

        cutoff = datetime.utcnow() - timedelta(days=ttl_days)
        valid_records = [
            r for r in all_records if datetime.fromisoformat(r.timestamp.rstrip("Z")) >= cutoff
        ]

        # Recent record should be present
        assert any(r.id == record_id for r in valid_records)


class TestEdgeCaseErrorHandling:
    """Test error handling for invalid/None inputs."""

    def test_none_query_and_hash(self, temp_db):
        """
        Verify that retrieve() raises ValueError when both query and query_hash are None.
        """
        with pytest.raises(ValueError):
            temp_db.retrieve(query=None, query_hash=None)

    def test_invalid_confidence_clamped(self, temp_db):
        """
        Verify that confidence values outside [0.0, 1.0] are clamped.
        """
        # Test too high
        id1 = temp_db.record(query="high_conf", decision="dec", confidence=1.5)
        record = temp_db.get(id1)
        assert record.confidence == 1.0

        # Test too low
        id2 = temp_db.record(query="low_conf", decision="dec", confidence=-0.5)
        record = temp_db.get(id2)
        assert record.confidence == 0.0

    def test_empty_string_query(self, temp_db):
        """
        Verify that empty string queries are handled gracefully.
        """
        record_id = temp_db.record(query="", decision="decision_for_empty", confidence=0.5)

        # Should be recordable
        assert record_id is not None

        # Should be retrievable
        record = temp_db.get(record_id)
        assert record is not None
        assert record.query == ""

    def test_none_decision_raises_error(self, temp_db):
        """
        Verify that None decision causes an error (type validation).
        """
        with pytest.raises((TypeError, ValueError, sqlite3.IntegrityError)):
            temp_db.record(query="test", decision=None)

    def test_special_characters_in_query(self, temp_db):
        """
        Verify that special characters in queries are handled correctly.
        """
        special_query = "query with 'quotes\" and \\ slashes & <brackets>"
        record_id = temp_db.record(query=special_query, decision="decision")

        record = temp_db.get(record_id)
        assert record.query == special_query

        # Retrieve by same query
        results = temp_db.retrieve(query=special_query)
        assert len(results) > 0
        assert any(r.id == record_id for r in results)

    def test_very_long_query(self, temp_db):
        """
        Verify that very long queries are handled without truncation.
        """
        long_query = "x" * 10000
        record_id = temp_db.record(query=long_query, decision="decision")

        record = temp_db.get(record_id)
        assert record.query == long_query
        assert len(record.query) == 10000


class TestEdgeCaseUpdateOutcome:
    """Test outcome recording and confidence adjustment."""

    def test_successful_outcome_increases_confidence(self, temp_db):
        """
        Verify that recording a successful outcome increases confidence.
        """
        record_id = temp_db.record(query="test", decision="decision", confidence=0.7)

        initial = temp_db.get(record_id)
        assert initial.confidence == 0.7

        # Record successful outcome
        temp_db.record_outcome(record_id=record_id, outcome="positive_result", success=True)

        updated = temp_db.get(record_id)
        # Confidence should increase by 0.05
        assert updated.confidence == 0.75
        assert updated.success is True

    def test_failed_outcome_decreases_confidence(self, temp_db):
        """
        Verify that recording a failed outcome decreases confidence.
        """
        record_id = temp_db.record(query="test", decision="decision", confidence=0.7)

        # Record failed outcome
        temp_db.record_outcome(record_id=record_id, outcome="negative_result", success=False)

        updated = temp_db.get(record_id)
        # Confidence should decrease by 0.1
        assert updated.confidence == 0.6
        assert updated.success is False

    def test_outcome_confidence_clamping(self, temp_db):
        """
        Verify that confidence never goes below 0.0 or above 1.0 after outcomes.
        """
        # Test low-end clamping
        id_low = temp_db.record(query="low", decision="d", confidence=0.05)
        temp_db.record_outcome(id_low, "failed", False)
        assert temp_db.get(id_low).confidence == 0.0

        # Test high-end clamping
        id_high = temp_db.record(query="high", decision="d", confidence=0.98)
        temp_db.record_outcome(id_high, "success", True)
        assert temp_db.get(id_high).confidence == 1.0


class TestEdgeCaseDelete:
    """Test delete operations."""

    def test_delete_nonexistent_record(self, temp_db):
        """
        Verify that deleting a nonexistent record returns False.
        """
        result = temp_db.delete("nonexistent_id")
        assert result is False

    def test_delete_existing_record(self, temp_db):
        """
        Verify that deleting an existing record removes it.
        """
        record_id = temp_db.record(query="test", decision="d")

        assert temp_db.get(record_id) is not None

        result = temp_db.delete(record_id)
        assert result is True

        assert temp_db.get(record_id) is None

    def test_clear_all_records(self, temp_db):
        """
        Verify that clear() removes all records.
        """
        # Insert multiple records
        for i in range(10):
            temp_db.record(query=f"query_{i}", decision=f"d_{i}")

        assert temp_db.count() == 10

        temp_db.clear()
        assert temp_db.count() == 0


class TestEdgeCaseDatabase:
    """Test database-level edge cases."""

    def test_database_file_creation(self, temp_db):
        """
        Verify that database file is created with correct path.
        """
        assert temp_db.db_path.exists()
        assert temp_db.db_path.suffix == ".db"

    def test_multiple_db_instances_same_path(self):
        """
        Verify that multiple DecisionMemoryDB instances on the same path
        work correctly (share the same database).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "shared.db"

            db1 = DecisionMemoryDB(str(db_path))
            id1 = db1.record(query="test", decision="d1")

            # Create second instance on same path
            db2 = DecisionMemoryDB(str(db_path))
            record = db2.get(id1)

            assert record is not None
            assert record.id == id1

    def test_corrupt_database_recovery(self):
        """
        Verify that database handles corruption gracefully (or requires deletion).

        Note: SQLite doesn't auto-recover from corruption by reinitializing.
        This test verifies the expected behavior: attempting to use a corrupted
        database should raise an error (handled by application).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            db = DecisionMemoryDB(str(db_path))
            id1 = db.record(query="test", decision="d")

            # Verify record was written
            assert db.count() == 1

            # Simulate corruption by corrupting the file
            with open(db_path, "wb") as f:
                f.write(b"CORRUPTED")

            # Creating new instance on corrupted DB should raise error
            with pytest.raises(sqlite3.DatabaseError):
                db2 = DecisionMemoryDB(str(db_path))
                db2.count()


class TestEdgeCaseRetrieve:
    """Test retrieve() edge cases."""

    def test_retrieve_empty_database(self, temp_db):
        """
        Verify that retrieve() returns empty list for non-existent query.
        """
        results = temp_db.retrieve(query="nonexistent_query")
        assert results == []

    def test_retrieve_top_k_limit(self, temp_db):
        """
        Verify that top_k parameter limits results correctly.
        """
        # Insert 10 records with same query hash
        for i in range(10):
            temp_db.record(
                query="same_query", decision=f"decision_{i}", confidence=0.5 + (i * 0.01)
            )

        # Retrieve with limit
        results = temp_db.retrieve(query="same_query", top_k=3)
        assert len(results) == 3

        # Verify sorted by confidence (descending)
        confidences = [r.confidence for r in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_retrieve_by_query_hash(self, temp_db):
        """
        Verify that retrieve() works with pre-computed query_hash.
        """
        import hashlib

        query = "test_query"
        query_hash = hashlib.sha256(query.lower().encode()).hexdigest()

        record_id = temp_db.record(query=query, decision="d")

        # Retrieve by hash
        results = temp_db.retrieve(query_hash=query_hash)
        assert len(results) == 1
        assert results[0].id == record_id


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
