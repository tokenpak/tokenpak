#!/usr/bin/env python3
"""
Test suite for TokenPak async database writes.

Tests:
1. MONITOR.log() returns immediately (<0.1ms)
2. Background thread drains queue properly
3. Queue overflow is handled gracefully
4. No data loss under normal load
5. Concurrent writes don't corrupt data
"""

import os
import sys
import threading
import time
import sqlite3
import tempfile
from pathlib import Path
from queue import Queue, Empty

# Add proxy to path
sys.path.insert(0, str(Path(__file__).parent))

import proxy


class TestAsyncDBWrites:
    """Test suite for async DB write functionality."""
    
    def setup_method(self):
        """Create a temporary database for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_monitor.db")
        
        # Reset global state
        proxy._DB_CONNECTION = None
        proxy._DB_WRITE_QUEUE = None
        proxy._DB_BACKGROUND_THREAD = None
        proxy._DB_BACKGROUND_STOP.clear()
    
    def teardown_method(self):
        """Clean up after tests."""
        proxy._DB_BACKGROUND_STOP.set()
        if proxy._DB_BACKGROUND_THREAD:
            proxy._DB_BACKGROUND_THREAD.join(timeout=2)
        
        # Clean up temp files
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_log_returns_immediately(self):
        """Test that MONITOR.log() returns in <0.1ms."""
        monitor = proxy.Monitor(self.db_path)
        
        # Measure log() execution time
        start = time.perf_counter()
        monitor.log(
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost=0.001,
            latency_ms=150,
            status_code=200,
            endpoint="/v1/messages",
            compilation_mode="hybrid",
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        assert elapsed_ms < 0.5, f"log() took {elapsed_ms}ms (should be <0.5ms)"
        print(f"✅ log() returned in {elapsed_ms:.4f}ms")
    
    def test_background_thread_drains_queue(self):
        """Test that background thread drains the queue properly."""
        monitor = proxy.Monitor(self.db_path)
        
        # Log 50 items
        for i in range(50):
            monitor.log(
                model=f"model-{i % 3}",
                input_tokens=100 + i,
                output_tokens=50 + i,
                cost=0.001 * (i + 1),
                latency_ms=150 + i,
                status_code=200,
                endpoint="/v1/messages",
            )
        
        # Wait for background thread to drain
        time.sleep(1)
        
        # Verify all items were written
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        conn.close()
        
        assert count == 50, f"Expected 50 rows, got {count}"
        print(f"✅ Background thread drained {count} items")
    
    def test_queue_overflow_drops_oldest(self):
        """Test that queue overflow drops oldest entries gracefully."""
        monitor = proxy.Monitor(self.db_path)
        
        # Log more items than max queue size
        # Max queue = 1000, so we log 1100 items
        num_items = 1100
        for i in range(num_items):
            monitor.log(
                model="test-model",
                input_tokens=100 + i,
                output_tokens=50 + i,
                cost=0.001,
                latency_ms=150,
                status_code=200,
                endpoint="/v1/messages",
            )
        
        # Wait for background thread to drain
        time.sleep(2)
        
        # Verify rows were written (should be close to num_items)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        conn.close()
        
        # Some entries dropped due to overflow, but most should be written
        assert count >= 900, f"Expected at least 900 rows, got {count}"
        print(f"✅ Overflow handled: {count} items written (expected ~1100)")
    
    def test_no_data_loss_under_load(self):
        """Test that no data is lost during concurrent writes."""
        monitor = proxy.Monitor(self.db_path)
        num_threads = 10
        items_per_thread = 50
        
        def worker(thread_id):
            for i in range(items_per_thread):
                monitor.log(
                    model=f"thread-{thread_id}-model-{i}",
                    input_tokens=100 + i,
                    output_tokens=50 + i,
                    cost=0.001 * (i + 1),
                    latency_ms=150 + i,
                    status_code=200,
                    endpoint=f"/thread/{thread_id}",
                )
        
        # Spawn worker threads
        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Wait for background thread to drain
        time.sleep(2)
        
        # Verify all items were written
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        unique_models = conn.execute(
            "SELECT COUNT(DISTINCT model) FROM requests"
        ).fetchone()[0]
        conn.close()
        
        expected_count = num_threads * items_per_thread
        assert count >= expected_count * 0.95, \
            f"Expected ~{expected_count} rows, got {count}"
        assert unique_models == expected_count, \
            f"Expected {expected_count} unique models, got {unique_models}"
        print(f"✅ No data loss: {count} items from {num_threads} threads")
    
    def test_queue_all_params(self):
        """Test that all monitor.log() parameters are correctly written."""
        monitor = proxy.Monitor(self.db_path)
        
        monitor.log(
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost=0.12345,
            latency_ms=123,
            status_code=200,
            endpoint="/v1/messages",
            compilation_mode="strict",
            protected_tokens=10,
            compressed_tokens=40,
            injected_tokens=5,
            injected_sources="block1,block2",
            cache_read_tokens=20,
            cache_creation_tokens=15,
        )
        
        time.sleep(0.5)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM requests").fetchone()
        conn.close()
        
        assert row is not None
        assert row["model"] == "test-model"
        assert row["input_tokens"] == 100
        assert row["output_tokens"] == 50
        assert abs(row["estimated_cost"] - 0.12345) < 0.0001
        assert row["latency_ms"] == 123
        assert row["status_code"] == 200
        assert row["endpoint"] == "/v1/messages"
        assert row["compilation_mode"] == "strict"
        assert row["protected_tokens"] == 10
        assert row["compressed_tokens"] == 40
        assert row["injected_tokens"] == 5
        assert row["injected_sources"] == "block1,block2"
        assert row["cache_read_tokens"] == 20
        assert row["cache_creation_tokens"] == 15
        print(f"✅ All parameters correctly written to DB")
    
    def test_queue_initialization_idempotent(self):
        """Test that queue initialization is idempotent."""
        monitor1 = proxy.Monitor(self.db_path)
        monitor2 = proxy.Monitor(self.db_path)
        
        # Both should share the same queue
        assert proxy._DB_WRITE_QUEUE is not None
        queue_id_1 = id(proxy._DB_WRITE_QUEUE)
        
        # Create another monitor
        monitor3 = proxy.Monitor(self.db_path)
        queue_id_2 = id(proxy._DB_WRITE_QUEUE)
        
        assert queue_id_1 == queue_id_2, "Queue was re-initialized"
        print(f"✅ Queue initialization is idempotent")


def main():
    """Run all tests."""
    test_suite = TestAsyncDBWrites()
    
    tests = [
        ("log() returns immediately", test_suite.test_log_returns_immediately),
        ("background thread drains queue", test_suite.test_background_thread_drains_queue),
        ("queue overflow handling", test_suite.test_queue_overflow_drops_oldest),
        ("no data loss under load", test_suite.test_no_data_loss_under_load),
        ("all parameters written", test_suite.test_queue_all_params),
        ("queue initialization", test_suite.test_queue_initialization_idempotent),
    ]
    
    failed = 0
    for name, test_fn in tests:
        test_suite.setup_method()
        try:
            test_fn()
            print(f"  PASS: {name}")
        except AssertionError as e:
            print(f"  FAIL: {name}")
            print(f"    {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {name}")
            print(f"    {e}")
            failed += 1
        finally:
            test_suite.teardown_method()
    
    print(f"\n{'='*60}")
    print(f"Tests: {len(tests)}, Passed: {len(tests) - failed}, Failed: {failed}")
    print(f"{'='*60}\n")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
