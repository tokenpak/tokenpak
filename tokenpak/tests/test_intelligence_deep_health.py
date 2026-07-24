# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for tokenpak.proxy.intelligence.deep_health.

Covers:
- CheckResult.to_dict — field inclusion logic
- DeepHealthResult.to_dict / http_status
- check_database — exists / not found / size
- check_index — freshness, stale, not found
- check_memory — psutil path + /proc/meminfo fallback + thresholds
- check_disk — usage thresholds (ok / warning / error)
- _check_provider — success, 429, 401/403, other HTTP errors, network error, timeout, no key
- check_anthropic / check_openai — correct env var + URL wiring
- DeepHealthChecker — injectable mocks, overall status aggregation, get_checker singleton

No live network or filesystem calls — all external I/O is mocked.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, mock_open, patch

# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


class TestCheckResultToDict(unittest.TestCase):
    def setUp(self):
        from tokenpak.proxy.intelligence.deep_health import CheckResult

        self.CR = CheckResult

    def test_status_only(self):
        cr = self.CR(status="ok")
        d = cr.to_dict()
        self.assertEqual(d["status"], "ok")
        self.assertNotIn("latency_ms", d)
        self.assertNotIn("error", d)

    def test_with_latency(self):
        cr = self.CR(status="ok", latency_ms=12.3456)
        d = cr.to_dict()
        self.assertAlmostEqual(d["latency_ms"], 12.3, places=0)

    def test_latency_rounded(self):
        cr = self.CR(status="ok", latency_ms=99.999)
        d = cr.to_dict()
        self.assertEqual(d["latency_ms"], 100.0)

    def test_with_error(self):
        cr = self.CR(status="error", error="timeout")
        d = cr.to_dict()
        self.assertEqual(d["error"], "timeout")

    def test_no_error_key_when_no_error(self):
        cr = self.CR(status="ok")
        self.assertNotIn("error", cr.to_dict())

    def test_details_merged(self):
        cr = self.CR(status="ok", details={"percent": 42.0, "free_gb": 100.0})
        d = cr.to_dict()
        self.assertEqual(d["percent"], 42.0)
        self.assertEqual(d["free_gb"], 100.0)


# ---------------------------------------------------------------------------
# DeepHealthResult
# ---------------------------------------------------------------------------


class TestDeepHealthResult(unittest.TestCase):
    def _make(self, statuses):
        from tokenpak.proxy.intelligence.deep_health import CheckResult, DeepHealthResult

        checks = {name: CheckResult(status=s) for name, s in statuses.items()}
        worst = (
            "error"
            if "error" in statuses.values()
            else "degraded"
            if "warning" in statuses.values()
            else "ok"
        )
        return DeepHealthResult(status=worst, checks=checks, duration_ms=12.5)

    def test_http_status_ok_is_200(self):
        r = self._make({"a": "ok"})
        r.status = "ok"
        self.assertEqual(r.http_status, 200)

    def test_http_status_degraded_is_200(self):
        r = self._make({"a": "warning"})
        r.status = "degraded"
        self.assertEqual(r.http_status, 200)

    def test_http_status_error_is_503(self):
        r = self._make({"a": "error"})
        r.status = "error"
        self.assertEqual(r.http_status, 503)

    def test_to_dict_has_status(self):
        r = self._make({"a": "ok"})
        d = r.to_dict()
        self.assertIn("status", d)

    def test_to_dict_has_duration(self):
        r = self._make({"a": "ok"})
        d = r.to_dict()
        self.assertIn("duration_ms", d)

    def test_to_dict_has_checks(self):
        r = self._make({"db": "ok", "mem": "warning"})
        d = r.to_dict()
        self.assertIn("checks", d)
        self.assertIn("db", d["checks"])
        self.assertIn("mem", d["checks"])


# ---------------------------------------------------------------------------
# check_database
# ---------------------------------------------------------------------------


class TestCheckDatabase(unittest.TestCase):
    def test_file_exists_returns_ok(self):
        from tokenpak.proxy.intelligence.deep_health import check_database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(b"x" * 1024)
            tmp_path = f.name
        try:
            result = check_database(db_path=tmp_path)
            self.assertEqual(result.status, "ok")
        finally:
            os.unlink(tmp_path)

    def test_file_exists_reports_size(self):
        from tokenpak.proxy.intelligence.deep_health import check_database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(b"x" * (1024 * 1024))  # 1 MB
            tmp_path = f.name
        try:
            result = check_database(db_path=tmp_path)
            self.assertAlmostEqual(result.details["size_mb"], 1.0, places=1)
        finally:
            os.unlink(tmp_path)

    def test_file_missing_returns_error(self):
        from tokenpak.proxy.intelligence.deep_health import check_database

        result = check_database(db_path="/nonexistent/path/monitor.db")
        self.assertEqual(result.status, "error")
        self.assertIn("not_found", result.error)

    def test_path_included_in_ok_details(self):
        from tokenpak.proxy.intelligence.deep_health import check_database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            result = check_database(db_path=tmp_path)
            self.assertIn("path", result.details)
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# check_index
# ---------------------------------------------------------------------------


class TestCheckIndex(unittest.TestCase):
    def test_fresh_index_returns_ok(self):
        from tokenpak.proxy.intelligence.deep_health import check_index

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        # Set mtime to now (fresh)
        os.utime(tmp_path, (time.time(), time.time()))
        try:
            result = check_index(index_path=tmp_path, stale_hours=24.0)
            self.assertEqual(result.status, "ok")
        finally:
            os.unlink(tmp_path)

    def test_stale_index_returns_warning(self):
        from tokenpak.proxy.intelligence.deep_health import check_index

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        # Set mtime to 25 hours ago
        old_ts = time.time() - (25 * 3600)
        os.utime(tmp_path, (old_ts, old_ts))
        try:
            result = check_index(index_path=tmp_path, stale_hours=24.0)
            self.assertEqual(result.status, "warning")
            self.assertEqual(result.error, "stale")
        finally:
            os.unlink(tmp_path)

    def test_missing_index_returns_error(self):
        from tokenpak.proxy.intelligence.deep_health import check_index

        result = check_index(index_path="/nonexistent/pricing_index.json")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "index_not_found")

    def test_age_hours_in_details(self):
        from tokenpak.proxy.intelligence.deep_health import check_index

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        os.utime(tmp_path, (time.time(), time.time()))
        try:
            result = check_index(index_path=tmp_path)
            self.assertIn("age_hours", result.details)
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# check_memory
# ---------------------------------------------------------------------------


class TestCheckMemoryPsutil(unittest.TestCase):
    def test_ok_under_85_percent(self):
        from tokenpak.proxy.intelligence.deep_health import check_memory

        mock_vm = MagicMock()
        mock_vm.percent = 60.0
        with patch("psutil.virtual_memory", return_value=mock_vm):
            result = check_memory()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.details["percent"], 60.0)

    def test_warning_above_85_percent(self):
        from tokenpak.proxy.intelligence.deep_health import check_memory

        mock_vm = MagicMock()
        mock_vm.percent = 88.0
        with patch("psutil.virtual_memory", return_value=mock_vm):
            result = check_memory()
        self.assertEqual(result.status, "warning")

    def test_error_above_95_percent(self):
        from tokenpak.proxy.intelligence.deep_health import check_memory

        mock_vm = MagicMock()
        mock_vm.percent = 97.0
        with patch("psutil.virtual_memory", return_value=mock_vm):
            result = check_memory()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "oom_risk")


class TestCheckMemoryProcMeminfo(unittest.TestCase):
    """Test the /proc/meminfo fallback path by removing psutil from sys.modules."""

    def _mock_meminfo(self, total_kb, available_kb):
        content = f"MemTotal:       {total_kb} kB\nMemFree:        0 kB\nMemAvailable:  {available_kb} kB\n"
        return content

    def test_procmeminfo_ok(self):
        import sys

        from tokenpak.proxy.intelligence.deep_health import check_memory

        # 37.5% used — should be ok
        meminfo = self._mock_meminfo(total_kb=8_000_000, available_kb=5_000_000)
        with patch.dict(sys.modules, {"psutil": None}):
            with patch("builtins.open", mock_open(read_data=meminfo)):
                result = check_memory()
        self.assertEqual(result.status, "ok")

    def test_procmeminfo_warning(self):
        import sys

        from tokenpak.proxy.intelligence.deep_health import check_memory

        # 88% used
        meminfo = self._mock_meminfo(total_kb=10_000_000, available_kb=1_200_000)
        with patch.dict(sys.modules, {"psutil": None}):
            with patch("builtins.open", mock_open(read_data=meminfo)):
                result = check_memory()
        self.assertEqual(result.status, "warning")

    def test_procmeminfo_total_zero_returns_error(self):
        import sys

        from tokenpak.proxy.intelligence.deep_health import check_memory

        meminfo = "MemTotal:       0 kB\nMemAvailable:   0 kB\n"
        with patch.dict(sys.modules, {"psutil": None}):
            with patch("builtins.open", mock_open(read_data=meminfo)):
                result = check_memory()
        self.assertEqual(result.status, "error")


# ---------------------------------------------------------------------------
# check_disk
# ---------------------------------------------------------------------------


class TestCheckDisk(unittest.TestCase):
    def _usage(self, total, used):
        import collections

        DU = collections.namedtuple("DU", ["total", "used", "free"])
        return DU(total=total, used=used, free=total - used)

    def test_ok_under_80_percent(self):
        from tokenpak.proxy.intelligence.deep_health import check_disk

        with patch("shutil.disk_usage", return_value=self._usage(100_000, 50_000)):
            result = check_disk("/")
        self.assertEqual(result.status, "ok")

    def test_warning_above_80_percent(self):
        from tokenpak.proxy.intelligence.deep_health import check_disk

        with patch("shutil.disk_usage", return_value=self._usage(100_000, 82_000)):
            result = check_disk("/")
        self.assertEqual(result.status, "warning")

    def test_error_above_95_percent(self):
        from tokenpak.proxy.intelligence.deep_health import check_disk

        with patch("shutil.disk_usage", return_value=self._usage(100_000, 96_000)):
            result = check_disk("/")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "disk_full")

    def test_free_gb_in_details(self):
        from tokenpak.proxy.intelligence.deep_health import check_disk

        with patch("shutil.disk_usage", return_value=self._usage(100_000_000_000, 40_000_000_000)):
            result = check_disk("/")
        self.assertIn("free_gb", result.details)
        self.assertGreater(result.details["free_gb"], 0)

    def test_error_on_exception(self):
        from tokenpak.proxy.intelligence.deep_health import check_disk

        with patch("shutil.disk_usage", side_effect=OSError("no such path")):
            result = check_disk("/nonexistent_mount")
        self.assertEqual(result.status, "error")


# ---------------------------------------------------------------------------
# _check_provider
# ---------------------------------------------------------------------------


class TestCheckProvider(unittest.TestCase):
    def _run(self, env_key, env_val, urlopen_side_effect=None, urlopen_return=None):
        from tokenpak.proxy.intelligence import deep_health as dh

        env = {env_key: env_val} if env_val else {}
        with patch.dict(os.environ, env, clear=False):
            if urlopen_side_effect is not None:
                with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
                    return dh._check_provider(
                        name="test",
                        api_key_env=env_key,
                        probe_url="https://example.com/probe",
                        api_key_header="x-api-key",
                        timeout=5.0,
                    )
            elif urlopen_return is not None:
                with patch("urllib.request.urlopen", return_value=urlopen_return):
                    return dh._check_provider(
                        name="test",
                        api_key_env=env_key,
                        probe_url="https://example.com/probe",
                        api_key_header="x-api-key",
                        timeout=5.0,
                    )
            else:
                return dh._check_provider(
                    name="test",
                    api_key_env=env_key,
                    probe_url="https://example.com/probe",
                    api_key_header="x-api-key",
                    timeout=5.0,
                )

    def test_missing_api_key_returns_error(self):
        # Ensure env var is absent
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MISSING_KEY_XYZ", None)
            from tokenpak.proxy.intelligence.deep_health import _check_provider

            result = _check_provider("test", "MISSING_KEY_XYZ", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "api_key_not_configured")

    def test_successful_response_returns_ok(self):

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        from tokenpak.proxy.intelligence.deep_health import _check_provider

        with patch.dict(os.environ, {"TEST_KEY": "mykey"}):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = _check_provider("test", "TEST_KEY", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.latency_ms)

    def test_rate_limited_429_returns_warning(self):
        import urllib.error

        err = urllib.error.HTTPError(
            url="https://x.com", code=429, msg="Too Many", hdrs=None, fp=None
        )
        from tokenpak.proxy.intelligence.deep_health import _check_provider

        with patch.dict(os.environ, {"TEST_KEY": "mykey"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = _check_provider("test", "TEST_KEY", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "warning")
        self.assertEqual(result.error, "rate_limited")

    def test_auth_failed_401_returns_error(self):
        import urllib.error

        err = urllib.error.HTTPError(
            url="https://x.com", code=401, msg="Unauthorized", hdrs=None, fp=None
        )
        from tokenpak.proxy.intelligence.deep_health import _check_provider

        with patch.dict(os.environ, {"TEST_KEY": "mykey"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = _check_provider("test", "TEST_KEY", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "auth_failed")

    def test_auth_failed_403_returns_error(self):
        import urllib.error

        err = urllib.error.HTTPError(
            url="https://x.com", code=403, msg="Forbidden", hdrs=None, fp=None
        )
        from tokenpak.proxy.intelligence.deep_health import _check_provider

        with patch.dict(os.environ, {"TEST_KEY": "mykey"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = _check_provider("test", "TEST_KEY", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "auth_failed")

    def test_other_http_error_returns_error(self):
        import urllib.error

        err = urllib.error.HTTPError(
            url="https://x.com", code=500, msg="Server Error", hdrs=None, fp=None
        )
        from tokenpak.proxy.intelligence.deep_health import _check_provider

        with patch.dict(os.environ, {"TEST_KEY": "mykey"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = _check_provider("test", "TEST_KEY", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "error")
        self.assertIn("http_500", result.error)

    def test_url_error_returns_error(self):
        import urllib.error

        err = urllib.error.URLError(reason="Name or service not known")
        from tokenpak.proxy.intelligence.deep_health import _check_provider

        with patch.dict(os.environ, {"TEST_KEY": "mykey"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = _check_provider("test", "TEST_KEY", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "error")
        self.assertIn("network_error", result.error)

    def test_timeout_returns_error(self):
        from tokenpak.proxy.intelligence.deep_health import _check_provider

        with patch.dict(os.environ, {"TEST_KEY": "mykey"}):
            with patch("urllib.request.urlopen", side_effect=TimeoutError()):
                result = _check_provider("test", "TEST_KEY", "https://x.com", "x-api-key")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "timeout")


class TestCheckAnthropicOpenAIWiring(unittest.TestCase):
    """Verify check_anthropic / check_openai call _check_provider with correct args."""

    def test_check_anthropic_uses_correct_env(self):
        from tokenpak.proxy.intelligence import deep_health as dh

        captured = {}

        def fake_check_provider(name, api_key_env, probe_url, api_key_header, timeout=5.0):
            captured["api_key_env"] = api_key_env
            captured["probe_url"] = probe_url
            return dh.CheckResult(status="ok")

        with patch.object(dh, "_check_provider", side_effect=fake_check_provider):
            dh.check_anthropic()

        self.assertEqual(captured["api_key_env"], "ANTHROPIC_API_KEY")
        self.assertIn("anthropic.com", captured["probe_url"])

    def test_check_openai_uses_correct_env(self):
        from tokenpak.proxy.intelligence import deep_health as dh

        captured = {}

        def fake_check_provider(name, api_key_env, probe_url, api_key_header, timeout=5.0):
            captured["api_key_env"] = api_key_env
            captured["probe_url"] = probe_url
            return dh.CheckResult(status="ok")

        with patch.object(dh, "_check_provider", side_effect=fake_check_provider):
            dh.check_openai()

        self.assertEqual(captured["api_key_env"], "OPENAI_API_KEY")
        self.assertIn("openai.com", captured["probe_url"])


# ---------------------------------------------------------------------------
# DeepHealthChecker
# ---------------------------------------------------------------------------


def _ok():
    from tokenpak.proxy.intelligence.deep_health import CheckResult

    return CheckResult(status="ok")


def _warn():
    from tokenpak.proxy.intelligence.deep_health import CheckResult

    return CheckResult(status="warning", error="stale")


def _err():
    from tokenpak.proxy.intelligence.deep_health import CheckResult

    return CheckResult(status="error", error="not_found")


class TestDeepHealthCheckerInit(unittest.TestCase):
    def test_default_init(self):
        from tokenpak.proxy.intelligence.deep_health import DeepHealthChecker

        checker = DeepHealthChecker()
        self.assertIsNone(checker.db_path)
        self.assertIsNone(checker.index_path)
        self.assertEqual(checker.provider_timeout, 5.0)

    def test_custom_paths(self):
        from tokenpak.proxy.intelligence.deep_health import DeepHealthChecker

        checker = DeepHealthChecker(db_path="/tmp/test.db", index_path="/tmp/idx.json")
        self.assertEqual(checker.db_path, "/tmp/test.db")
        self.assertEqual(checker.index_path, "/tmp/idx.json")


class TestDeepHealthCheckerRun(unittest.TestCase):
    def _make_checker(
        self, anthropic_result, openai_result, db_result, index_result, memory_result, disk_result
    ):
        from tokenpak.proxy.intelligence.deep_health import DeepHealthChecker

        return DeepHealthChecker(
            _check_anthropic=lambda timeout: anthropic_result,
            _check_openai=lambda timeout: openai_result,
            _check_database=lambda db_path: db_result,
            _check_index=lambda index_path: index_result,
            _check_memory=lambda: memory_result,
            _check_disk=lambda: disk_result,
        )

    def test_all_ok_returns_ok_status(self):
        checker = self._make_checker(_ok(), _ok(), _ok(), _ok(), _ok(), _ok())
        result = checker.run()
        self.assertEqual(result.status, "ok")

    def test_one_warning_returns_degraded(self):
        checker = self._make_checker(_ok(), _ok(), _ok(), _warn(), _ok(), _ok())
        result = checker.run()
        self.assertEqual(result.status, "degraded")

    def test_one_error_returns_error(self):
        checker = self._make_checker(_ok(), _ok(), _err(), _ok(), _ok(), _ok())
        result = checker.run()
        self.assertEqual(result.status, "error")

    def test_error_overrides_warning(self):
        checker = self._make_checker(_warn(), _ok(), _err(), _ok(), _ok(), _ok())
        result = checker.run()
        self.assertEqual(result.status, "error")

    def test_result_has_all_check_keys(self):
        checker = self._make_checker(_ok(), _ok(), _ok(), _ok(), _ok(), _ok())
        result = checker.run()
        expected_keys = {"anthropic", "openai", "database", "index", "memory", "disk"}
        self.assertEqual(set(result.checks.keys()), expected_keys)

    def test_result_duration_positive(self):
        checker = self._make_checker(_ok(), _ok(), _ok(), _ok(), _ok(), _ok())
        result = checker.run()
        self.assertGreater(result.duration_ms, 0)

    def test_http_status_503_on_error(self):
        checker = self._make_checker(_ok(), _ok(), _err(), _ok(), _ok(), _ok())
        result = checker.run()
        self.assertEqual(result.http_status, 503)

    def test_http_status_200_on_degraded(self):
        checker = self._make_checker(_warn(), _ok(), _ok(), _ok(), _ok(), _ok())
        result = checker.run()
        self.assertEqual(result.http_status, 200)


class TestGetCheckerSingleton(unittest.TestCase):
    def test_get_checker_returns_instance(self):
        import tokenpak.proxy.intelligence.deep_health as dh

        # Reset singleton for clean test
        dh._checker = None
        checker = dh.get_checker()
        self.assertIsInstance(checker, dh.DeepHealthChecker)

    def test_get_checker_returns_same_instance(self):
        import tokenpak.proxy.intelligence.deep_health as dh

        dh._checker = None
        c1 = dh.get_checker()
        c2 = dh.get_checker()
        self.assertIs(c1, c2)


if __name__ == "__main__":
    unittest.main()
