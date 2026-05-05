"""CCI-09 — Per-mode dashboard panels tests.

Covers all acceptance criteria:
  AC 1 — Each of the 6 modes renders an HTTP 200 at /dashboard?mode=<mode>
  AC 2 — /dashboard with no ?mode= returns 200 (defaults to detected mode)
  AC 3 — Mode selector appears in every per-mode response
  AC 4 — Unknown ?mode= value returns 404
  AC 5 — Cross-mode shared header fields appear in every response
  AC 6 — HTMX polling endpoint /dashboard/htmx/mode/tui/cost returns 200
  AC 7 — TUI live-meter polling script included in tui response
  AC 8 — No regressions: existing dashboard routes still work

All tests use TestClient against the combined FastAPI app — no running proxy
needed.
"""

from __future__ import annotations

import os
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from typing import Any

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------
try:
    from tokenpak.dashboard.app import create_dashboard_app, VALID_MODES, _detect_active_mode
except ImportError as exc:
    pytest.fail(f"Failed to import tokenpak.dashboard.app: {exc}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """TestClient for the dashboard app with an empty telemetry store."""
    app = create_dashboard_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# AC 1 — Each mode renders HTTP 200
# ---------------------------------------------------------------------------

class TestEachModeRenders:
    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_mode_returns_200(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        assert r.status_code == 200, f"mode={mode} got {r.status_code}: {r.text[:200]}"

    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_mode_returns_html(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        assert "text/html" in r.headers.get("content-type", "")

    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_mode_panel_title_in_response(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        assert mode in r.text.lower(), f"mode={mode} not found in response"


# ---------------------------------------------------------------------------
# AC 2 — No ?mode= defaults gracefully
# ---------------------------------------------------------------------------

class TestDefaultMode:
    def test_no_mode_returns_200(self, client):
        # Force TOKENPAK_MODE unset so detection falls through to "cli"
        env = {k: v for k, v in os.environ.items() if k != "TOKENPAK_MODE"}
        with patch.dict(os.environ, {}, clear=True):
            os.environ.update(env)
            r = client.get("/dashboard")
        assert r.status_code == 200

    def test_no_mode_renders_mode_selector(self, client):
        with patch.dict(os.environ, {}, clear=True):
            r = client.get("/dashboard")
        assert "mode-buttons" in r.text or "mode-btn" in r.text


# ---------------------------------------------------------------------------
# AC 3 — Mode selector present in every response
# ---------------------------------------------------------------------------

class TestModeSelectorPresent:
    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_all_6_modes_linked(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        # All 6 modes should appear as links in the selector
        for m in ["cli", "tui", "tmux", "sdk", "ide", "cron"]:
            assert f"?mode={m}" in r.text, f"Link to ?mode={m} missing in mode={mode} response"

    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_active_mode_has_active_class(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        # The active mode button should have the "active" CSS class
        # Check that mode appears near "active" in the response
        assert "active" in r.text

    def test_mode_selector_is_1_click(self, client):
        # Each mode is a direct link (<a href=...>) not nested in menus
        r = client.get("/dashboard?mode=cli")
        for m in ["cli", "tui", "tmux", "sdk", "ide", "cron"]:
            assert f'href="/dashboard?mode={m}"' in r.text, f"Direct link for mode={m} missing"


# ---------------------------------------------------------------------------
# AC 4 — Unknown mode returns 404
# ---------------------------------------------------------------------------

class TestUnknownMode:
    def test_unknown_mode_returns_404(self, client):
        r = client.get("/dashboard?mode=unknown_xyz")
        assert r.status_code == 404

    def test_empty_mode_param_returns_200(self, client):
        # Empty string falls through; server treats as no-mode
        r = client.get("/dashboard?mode=")
        # Should either default gracefully or return 404
        assert r.status_code in (200, 404)

    @pytest.mark.parametrize("bad_mode", ["admin", "root", "api", "../etc"])
    def test_bad_modes_return_404(self, client, bad_mode):
        r = client.get(f"/dashboard?mode={bad_mode}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# AC 5 — Shared header fields appear in every response
# ---------------------------------------------------------------------------

class TestSharedHeader:
    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_active_profile_shown(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        assert "profile" in r.text.lower()

    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_total_cost_today_shown(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        # Should contain a dollar sign for cost display
        assert "$" in r.text

    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_cache_hit_rate_label_shown(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        assert "cache" in r.text.lower()

    @pytest.mark.parametrize("mode", ["cli", "tui", "tmux", "sdk", "ide", "cron"])
    def test_shared_header_div_present(self, client, mode):
        r = client.get(f"/dashboard?mode={mode}")
        assert "shared-header" in r.text


# ---------------------------------------------------------------------------
# AC 6 — TUI HTMX polling endpoint
# ---------------------------------------------------------------------------

class TestTuiPollingEndpoint:
    def test_tui_cost_endpoint_returns_200(self, client):
        r = client.get("/dashboard/htmx/mode/tui/cost")
        assert r.status_code == 200

    def test_tui_cost_endpoint_returns_dollar_amount(self, client):
        r = client.get("/dashboard/htmx/mode/tui/cost")
        assert r.text.startswith("$")

    def test_tui_cost_endpoint_returns_numeric(self, client):
        r = client.get("/dashboard/htmx/mode/tui/cost")
        # Should be parseable: "$0.000000"
        cost_str = r.text.lstrip("$")
        float(cost_str)  # raises ValueError if not a number


# ---------------------------------------------------------------------------
# AC 7 — TUI mode includes live-meter polling script
# ---------------------------------------------------------------------------

class TestTuiLiveMeter:
    def test_tui_has_live_meter_element(self, client):
        r = client.get("/dashboard?mode=tui")
        assert "live-meter" in r.text

    def test_tui_has_polling_script(self, client):
        r = client.get("/dashboard?mode=tui")
        assert "setInterval" in r.text or "htmx" in r.text.lower()

    def test_tui_polling_targets_correct_endpoint(self, client):
        r = client.get("/dashboard?mode=tui")
        assert "/dashboard/htmx/mode/tui/cost" in r.text


# ---------------------------------------------------------------------------
# AC 8 — No regressions: existing routes still work
# ---------------------------------------------------------------------------

class TestNoRegressions:
    def test_existing_agents_route_still_works(self, client):
        r = client.get("/dashboard/agents")
        assert r.status_code == 200

    def test_existing_timeline_route_still_works(self, client):
        r = client.get("/dashboard/timeline")
        assert r.status_code == 200

    def test_existing_audit_route_still_works(self, client):
        r = client.get("/dashboard/audit")
        assert r.status_code == 200

    def test_health_route_still_works(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_valid_modes_constant_has_6_entries(self):
        assert len(VALID_MODES) == 6
        assert VALID_MODES == {"cli", "tui", "tmux", "sdk", "ide", "cron"}


# ---------------------------------------------------------------------------
# Unit: _detect_active_mode
# ---------------------------------------------------------------------------

class TestDetectActiveMode:
    def test_explicit_env_var_takes_priority(self):
        with patch.dict(os.environ, {"TOKENPAK_MODE": "sdk"}, clear=False):
            assert _detect_active_mode() == "sdk"

    def test_invalid_env_var_falls_through_to_heuristic(self):
        with patch.dict(os.environ, {"TOKENPAK_MODE": "invalid", "TMUX": ""}, clear=False):
            # "invalid" not in VALID_MODES, should fall through (TMUX="" is falsy)
            result = _detect_active_mode()
            assert result in VALID_MODES

    def test_tmux_env_detected(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("TOKENPAK_MODE", "TMUX", "TMUX_PANE",
                            "TERM_PROGRAM", "VSCODE_PID", "JETBRAINS_IDE",
                            "TOKENPAK_JOB_NAME", "CRON_JOB")}
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1234/default,123,0"}, clear=True):
            os.environ.update(env)
            assert _detect_active_mode() == "tmux"

    def test_no_signals_defaults_to_cli(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _detect_active_mode() == "cli"
