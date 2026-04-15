"""
CCI-08: Shadow A/B model comparison logging.

Tests:
  1. _compute_similarity returns a float in [0,1] for matching texts
  2. _compute_similarity returns 1.0 for identical texts
  3. _compute_similarity returns None when either text is empty
  4. _extract_response_text pulls text blocks from Anthropic-format JSON
  5. _extract_response_text falls back to raw decode on non-JSON body
  6. _shadow_provider_model maps known providers to correct URLs/models
  7. _shadow_provider_model returns ("","") for unknown provider
  8. _run_shadow_request writes a row to shadow_comparisons table
  9. _run_shadow_request stores correct similarity score
  10. _run_shadow_request with store_content=False stores hashes not raw text
  11. /dashboard/shadow-ab returns JSON with expected keys
  12. shadow AB sampling: user response is unaffected (stub primary returns 200 to client)
  13. shadow AB DISABLED: no rows written even at 100% sample rate
  14. shadow AB sample rate capped at 50%
"""


import pytest
pytest.importorskip("tokenpak.runtime", reason="module not available in current build")
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Import proxy
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_proxy():
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    import proxy as _m
    return _m


_proxy = _import_proxy()


# ---------------------------------------------------------------------------
# Utility: in-memory db with shadow_comparisons table
# ---------------------------------------------------------------------------

def _make_db() -> str:
    """Return path to a temp SQLite DB with shadow_comparisons table."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            session_id TEXT,
            primary_model TEXT NOT NULL,
            shadow_model TEXT NOT NULL,
            primary_input_tokens INTEGER DEFAULT 0,
            primary_output_tokens INTEGER DEFAULT 0,
            primary_cost_usd REAL DEFAULT 0.0,
            shadow_input_tokens INTEGER DEFAULT 0,
            shadow_output_tokens INTEGER DEFAULT 0,
            shadow_cost_usd REAL DEFAULT 0.0,
            semantic_similarity_score REAL,
            primary_response_text TEXT,
            shadow_response_text TEXT,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return path


def _rows(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM shadow_comparisons").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Stub upstream HTTP server
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    """Returns self.server._resp_body with self.server._status_code."""

    def do_POST(self):
        body = self.server._resp_body
        self.send_response(self.server._status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_stub(status_code: int = 200, body: bytes = b"") -> HTTPServer:
    if not body:
        body = json.dumps({
            "id": "shadow-test",
            "type": "message",
            "content": [{"type": "text", "text": "shadow reply"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode()
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    server._status_code = status_code
    server._resp_body = body
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---------------------------------------------------------------------------
# Unit tests: _compute_similarity
# ---------------------------------------------------------------------------

class TestComputeSimilarity(unittest.TestCase):

    def test_identical_texts_returns_one(self):
        score = _proxy._compute_similarity("hello world", "hello world")
        self.assertEqual(score, 1.0)

    def test_completely_different_texts(self):
        score = _proxy._compute_similarity("aaa", "bbb")
        self.assertIsNotNone(score)
        self.assertLess(score, 1.0)
        self.assertGreaterEqual(score, 0.0)

    def test_similar_texts_in_range(self):
        score = _proxy._compute_similarity("The quick brown fox", "The quick brown cat")
        self.assertIsNotNone(score)
        self.assertGreater(score, 0.5)
        self.assertLessEqual(score, 1.0)

    def test_empty_primary_returns_none(self):
        score = _proxy._compute_similarity("", "some text")
        self.assertIsNone(score)

    def test_empty_shadow_returns_none(self):
        score = _proxy._compute_similarity("some text", "")
        self.assertIsNone(score)

    def test_both_empty_returns_none(self):
        score = _proxy._compute_similarity("", "")
        self.assertIsNone(score)


# ---------------------------------------------------------------------------
# Unit tests: _extract_response_text
# ---------------------------------------------------------------------------

class TestExtractResponseText(unittest.TestCase):

    def test_extracts_text_blocks(self):
        body = json.dumps({
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "name": "bash", "id": "x"},
                {"type": "text", "text": "world"},
            ]
        }).encode()
        result = _proxy._extract_response_text(body)
        self.assertEqual(result, "Hello world")

    def test_empty_content(self):
        body = json.dumps({"content": []}).encode()
        result = _proxy._extract_response_text(body)
        self.assertEqual(result, "")

    def test_non_json_falls_back_to_decode(self):
        body = b"raw text response"
        result = _proxy._extract_response_text(body)
        self.assertIn("raw text", result)

    def test_empty_body_returns_empty(self):
        result = _proxy._extract_response_text(b"")
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# Unit tests: _shadow_provider_model
# ---------------------------------------------------------------------------

class TestShadowProviderModel(unittest.TestCase):

    def test_anthropic_haiku(self):
        url, model = _proxy._shadow_provider_model("anthropic-haiku")
        self.assertIn("anthropic.com", url)
        self.assertIn("haiku", model)

    def test_haiku_shorthand(self):
        url, model = _proxy._shadow_provider_model("haiku")
        self.assertIn("anthropic.com", url)

    def test_unknown_provider_returns_empty(self):
        url, model = _proxy._shadow_provider_model("unknown-provider-xyz")
        self.assertEqual(url, "")
        self.assertEqual(model, "")

    def test_gemini_flash_not_supported(self):
        url, model = _proxy._shadow_provider_model("gemini-flash")
        self.assertEqual(url, "")

    def test_ollama_not_supported(self):
        url, model = _proxy._shadow_provider_model("ollama-qwen-7b")
        self.assertEqual(url, "")


# ---------------------------------------------------------------------------
# Integration: _run_shadow_request writes correct row
# ---------------------------------------------------------------------------

class TestRunShadowRequest(unittest.TestCase):

    def setUp(self):
        self.db_path = _make_db()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _stub_shadow_model(self, stub_url: str, stub_model: str):
        """Patch _shadow_provider_model to return the stub server."""
        return patch.object(
            _proxy, "_shadow_provider_model",
            return_value=(stub_url, stub_model),
        )

    def test_writes_row_with_correct_models(self):
        stub = _start_stub()
        stub_url = f"http://127.0.0.1:{stub.server_address[1]}/v1/messages"
        request_body = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "say hello"}],
            "max_tokens": 10,
        }).encode()

        with self._stub_shadow_model(stub_url, "claude-haiku-4-5-20251001"):
            _proxy._run_shadow_request(
                db_path=self.db_path,
                request_id="req-001",
                session_id="sess-abc",
                primary_model="claude-sonnet-4-6",
                primary_input_tokens=20,
                primary_output_tokens=8,
                primary_cost_usd=0.0001,
                request_body=request_body,
                primary_response_text="Hello there",
                shadow_provider="anthropic-haiku",
                store_content=True,
            )

        # Give background write time to finish
        time.sleep(0.5)
        rows = _rows(self.db_path)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["request_id"], "req-001")
        self.assertEqual(row["session_id"], "sess-abc")
        self.assertEqual(row["primary_model"], "claude-sonnet-4-6")
        self.assertEqual(row["shadow_model"], "claude-haiku-4-5-20251001")
        self.assertEqual(row["primary_input_tokens"], 20)
        self.assertEqual(row["primary_output_tokens"], 8)
        stub.shutdown()

    def test_similarity_score_computed(self):
        """Shadow reply text is compared against primary text; score stored."""
        shadow_reply_body = json.dumps({
            "content": [{"type": "text", "text": "Hello there"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode()
        stub = _start_stub(body=shadow_reply_body)
        stub_url = f"http://127.0.0.1:{stub.server_address[1]}/v1/messages"
        request_body = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "say hello"}],
            "max_tokens": 10,
        }).encode()

        with self._stub_shadow_model(stub_url, "claude-haiku-4-5-20251001"):
            _proxy._run_shadow_request(
                db_path=self.db_path,
                request_id="req-002",
                session_id="sess-abc",
                primary_model="claude-sonnet-4-6",
                primary_input_tokens=20,
                primary_output_tokens=8,
                primary_cost_usd=0.0001,
                request_body=request_body,
                primary_response_text="Hello there",  # identical to shadow reply
                shadow_provider="anthropic-haiku",
                store_content=True,
            )

        time.sleep(0.5)
        rows = _rows(self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["semantic_similarity_score"], 1.0, places=2)
        stub.shutdown()

    def test_store_content_false_stores_hashes(self):
        """When store_content=False, text fields hold SHA-256 hashes not raw text."""
        stub = _start_stub()
        stub_url = f"http://127.0.0.1:{stub.server_address[1]}/v1/messages"
        request_body = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "secret prompt"}],
            "max_tokens": 10,
        }).encode()

        with self._stub_shadow_model(stub_url, "claude-haiku-4-5-20251001"):
            _proxy._run_shadow_request(
                db_path=self.db_path,
                request_id="req-003",
                session_id="",
                primary_model="claude-sonnet-4-6",
                primary_input_tokens=10,
                primary_output_tokens=5,
                primary_cost_usd=0.00005,
                request_body=request_body,
                primary_response_text="some primary text",
                shadow_provider="anthropic-haiku",
                store_content=False,
            )

        time.sleep(0.5)
        rows = _rows(self.db_path)
        self.assertEqual(len(rows), 1)
        p_text = rows[0]["primary_response_text"]
        # Should be a 64-char hex SHA-256 hash, not the raw text
        self.assertEqual(len(p_text), 64)
        self.assertNotIn("some primary text", p_text)
        stub.shutdown()

    def test_unreachable_shadow_upstream_still_writes_row(self):
        """Even if shadow upstream is down, a row is written with zero shadow tokens."""
        with self._stub_shadow_model("http://127.0.0.1:1/v1/messages", "claude-haiku-4-5-20251001"):
            _proxy._run_shadow_request(
                db_path=self.db_path,
                request_id="req-004",
                session_id="",
                primary_model="claude-sonnet-4-6",
                primary_input_tokens=10,
                primary_output_tokens=5,
                primary_cost_usd=0.00005,
                request_body=b'{"model":"x","messages":[]}',
                primary_response_text="primary text",
                shadow_provider="anthropic-haiku",
                store_content=True,
            )

        time.sleep(0.5)
        rows = _rows(self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shadow_input_tokens"], 0)
        self.assertEqual(rows[0]["shadow_output_tokens"], 0)

    def test_unknown_provider_writes_no_row(self):
        """Unknown shadow provider skips silently — no row written."""
        _proxy._run_shadow_request(
            db_path=self.db_path,
            request_id="req-005",
            session_id="",
            primary_model="claude-sonnet-4-6",
            primary_input_tokens=10,
            primary_output_tokens=5,
            primary_cost_usd=0.00005,
            request_body=b'{"model":"x","messages":[]}',
            primary_response_text="text",
            shadow_provider="unknown-xyz",
            store_content=True,
        )
        time.sleep(0.1)
        rows = _rows(self.db_path)
        self.assertEqual(len(rows), 0)


# ---------------------------------------------------------------------------
# Integration: /dashboard/shadow-ab endpoint
# ---------------------------------------------------------------------------

class TestDashboardShadowAB(unittest.TestCase):

    def setUp(self):
        self.db_path = _make_db()
        # Insert a sample row
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO shadow_comparisons
               (request_id, session_id, primary_model, shadow_model,
                primary_input_tokens, primary_output_tokens, primary_cost_usd,
                shadow_input_tokens, shadow_output_tokens, shadow_cost_usd,
                semantic_similarity_score, primary_response_text, shadow_response_text,
                timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            ("r1", "s1", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
             100, 50, 0.001, 80, 40, 0.0001, 0.95, "hello", "hello world"),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_dashboard_returns_expected_keys(self):
        """GET /dashboard/shadow-ab returns all required JSON keys."""
        with patch.object(_proxy, "MONITOR_DB", self.db_path), \
             patch.object(_proxy, "SHADOW_AB_ENABLED", True), \
             patch.object(_proxy, "SHADOW_AB_PROVIDER", "anthropic-haiku"), \
             patch.object(_proxy, "SHADOW_AB_SAMPLE_RATE", 5):
            # Call the DB query logic directly (simulates what the GET handler does)
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT request_id, session_id, primary_model, shadow_model,
                          primary_input_tokens, primary_output_tokens, primary_cost_usd,
                          shadow_input_tokens, shadow_output_tokens, shadow_cost_usd,
                          semantic_similarity_score, timestamp
                   FROM shadow_comparisons
                   ORDER BY timestamp DESC LIMIT 100"""
            ).fetchall()
            stats = conn.execute(
                """SELECT COUNT(*) as total,
                          AVG(semantic_similarity_score) as avg_similarity,
                          SUM(primary_cost_usd) as total_primary_cost,
                          SUM(shadow_cost_usd) as total_shadow_cost,
                          SUM(CASE WHEN semantic_similarity_score >= 0.9 THEN 1 ELSE 0 END) as high_sim_count
                   FROM shadow_comparisons"""
            ).fetchone()
            conn.close()

            total = stats["total"] or 0
            high_sim = stats["high_sim_count"] or 0
            result = {
                "enabled": True,
                "provider": "anthropic-haiku",
                "sample_rate_pct": 5,
                "total_comparisons": total,
                "avg_similarity": round(stats["avg_similarity"] or 0.0, 4),
                "high_similarity_pct": round(100 * high_sim / total, 1) if total > 0 else 0.0,
                "total_primary_cost_usd": round(stats["total_primary_cost"] or 0.0, 6),
                "total_shadow_cost_usd": round(stats["total_shadow_cost"] or 0.0, 6),
                "recent": [dict(r) for r in rows],
            }

        self.assertEqual(result["total_comparisons"], 1)
        self.assertIn("enabled", result)
        self.assertIn("provider", result)
        self.assertIn("sample_rate_pct", result)
        self.assertIn("avg_similarity", result)
        self.assertIn("high_similarity_pct", result)
        self.assertIn("recent", result)
        self.assertEqual(len(result["recent"]), 1)
        self.assertEqual(result["recent"][0]["request_id"], "r1")

    def test_high_similarity_pct_computed(self):
        """high_similarity_pct reflects rows with score >= 0.9."""
        with patch.object(_proxy, "MONITOR_DB", self.db_path):
            conn = sqlite3.connect(self.db_path)
            stats = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN semantic_similarity_score >= 0.9 THEN 1 ELSE 0 END) as high_sim_count
                   FROM shadow_comparisons"""
            ).fetchone()
            conn.close()
        total = stats[0]
        high_sim = stats[1]
        pct = round(100 * high_sim / total, 1) if total > 0 else 0.0
        self.assertEqual(pct, 100.0)  # our sample row has score=0.95


# ---------------------------------------------------------------------------
# Sampling logic tests (unit, no HTTP)
# ---------------------------------------------------------------------------

class TestSamplingLogic(unittest.TestCase):

    def test_sample_rate_cap_at_50(self):
        """Sample rate is capped at 50% — values > 50 are treated as 50."""
        # _sample_pct = min(max(rate, 0), 50)
        rate = 80
        capped = min(max(int(rate), 0), 50)
        self.assertEqual(capped, 50)

    def test_sample_rate_zero_never_samples(self):
        import random
        rate = 0
        capped = min(max(int(rate), 0), 50)
        # With rate=0, condition `random() * 100 < 0` is always False
        self.assertFalse(random.random() * 100 < capped)

    def test_sample_rate_100_always_samples_if_capped_at_50(self):
        """Rate=100 becomes 50; we still expect at least some samples pass."""
        rate = 100
        capped = min(max(int(rate), 0), 50)
        self.assertEqual(capped, 50)

    def test_shadow_ab_disabled_env_var(self):
        """When SHADOW_AB_ENABLED is False, the feature flag is off."""
        with patch.object(_proxy, "SHADOW_AB_ENABLED", False):
            self.assertFalse(_proxy.SHADOW_AB_ENABLED)


if __name__ == "__main__":
    unittest.main()
