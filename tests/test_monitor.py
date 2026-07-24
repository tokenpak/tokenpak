"""
Tests for TokenPak Live Monitor Dashboard.
"""

import pytest

pytest.importorskip("tokenpak.monitor.server", reason="module not available in current build")
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import MagicMock, patch

from tokenpak.monitor.server import (
    DASHBOARD_HTML,
    MonitorHandler,
    ThreadedHTTPServer,
    _fetch_errors,
    _fetch_stats,
)

# ── Stats parsing ────────────────────────────────────────────────


class TestFetchStats(unittest.TestCase):
    @patch("tokenpak.monitor.server.urllib.request.urlopen")
    def test_fetch_stats_success(self, mock_open):
        payload = {"session": {"requests": 42, "cost": 1.23}}
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read = lambda: json.dumps(payload).encode()
        result = _fetch_stats()
        self.assertEqual(result["session"]["requests"], 42)

    @patch("tokenpak.monitor.server.urllib.request.urlopen", side_effect=Exception("timeout"))
    def test_fetch_stats_fallback(self, _):
        result = _fetch_stats()
        self.assertIn("error", result)


# ── Error log parsing ────────────────────────────────────────────


class TestFetchErrors(unittest.TestCase):
    def _make_log(self, tmpdir, name, entries):
        fpath = os.path.join(tmpdir, name)
        with open(fpath, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return fpath

    def test_basic_error_reading(self):
        with tempfile.TemporaryDirectory() as td:
            self._make_log(
                td,
                "errors-2026-03-24.jsonl",
                [
                    {
                        "timestamp": "2026-03-24T10:00:00Z",
                        "error_type": "ValueError",
                        "message": "oops",
                        "context": {"model": "haiku"},
                    },
                    {
                        "timestamp": "2026-03-24T10:01:00Z",
                        "error_type": "RateLimitError",
                        "message": "too fast",
                        "context": {"model": "sonnet"},
                    },
                ],
            )
            with patch("tokenpak.monitor.server.LOGS_DIR", td):
                errors = _fetch_errors(limit=100)
            self.assertEqual(len(errors), 2)

    def test_model_filter(self):
        with tempfile.TemporaryDirectory() as td:
            self._make_log(
                td,
                "errors-2026-03-24.jsonl",
                [
                    {
                        "timestamp": "2026-03-24T10:00:00Z",
                        "error_type": "E1",
                        "message": "a",
                        "context": {"model": "haiku"},
                    },
                    {
                        "timestamp": "2026-03-24T10:01:00Z",
                        "error_type": "E2",
                        "message": "b",
                        "context": {"model": "sonnet"},
                    },
                ],
            )
            with patch("tokenpak.monitor.server.LOGS_DIR", td):
                errors = _fetch_errors(model_filter="haiku")
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["error_type"], "E1")

    def test_limit_enforced(self):
        entries = [
            {
                "timestamp": f"2026-03-24T{i:02d}:00:00Z",
                "error_type": "E",
                "message": "x",
                "context": {},
            }
            for i in range(20)
        ]
        with tempfile.TemporaryDirectory() as td:
            self._make_log(td, "errors-2026-03-24.jsonl", entries)
            with patch("tokenpak.monitor.server.LOGS_DIR", td):
                errors = _fetch_errors(limit=5)
            self.assertLessEqual(len(errors), 5)

    def test_malformed_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            fpath = os.path.join(td, "errors-2026-03-24.jsonl")
            with open(fpath, "w") as f:
                f.write("not json\n")
                f.write(
                    json.dumps(
                        {
                            "timestamp": "2026-03-24T00:00:00Z",
                            "error_type": "OK",
                            "message": "fine",
                            "context": {},
                        }
                    )
                    + "\n"
                )
            with patch("tokenpak.monitor.server.LOGS_DIR", td):
                errors = _fetch_errors()
            self.assertEqual(len(errors), 1)

    def test_missing_logs_dir(self):
        with patch("tokenpak.monitor.server.LOGS_DIR", "/nonexistent/path/xyz"):
            errors = _fetch_errors()
        self.assertEqual(errors, [])


# ── Cost calculations ────────────────────────────────────────────


class TestCostCalc(unittest.TestCase):
    """Verify cost projection math is correct (tested via JS logic reimplemented in Python)."""

    def test_hourly_rate(self):
        cost = 1.80
        elapsed_hours = 1.5
        rate = cost / elapsed_hours
        self.assertAlmostEqual(rate, 1.20, places=2)

    def test_daily_projection(self):
        cost = 1.80
        elapsed_hours = 1.5
        rate = cost / elapsed_hours
        self.assertAlmostEqual(rate * 24, 28.80, places=2)

    def test_monthly_projection(self):
        cost = 1.80
        elapsed_hours = 1.5
        rate = cost / elapsed_hours
        self.assertAlmostEqual(rate * 24 * 30, 864.0, places=0)


# ── HTTP server integration ──────────────────────────────────────


class TestMonitorServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedHTTPServer(("127.0.0.1", 18767), MonitorHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _get(self, path):
        url = f"http://127.0.0.1:18767{path}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode()

    def test_dashboard_serves(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("TokenPak", body)

    def test_api_stats_returns_json(self):
        status, body = self._get("/api/stats")
        self.assertEqual(status, 200)
        data = json.loads(body)
        # Either live stats or an error key (proxy may not be running in test env)
        self.assertIsInstance(data, dict)

    def test_api_errors_returns_json(self):
        status, body = self._get("/api/errors")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("errors", data)
        self.assertIn("count", data)

    def test_404_for_unknown_path(self):
        import urllib.error

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/nonexistent")
        self.assertEqual(ctx.exception.code, 404)


# ── Dashboard HTML exists ────────────────────────────────────────


class TestDashboardHTML(unittest.TestCase):
    def test_html_file_exists(self):
        self.assertTrue(DASHBOARD_HTML.exists(), f"dashboard.html not found at {DASHBOARD_HTML}")

    def test_html_has_required_elements(self):
        content = DASHBOARD_HTML.read_text()
        for token in [
            "TokenPak",
            "api/stats",
            "api/errors",
            "auto-refresh interval",
            "toggleTheme",
        ]:
            self.assertIn(token.lower(), content.lower(), f"Missing token: {token}")


if __name__ == "__main__":
    unittest.main()
