"""Tests for account-scoped dashboard (WS-8).

Coverage:
  - License detection (key_id extraction)
  - Pro+ access gating
  - Usage data loading from metering
  - Savings calculations & ROI
  - Template rendering
  - JSON API endpoints
"""

import pytest

try:
    from tokenpak.dashboard.account_dashboard import _get_license_key_id
except ImportError:
    pytest.skip(
        "Cannot import _get_license_key_id from tokenpak.dashboard.account_dashboard — removed in current build",
        allow_module_level=True,
    )
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from tokenpak.dashboard.account_dashboard import (
    _calculate_roi,
    _check_pro_access,
    _get_license_key_id,
    _load_usage_data,
    router,
)

# ─────────────────────────────────────────────
# License Detection
# ─────────────────────────────────────────────


class TestLicenseDetection:
    def test_get_key_from_env_variable(self):
        """Extract key_id from TOKENPAK_LICENSE_KEY env var."""
        with patch.dict(os.environ, {"TOKENPAK_LICENSE_KEY": "TPAK-TEST-1234"}):
            key_id = _get_license_key_id()
            assert key_id == "TPAK-TEST-1234"

    def test_get_key_from_none_if_no_env(self):
        """Return None if no license configured."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path("/tmp/nonexistent")
                key_id = _get_license_key_id()
                assert key_id is None

    def test_get_key_prefers_env_over_file(self):
        """Environment variable takes priority over license file."""
        with patch.dict(os.environ, {"TOKENPAK_LICENSE_KEY": "TPAK-ENV-999"}):
            # Environment var should be returned first, no need to check file
            key_id = _get_license_key_id()
            assert key_id == "TPAK-ENV-999"


# ─────────────────────────────────────────────
# Access Gating
# ─────────────────────────────────────────────


class TestAccessGating:
    def test_pro_access_allowed_with_license(self):
        """User with license can access account dashboard."""
        with patch("tokenpak.dashboard.account_dashboard._get_license_key_id") as mock_get:
            mock_get.return_value = "TPAK-PRO-123"
            request = MagicMock()
            key_id = _check_pro_access(request)
            assert key_id == "TPAK-PRO-123"

    def test_pro_access_forbidden_without_license(self):
        """OSS user (no license) gets 403."""
        with patch("tokenpak.dashboard.account_dashboard._get_license_key_id") as mock_get:
            mock_get.return_value = None
            request = MagicMock()

            with pytest.raises(HTTPException) as exc:
                _check_pro_access(request)

            assert exc.value.status_code == status.HTTP_403_FORBIDDEN
            detail = exc.value.detail
            assert "Pro or higher" in detail["message"]
            assert "upgrade_url" in detail


# ─────────────────────────────────────────────
# Usage Data Loading
# ─────────────────────────────────────────────


class TestUsageDataLoading:
    @patch("tokenpak.metering.UsageMeterManager")
    def test_load_usage_data_success(self, mock_manager_class):
        """Load usage data for a date range."""
        # Mock the meter
        mock_meter = MagicMock()
        mock_meter.get_daily_summary.side_effect = lambda date_str: {
            "total_input": 5000 if "22" in date_str else 10000,
            "total_output": 1000 if "22" in date_str else 2000,
            "total_saved": 500 if "22" in date_str else 1000,
            "request_count": 10 if "22" in date_str else 20,
        }

        mock_manager = MagicMock()
        mock_manager.get_meter.return_value = mock_meter
        mock_manager_class.return_value = mock_manager

        data = _load_usage_data("TPAK-TEST-123", "2026-03-21", "2026-03-22")

        assert len(data) == 2
        assert data[0]["date"] == "2026-03-21"
        assert data[0]["input_tokens"] == 10000
        assert data[1]["date"] == "2026-03-22"
        assert data[1]["input_tokens"] == 5000

    @patch("tokenpak.metering.UsageMeterManager")
    def test_load_usage_data_empty_range(self, mock_manager_class):
        """Return empty list if no data in range."""
        mock_meter = MagicMock()
        mock_meter.get_daily_summary.return_value = {
            "total_input": 0,
            "total_output": 0,
            "total_saved": 0,
        }

        mock_manager = MagicMock()
        mock_manager.get_meter.return_value = mock_meter
        mock_manager_class.return_value = mock_manager

        data = _load_usage_data("TPAK-TEST-123", "2026-03-21", "2026-03-22")

        # Empty summaries are filtered out
        assert len(data) == 0

    @patch("tokenpak.metering.UsageMeterManager")
    def test_load_usage_data_handles_error(self, mock_manager_class):
        """Return empty list on metering error (graceful degradation)."""
        mock_manager_class.side_effect = Exception("DB error")

        data = _load_usage_data("TPAK-TEST-123", "2026-03-21", "2026-03-22")

        assert data == []


# ─────────────────────────────────────────────
# ROI Calculation
# ─────────────────────────────────────────────


class TestROICalculation:
    def test_calculate_roi_zero_tokens(self):
        """ROI for zero saved tokens."""
        roi = _calculate_roi(0)
        assert roi["total_saved_tokens"] == 0
        assert roi["estimated_savings_usd"] == 0.0

    def test_calculate_roi_nonzero_tokens(self):
        """ROI calculation scales with tokens saved."""
        # 100k tokens saved should produce non-zero savings
        roi = _calculate_roi(100000)
        assert roi["total_saved_tokens"] == 100000
        assert roi["estimated_savings_usd"] > 0
        # Sanity check: scales linearly with token count
        # Actual value depends on model mix average in the formula
        assert roi["estimated_savings_usd"] > 1.0

    def test_calculate_roi_includes_period(self):
        """ROI includes period label."""
        roi = _calculate_roi(50000)
        assert "period" in roi
        assert roi["period"] == "since activation"


# ─────────────────────────────────────────────
# Route Tests
# ─────────────────────────────────────────────


@pytest.fixture
def client():
    """FastAPI test client with account dashboard router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestAccountDashboardRoutes:
    @patch("tokenpak.dashboard.account_dashboard._check_pro_access")
    @patch("tokenpak.dashboard.account_dashboard._load_usage_data")
    def test_usage_route_renders_html(self, mock_load, mock_check, client):
        """GET /dashboard/account/usage returns HTML."""
        mock_check.return_value = "TPAK-TEST-123"
        mock_load.return_value = [
            {
                "date": "2026-03-22",
                "model": "claude-sonnet",
                "input_tokens": 5000,
                "output_tokens": 1000,
                "saved_tokens": 500,
                "request_count": 10,
            }
        ]

        response = client.get("/dashboard/account/usage?days=7")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Token Usage" in response.text

    @patch("tokenpak.dashboard.account_dashboard._check_pro_access")
    def test_usage_route_pro_gate_blocks_oss(self, mock_check, client):
        """GET /dashboard/account/usage returns 403 for OSS users."""
        from fastapi import HTTPException

        mock_check.side_effect = HTTPException(
            status_code=403, detail="Account dashboard requires Pro"
        )

        response = client.get("/dashboard/account/usage")

        assert response.status_code == 403

    @patch("tokenpak.dashboard.account_dashboard._check_pro_access")
    @patch("tokenpak.dashboard.account_dashboard._load_usage_data")
    def test_savings_route_renders_html(self, mock_load, mock_check, client):
        """GET /dashboard/account/savings returns HTML."""
        mock_check.return_value = "TPAK-TEST-123"
        mock_load.return_value = [
            {
                "date": "2026-03-22",
                "input_tokens": 5000,
                "output_tokens": 1000,
                "saved_tokens": 500,
            }
        ]

        response = client.get("/dashboard/account/savings?days=30")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Compression Savings" in response.text

    @patch("tokenpak.dashboard.account_dashboard._check_pro_access")
    @patch("tokenpak.dashboard.account_dashboard._calculate_roi")
    @patch("tokenpak.dashboard.account_dashboard._load_usage_data")
    def test_roi_route_renders_html(self, mock_load, mock_roi, mock_check, client):
        """GET /dashboard/account/roi returns HTML."""
        mock_check.return_value = "TPAK-TEST-123"
        mock_load.return_value = [
            {
                "date": "2026-03-22",
                "saved_tokens": 5000,
            }
        ]
        mock_roi.return_value = {
            "total_saved_tokens": 5000,
            "estimated_savings_usd": 0.15,
            "period": "since activation",
        }

        response = client.get("/dashboard/account/roi")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Return on Investment" in response.text

    @patch("tokenpak.dashboard.account_dashboard._check_pro_access")
    @patch("tokenpak.dashboard.account_dashboard._load_usage_data")
    def test_api_usage_json(self, mock_load, mock_check, client):
        """GET /dashboard/account/api/usage.json returns JSON."""
        mock_check.return_value = "TPAK-TEST-123"
        mock_load.return_value = [
            {
                "date": "2026-03-22",
                "model": "claude-sonnet",
                "input_tokens": 5000,
                "output_tokens": 1000,
                "saved_tokens": 500,
            }
        ]

        response = client.get("/dashboard/account/api/usage.json?days=7")

        assert response.status_code == 200
        data = response.json()
        assert data["key_id"] == "TPAK-TEST-123"
        assert len(data["data"]) == 1
        assert data["data"][0]["input_tokens"] == 5000

    @patch("tokenpak.dashboard.account_dashboard._check_pro_access")
    @patch("tokenpak.dashboard.account_dashboard._load_usage_data")
    @patch("tokenpak.dashboard.account_dashboard._calculate_roi")
    def test_api_savings_json(self, mock_roi, mock_load, mock_check, client):
        """GET /dashboard/account/api/savings.json returns JSON."""
        mock_check.return_value = "TPAK-TEST-123"
        mock_load.return_value = [
            {
                "date": "2026-03-22",
                "saved_tokens": 5000,
            }
        ]
        mock_roi.return_value = {
            "total_saved_tokens": 5000,
            "estimated_savings_usd": 0.15,
        }

        response = client.get("/dashboard/account/api/savings.json?days=30")

        assert response.status_code == 200
        data = response.json()
        assert data["key_id"] == "TPAK-TEST-123"
        assert data["estimated_savings_usd"] == 0.15


# ─────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────


class TestIntegration:
    @patch("tokenpak.dashboard.account_dashboard._get_license_key_id")
    @patch("tokenpak.metering.UsageMeterManager")
    def test_end_to_end_usage_page(self, mock_manager_class, mock_get_key, client):
        """Full flow: detect license, load data, render usage page."""
        mock_get_key.return_value = "TPAK-PRO-123"

        mock_meter = MagicMock()
        mock_meter.get_daily_summary.return_value = {
            "total_input": 10000,
            "total_output": 2000,
            "total_saved": 1000,
            "request_count": 20,
        }

        mock_manager = MagicMock()
        mock_manager.get_meter.return_value = mock_meter
        mock_manager_class.return_value = mock_manager

        response = client.get("/dashboard/account/usage?days=7")

        assert response.status_code == 200
        assert "Token Usage" in response.text
        assert "10,000" in response.text or "10000" in response.text

    @patch("tokenpak.dashboard.account_dashboard._get_license_key_id")
    def test_oss_user_blocked_from_account_pages(self, mock_get_key, client):
        """OSS user (no license) can't access account-scoped pages."""
        mock_get_key.return_value = None  # No license

        response = client.get("/dashboard/account/usage")

        assert response.status_code == 403


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
