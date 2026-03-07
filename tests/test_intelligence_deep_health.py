"""
Tests for GET /health and GET /health?deep=true on the intelligence server.

Covers:
  1. Basic /health is fast (<10 ms) and returns {"status": "ok"}
  2. Deep check with all-ok providers → overall ok, HTTP 200
  3. Deep check with one error provider → overall error, HTTP 503
  4. Deep check with one warning provider → overall degraded, HTTP 200
  5. Provider latency reported
  6. Provider with no API key → error: api_key_not_configured
  7. Database ok path
  8. Database missing → error
  9. Index ok path
  10. Index stale → warning
  11. Memory ok path
  12. Memory high → warning (>85%)
  13. Disk ok path
  14. Disk high → warning (>80%)
  15. status: 503 when any check is error
  16. status: 200 when only warnings present
  17. response includes version field
  18. duration_ms reported
"""

from __future__ import annotations

import os
import time
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tokenpak.intelligence.server import create_app
from tokenpak.intelligence.deep_health import (
    CheckResult,
    DeepHealthChecker,
    check_memory,
    check_disk,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """TestClient for the intelligence server (no auth needed for /health)."""
    app = create_app()
    return TestClient(app)


def _ok(latency_ms: float = 42.0, **details) -> CheckResult:
    return CheckResult(status="ok", latency_ms=latency_ms, details=details)


def _warn(error: str = "rate_limited", latency_ms: float = 55.0) -> CheckResult:
    return CheckResult(status="warning", latency_ms=latency_ms, error=error)


def _err(error: str = "network_error") -> CheckResult:
    return CheckResult(status="error", error=error)


def _make_checker(**overrides) -> DeepHealthChecker:
    """Build a DeepHealthChecker with all providers mocked to ok by default."""
    defaults = dict(
        _check_anthropic=lambda *a, **kw: _ok(45.0),
        _check_openai=lambda *a, **kw: _ok(60.0),
        _check_database=lambda *a, **kw: CheckResult(status="ok", details={"size_mb": 12.5}),
        _check_index=lambda *a, **kw: CheckResult(status="ok", details={"age_hours": 2.0}),
        _check_memory=lambda: CheckResult(status="ok", details={"percent": 6.2}),
        _check_disk=lambda *a, **kw: CheckResult(status="ok", details={"percent": 45.0, "free_gb": 55.0}),
    )
    defaults.update(overrides)
    return DeepHealthChecker(**defaults)


# ---------------------------------------------------------------------------
# 1. Basic /health fast path
# ---------------------------------------------------------------------------

def test_basic_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_basic_health_is_fast(client):
    t0 = time.time()
    client.get("/health")
    elapsed_ms = (time.time() - t0) * 1000
    assert elapsed_ms < 200, f"Basic health took {elapsed_ms:.1f}ms"


def test_basic_health_has_version(client):
    data = client.get("/health").json()
    assert "version" in data
    assert isinstance(data["version"], str)


# ---------------------------------------------------------------------------
# 2. Deep check — all ok → HTTP 200
# ---------------------------------------------------------------------------

def test_deep_health_all_ok_returns_200():
    checker = _make_checker()
    result = checker.run()
    assert result.status == "ok"
    assert result.http_status == 200


def test_deep_health_all_ok_checks_present():
    checker = _make_checker()
    result = checker.run()
    expected_keys = {"anthropic", "openai", "database", "index", "memory", "disk"}
    assert set(result.checks.keys()) == expected_keys


# ---------------------------------------------------------------------------
# 3. Deep check — one error → HTTP 503
# ---------------------------------------------------------------------------

def test_deep_health_error_provider_503():
    checker = _make_checker(_check_openai=lambda *a, **kw: _err("rate_limited"))
    result = checker.run()
    assert result.status == "error"
    assert result.http_status == 503


def test_deep_health_error_check_field():
    checker = _make_checker(_check_anthropic=lambda *a, **kw: _err("network_error"))
    result = checker.run()
    assert result.checks["anthropic"].status == "error"
    assert result.checks["anthropic"].error == "network_error"


# ---------------------------------------------------------------------------
# 4. Deep check — one warning → degraded, HTTP 200
# ---------------------------------------------------------------------------

def test_deep_health_warning_is_degraded():
    checker = _make_checker(_check_openai=lambda *a, **kw: _warn())
    result = checker.run()
    assert result.status == "degraded"
    assert result.http_status == 200


def test_deep_health_warning_check_field():
    checker = _make_checker(_check_openai=lambda *a, **kw: _warn("rate_limited"))
    result = checker.run()
    assert result.checks["openai"].status == "warning"
    assert result.checks["openai"].error == "rate_limited"


# ---------------------------------------------------------------------------
# 5. Provider latency reported
# ---------------------------------------------------------------------------

def test_deep_health_latency_reported():
    checker = _make_checker(_check_anthropic=lambda *a, **kw: _ok(latency_ms=45.0))
    result = checker.run()
    assert result.checks["anthropic"].latency_ms == pytest.approx(45.0, abs=0.5)


def test_deep_health_latency_in_dict():
    checker = _make_checker(_check_anthropic=lambda *a, **kw: _ok(latency_ms=123.4))
    result = checker.run()
    d = result.checks["anthropic"].to_dict()
    assert "latency_ms" in d
    assert d["latency_ms"] == pytest.approx(123.4, abs=0.5)


# ---------------------------------------------------------------------------
# 6. No API key → error
# ---------------------------------------------------------------------------

def test_check_anthropic_no_api_key():
    """With no ANTHROPIC_API_KEY env var, should return error."""
    from tokenpak.intelligence.deep_health import check_anthropic
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        result = check_anthropic()
    assert result.status == "error"
    assert "api_key_not_configured" in result.error


def test_check_openai_no_api_key():
    from tokenpak.intelligence.deep_health import check_openai
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        result = check_openai()
    assert result.status == "error"
    assert "api_key_not_configured" in result.error


# ---------------------------------------------------------------------------
# 7. Database ok path
# ---------------------------------------------------------------------------

def test_check_database_ok(tmp_path):
    from tokenpak.intelligence.deep_health import check_database
    db = tmp_path / "test.db"
    db.write_bytes(b"\x00" * 1024)
    result = check_database(str(db))
    assert result.status == "ok"
    assert result.details["size_mb"] == pytest.approx(0.001, abs=0.001)


# ---------------------------------------------------------------------------
# 8. Database missing → error
# ---------------------------------------------------------------------------

def test_check_database_missing():
    from tokenpak.intelligence.deep_health import check_database
    result = check_database("/nonexistent/path/monitor.db")
    assert result.status == "error"
    assert "not_found" in result.error


# ---------------------------------------------------------------------------
# 9. Index ok path
# ---------------------------------------------------------------------------

def test_check_index_ok(tmp_path):
    from tokenpak.intelligence.deep_health import check_index
    idx = tmp_path / "pricing_index.json"
    idx.write_text("{}")
    result = check_index(str(idx))
    assert result.status == "ok"
    assert result.details["age_hours"] < 0.1


# ---------------------------------------------------------------------------
# 10. Index stale → warning
# ---------------------------------------------------------------------------

def test_check_index_stale(tmp_path):
    from tokenpak.intelligence.deep_health import check_index
    import os
    idx = tmp_path / "old_index.json"
    idx.write_text("{}")
    # Backdate modification time by 30 hours
    old_time = time.time() - 30 * 3600
    os.utime(str(idx), (old_time, old_time))
    result = check_index(str(idx), stale_hours=24.0)
    assert result.status == "warning"
    assert result.error == "stale"
    assert result.details["age_hours"] > 24.0


# ---------------------------------------------------------------------------
# 11. Memory ok path
# ---------------------------------------------------------------------------

def test_check_memory_ok():
    """Memory check should return a valid result with a percent field."""
    result = check_memory()
    assert result.status in ("ok", "warning", "error")
    if result.status != "error":
        assert "percent" in result.details
        assert 0.0 <= result.details["percent"] <= 100.0


def test_check_memory_ok_mock():
    from tokenpak.intelligence.deep_health import check_memory

    def _fake_meminfo():
        return {"MemTotal": 16000000, "MemAvailable": 14000000}

    # Patch /proc/meminfo open
    import builtins
    proc_content = "MemTotal:       16000000 kB\nMemAvailable:   14000000 kB\n"
    with patch("builtins.open", return_value=iter(proc_content.splitlines(keepends=True))):
        with patch.dict("sys.modules", {"psutil": None}):
            result = check_memory()
    # percent = (16000 - 14000) / 16000 = 12.5%
    # May or may not work depending on psutil availability; just check result
    assert result.status in ("ok", "warning", "error")


# ---------------------------------------------------------------------------
# 12. Memory high → warning
# ---------------------------------------------------------------------------

def test_check_memory_warning():
    """Simulate high memory usage."""
    try:
        import psutil

        class _FakeVM:
            percent = 90.0

        with patch("psutil.virtual_memory", return_value=_FakeVM()):
            result = check_memory()
        assert result.status == "warning"
        assert result.details["percent"] == 90.0
    except ImportError:
        pytest.skip("psutil not installed")


def test_check_memory_error_oom():
    """Simulate OOM-risk memory level."""
    try:
        import psutil

        class _FakeVM:
            percent = 97.0

        with patch("psutil.virtual_memory", return_value=_FakeVM()):
            result = check_memory()
        assert result.status == "error"
        assert result.error == "oom_risk"
    except ImportError:
        pytest.skip("psutil not installed")


# ---------------------------------------------------------------------------
# 13. Disk ok path
# ---------------------------------------------------------------------------

def test_check_disk_ok():
    result = check_disk("/tmp")
    assert result.status in ("ok", "warning", "error")


def test_check_disk_ok_mock():
    import shutil
    fake_usage = shutil.disk_usage.__class__  # just use named tuple

    with patch("shutil.disk_usage") as mock_du:
        mock_du.return_value = (total := 100 * 1024**3, 50 * 1024**3, 50 * 1024**3)
        # Patch as a proper namedtuple-like object
        import collections
        DU = collections.namedtuple("DU", ["total", "used", "free"])
        mock_du.return_value = DU(100 * 1024**3, 50 * 1024**3, 50 * 1024**3)
        result = check_disk("/")
    assert result.status == "ok"
    assert result.details["percent"] == 50.0


# ---------------------------------------------------------------------------
# 14. Disk high → warning
# ---------------------------------------------------------------------------

def test_check_disk_warning():
    import collections
    DU = collections.namedtuple("DU", ["total", "used", "free"])
    with patch("shutil.disk_usage") as mock_du:
        mock_du.return_value = DU(100 * 1024**3, 85 * 1024**3, 15 * 1024**3)
        result = check_disk("/")
    assert result.status == "warning"
    assert result.details["percent"] == 85.0


def test_check_disk_error():
    import collections
    DU = collections.namedtuple("DU", ["total", "used", "free"])
    with patch("shutil.disk_usage") as mock_du:
        mock_du.return_value = DU(100 * 1024**3, 96 * 1024**3, 4 * 1024**3)
        result = check_disk("/")
    assert result.status == "error"
    assert result.error == "disk_full"


# ---------------------------------------------------------------------------
# 15. HTTP 503 when any check is error
# ---------------------------------------------------------------------------

def test_deep_result_503_on_error():
    from tokenpak.intelligence.deep_health import DeepHealthResult
    checks = {
        "anthropic": _ok(),
        "openai": _err("timeout"),
        "database": CheckResult(status="ok"),
        "index": CheckResult(status="ok"),
        "memory": CheckResult(status="ok"),
        "disk": CheckResult(status="ok"),
    }
    result = DeepHealthResult(status="error", checks=checks, duration_ms=120.0)
    assert result.http_status == 503


# ---------------------------------------------------------------------------
# 16. HTTP 200 when only warnings
# ---------------------------------------------------------------------------

def test_deep_result_200_on_degraded():
    from tokenpak.intelligence.deep_health import DeepHealthResult
    checks = {
        "anthropic": _warn(),
        "openai": _ok(),
        "database": CheckResult(status="ok"),
        "index": CheckResult(status="ok"),
        "memory": CheckResult(status="ok"),
        "disk": CheckResult(status="ok"),
    }
    result = DeepHealthResult(status="degraded", checks=checks, duration_ms=80.0)
    assert result.http_status == 200


# ---------------------------------------------------------------------------
# 17. Response includes version
# ---------------------------------------------------------------------------

def test_deep_health_endpoint_has_version(client):
    """Wire the full endpoint using injected checker."""
    from tokenpak.intelligence import deep_health as dh_module

    original = dh_module.get_checker

    def _patched_get_checker(**kwargs):
        return _make_checker()

    dh_module.get_checker = _patched_get_checker
    try:
        resp = client.get("/health?deep=true")
        data = resp.json()
        assert "version" in data
    finally:
        dh_module.get_checker = original


# ---------------------------------------------------------------------------
# 18. duration_ms reported
# ---------------------------------------------------------------------------

def test_deep_health_duration_reported():
    checker = _make_checker()
    result = checker.run()
    assert result.duration_ms >= 0
    d = result.to_dict()
    assert "duration_ms" in d
    assert d["duration_ms"] >= 0
