"""
Unit tests for tokenpak/routing_ledger.py.

Covers:
- RoutingLedger init + WAL mode
- log_transaction: returns row ID, persists fields
- record_outcome: updates accepted/rejection_reason, returns True/False
- get_transaction: retrieves by ID, returns None for missing
- get_recent: limit, ordering
- get_stats: totals, by_model, by_task_type, acceptance counts
- sample_count / acceptance_rate: with and without data
- _compute_context_weight: edge cases
- Concurrency: multiple threads logging simultaneously
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path

from tokenpak.routing_ledger import RoutingLedger


class TestRoutingLedgerInit(unittest.TestCase):

    def _make_ledger(self):
        tmp = tempfile.mktemp(suffix=".db")
        return RoutingLedger(db_path=tmp), tmp

    def test_creates_db_file(self):
        ledger, path = self._make_ledger()
        self.assertTrue(Path(path).exists())
        os.unlink(path)

    def test_wal_mode_active(self):
        ledger, path = self._make_ledger()
        self.assertTrue(ledger.wal_mode_active())
        os.unlink(path)


class TestLogTransaction(unittest.TestCase):

    def setUp(self):
        self._f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.ledger = RoutingLedger(db_path=self._f.name)

    def tearDown(self):
        os.unlink(self._f.name)

    def test_returns_row_id(self):
        row_id = self.ledger.log_transaction(
            model="claude-sonnet-4-6",
            query="What is 2+2?",
            context_blocks=[],
            response="4",
        )
        self.assertIsInstance(row_id, int)
        self.assertGreater(row_id, 0)

    def test_row_id_increments(self):
        id1 = self.ledger.log_transaction("m", "q1", [], "r1")
        id2 = self.ledger.log_transaction("m", "q2", [], "r2")
        self.assertGreater(id2, id1)

    def test_fields_persisted(self):
        row_id = self.ledger.log_transaction(
            model="claude-haiku-4-5",
            query="Hello",
            context_blocks=["block text"],
            response="Hi",
            accepted=True,
            latency_ms=123.4,
            context_tokens=100,
            response_tokens=10,
            routing_action="downgrade",
        )
        tx = self.ledger.get_transaction(row_id)
        self.assertEqual(tx["model_used"], "claude-haiku-4-5")
        self.assertEqual(tx["accepted"], 1)
        self.assertAlmostEqual(tx["latency_ms"], 123.4, places=1)
        self.assertEqual(tx["context_tokens"], 100)
        self.assertEqual(tx["routing_action"], "downgrade")

    def test_accepted_false_stored_as_zero(self):
        row_id = self.ledger.log_transaction("m", "q", [], "r", accepted=False)
        tx = self.ledger.get_transaction(row_id)
        self.assertEqual(tx["accepted"], 0)

    def test_accepted_none_stored_as_null(self):
        row_id = self.ledger.log_transaction("m", "q", [], "r", accepted=None)
        tx = self.ledger.get_transaction(row_id)
        self.assertIsNone(tx["accepted"])

    def test_query_preview_truncated_to_200(self):
        long_query = "x" * 500
        row_id = self.ledger.log_transaction("m", long_query, [], "r")
        tx = self.ledger.get_transaction(row_id)
        self.assertEqual(len(tx["query_preview"]), 200)

    def test_empty_query_safe(self):
        row_id = self.ledger.log_transaction("m", "", [], "")
        tx = self.ledger.get_transaction(row_id)
        self.assertIsNotNone(tx)

    def test_rejection_reason_stored(self):
        row_id = self.ledger.log_transaction(
            "m", "q", [], "r", accepted=False, rejection_reason="off-topic"
        )
        tx = self.ledger.get_transaction(row_id)
        self.assertEqual(tx["rejection_reason"], "off-topic")


class TestRecordOutcome(unittest.TestCase):

    def setUp(self):
        self._f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.ledger = RoutingLedger(db_path=self._f.name)
        self.row_id = self.ledger.log_transaction("m", "q", [], "r")

    def tearDown(self):
        os.unlink(self._f.name)

    def test_updates_accepted(self):
        self.ledger.record_outcome(self.row_id, accepted=True)
        tx = self.ledger.get_transaction(self.row_id)
        self.assertEqual(tx["accepted"], 1)

    def test_updates_rejection_reason(self):
        self.ledger.record_outcome(self.row_id, accepted=False, rejection_reason="wrong")
        tx = self.ledger.get_transaction(self.row_id)
        self.assertEqual(tx["rejection_reason"], "wrong")

    def test_returns_true_for_existing_row(self):
        result = self.ledger.record_outcome(self.row_id, accepted=True)
        self.assertTrue(result)

    def test_returns_false_for_missing_row(self):
        result = self.ledger.record_outcome(99999, accepted=True)
        self.assertFalse(result)


class TestGetTransaction(unittest.TestCase):

    def setUp(self):
        self._f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.ledger = RoutingLedger(db_path=self._f.name)

    def tearDown(self):
        os.unlink(self._f.name)

    def test_returns_dict(self):
        row_id = self.ledger.log_transaction("m", "q", [], "r")
        tx = self.ledger.get_transaction(row_id)
        self.assertIsInstance(tx, dict)

    def test_returns_none_for_missing(self):
        tx = self.ledger.get_transaction(99999)
        self.assertIsNone(tx)


class TestGetRecent(unittest.TestCase):

    def setUp(self):
        self._f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.ledger = RoutingLedger(db_path=self._f.name)
        for i in range(5):
            self.ledger.log_transaction("m", f"query {i}", [], "r")

    def tearDown(self):
        os.unlink(self._f.name)

    def test_returns_list(self):
        rows = self.ledger.get_recent()
        self.assertIsInstance(rows, list)

    def test_respects_limit(self):
        rows = self.ledger.get_recent(limit=3)
        self.assertEqual(len(rows), 3)

    def test_ordered_newest_first(self):
        rows = self.ledger.get_recent(limit=5)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_empty_ledger_returns_empty(self):
        f2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        empty = RoutingLedger(db_path=f2.name)
        self.assertEqual(empty.get_recent(), [])
        os.unlink(f2.name)


class TestGetStats(unittest.TestCase):

    def setUp(self):
        self._f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.ledger = RoutingLedger(db_path=self._f.name)

    def tearDown(self):
        os.unlink(self._f.name)

    def test_empty_stats(self):
        stats = self.ledger.get_stats()
        self.assertEqual(stats["total"], 0)

    def test_counts_accepted_rejected_unreviewed(self):
        self.ledger.log_transaction("m", "q", [], "r", accepted=True)
        self.ledger.log_transaction("m", "q", [], "r", accepted=False)
        self.ledger.log_transaction("m", "q", [], "r", accepted=None)
        stats = self.ledger.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["unreviewed"], 1)

    def test_by_model_breakdown(self):
        self.ledger.log_transaction("claude-sonnet-4-6", "q", [], "r")
        self.ledger.log_transaction("claude-haiku-4-5", "q", [], "r")
        stats = self.ledger.get_stats()
        self.assertIn("claude-sonnet-4-6", stats["by_model"])
        self.assertIn("claude-haiku-4-5", stats["by_model"])


class TestSampleCountAndAcceptanceRate(unittest.TestCase):

    def setUp(self):
        self._f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.ledger = RoutingLedger(db_path=self._f.name)

    def tearDown(self):
        os.unlink(self._f.name)

    def _log(self, model, accepted):
        row_id = self.ledger.log_transaction(model, "q", [], "r")
        self.ledger.record_outcome(row_id, accepted=accepted)

    def test_sample_count_zero_with_no_data(self):
        self.assertEqual(self.ledger.sample_count("m", "UNKNOWN"), 0)

    def test_acceptance_rate_zero_with_no_data(self):
        self.assertEqual(self.ledger.acceptance_rate("m", "UNKNOWN"), 0.0)

    def test_acceptance_rate_100_percent(self):
        for _ in range(3):
            self._log("claude-sonnet-4-6", accepted=True)
        # task_type is set by score_complexity — use whatever is returned
        stats = self.ledger.get_stats()
        task_type = list(stats["by_task_type"].keys())[0]
        rate = self.ledger.acceptance_rate("claude-sonnet-4-6", task_type)
        self.assertAlmostEqual(rate, 1.0)

    def test_acceptance_rate_zero_percent(self):
        self._log("claude-haiku-4-5", accepted=False)
        stats = self.ledger.get_stats()
        task_type = list(stats["by_task_type"].keys())[0]
        rate = self.ledger.acceptance_rate("claude-haiku-4-5", task_type)
        self.assertAlmostEqual(rate, 0.0)


class TestContextWeight(unittest.TestCase):

    def test_zero_tokens(self):
        w = RoutingLedger._compute_context_weight(0, 0)
        self.assertEqual(w, 0.0)

    def test_all_context(self):
        w = RoutingLedger._compute_context_weight(1000, 0)
        self.assertEqual(w, 1.0)

    def test_half_context(self):
        w = RoutingLedger._compute_context_weight(500, 500)
        self.assertEqual(w, 0.5)

    def test_rounded_to_4_places(self):
        w = RoutingLedger._compute_context_weight(1, 3)
        self.assertEqual(w, 0.25)


class TestConcurrency(unittest.TestCase):

    def test_concurrent_writes_safe(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        ledger = RoutingLedger(db_path=f.name)
        errors = []

        def worker():
            try:
                ledger.log_transaction("m", "q", [], "r")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent write errors: {errors}")
        stats = ledger.get_stats()
        self.assertEqual(stats["total"], 20)
        os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
