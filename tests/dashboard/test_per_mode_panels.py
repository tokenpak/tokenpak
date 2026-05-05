"""CCI-09 — Per-mode dashboard panel coverage."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

try:
    from tokenpak.dashboard import CCI09_DASHBOARD_MODES, serve_dashboard_file
    from tokenpak.dashboard.app import (
        MODE_PANELS,
        VALID_MODES,
        _detect_active_mode,
        _mode_from_profile,
        create_dashboard_app,
    )
except ImportError as exc:  # pragma: no cover - import guard should fail loudly
    pytest.fail(f"Failed to import tokenpak.dashboard.app: {exc}")

MODES = tuple(panel["mode"] for panel in MODE_PANELS)


@pytest.fixture(scope="module")
def client():
    """TestClient for the standalone dashboard app."""
    app = create_dashboard_app()
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


class TestEachModeRenders:
    @pytest.mark.parametrize("mode", MODES)
    def test_mode_returns_200(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        assert response.status_code == 200, response.text[:200]

    @pytest.mark.parametrize("mode", MODES)
    def test_mode_returns_html(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        assert "text/html" in response.headers.get("content-type", "")

    @pytest.mark.parametrize("mode", MODES)
    def test_mode_panel_title_in_response(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        assert mode in response.text.lower()


class TestDefaultMode:
    def test_no_mode_returns_200(self, client):
        with patch.dict(os.environ, {}, clear=True):
            response = client.get("/dashboard")
        assert response.status_code == 200

    def test_no_mode_renders_mode_selector(self, client):
        with patch.dict(os.environ, {}, clear=True):
            response = client.get("/dashboard")
        assert "mode-buttons" in response.text

    def test_default_mode_uses_cci04_profile(self, client):
        with patch.dict(os.environ, {"TOKENPAK_PROFILE": "claude-code-ide"}, clear=True):
            response = client.get("/dashboard")
        assert response.status_code == 200
        assert "IDE Mode" in response.text
        assert "claude-code-ide" in response.text


class TestModeSelectorPresent:
    @pytest.mark.parametrize("mode", MODES)
    def test_all_modes_linked_from_single_source(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        for expected in MODES:
            assert f"?mode={expected}" in response.text

    @pytest.mark.parametrize("mode", MODES)
    def test_active_mode_has_active_class(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        assert "mode-btn active" in response.text
        assert "aria-current=\"page\"" in response.text

    def test_mode_selector_is_one_click(self, client):
        response = client.get("/dashboard?mode=cli")
        for mode in MODES:
            assert f'href="/dashboard?mode={mode}"' in response.text


class TestUnknownMode:
    @pytest.mark.parametrize("bad_mode", ["unknown_xyz", "admin", "root", "api", "../etc"])
    def test_bad_modes_return_404(self, client, bad_mode):
        response = client.get(f"/dashboard?mode={bad_mode}")
        assert response.status_code == 404


class TestSharedHeader:
    @pytest.mark.parametrize("mode", MODES)
    def test_active_profile_shown(self, client, mode):
        # Clear profile env vars so _active_profile_from_env falls through to
        # the CCI-04 default pattern ("claude-code-<mode>").
        env_keys = (
            "TOKENPAK_ACTIVE_PROFILE", "TOKENPAK_PROFILE",
            "TOKENPAK_COMPANION_PROFILE", "TOKENPAK_CONSUMPTION_MODE", "TOKENPAK_MODE",
        )
        clean = {k: v for k, v in os.environ.items() if k not in env_keys}
        with patch.dict(os.environ, clean, clear=True):
            response = client.get(f"/dashboard?mode={mode}")
        assert "Active profile" in response.text
        assert f"claude-code-{mode}" in response.text

    @pytest.mark.parametrize("mode", MODES)
    def test_total_cost_today_shown(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        assert "Total cost today" in response.text
        assert "$" in response.text

    @pytest.mark.parametrize("mode", MODES)
    def test_cache_hit_rate_label_shown(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        assert "Cache hit rate" in response.text

    @pytest.mark.parametrize("mode", MODES)
    def test_shared_header_div_present(self, client, mode):
        response = client.get(f"/dashboard?mode={mode}")
        assert "shared-header" in response.text


class TestTuiPollingEndpoint:
    def test_tui_cost_endpoint_returns_200(self, client):
        response = client.get("/dashboard/htmx/mode/tui/cost")
        assert response.status_code == 200

    def test_tui_cost_endpoint_returns_dollar_amount(self, client):
        response = client.get("/dashboard/htmx/mode/tui/cost")
        assert response.text.startswith("$")

    def test_tui_cost_endpoint_returns_numeric(self, client):
        response = client.get("/dashboard/htmx/mode/tui/cost")
        float(response.text.lstrip("$"))


class TestTuiLiveMeter:
    def test_tui_has_live_meter_element(self, client):
        response = client.get("/dashboard?mode=tui")
        assert "live-meter" in response.text

    def test_tui_has_htmx_polling_attributes(self, client):
        response = client.get("/dashboard?mode=tui")
        assert 'hx-get="/dashboard/htmx/mode/tui/cost"' in response.text
        assert 'hx-trigger="every 5s"' in response.text

    def test_tui_has_local_polling_shim(self, client):
        response = client.get("/dashboard?mode=tui")
        assert "querySelectorAll('[hx-get][hx-trigger]')" in response.text


class TestNoRegressions:
    def test_existing_agents_route_still_works(self, client):
        response = client.get("/dashboard/agents")
        assert response.status_code == 200

    def test_existing_timeline_route_still_works(self, client):
        response = client.get("/dashboard/timeline")
        assert response.status_code == 200

    def test_existing_audit_route_still_works(self, client):
        response = client.get("/dashboard/audit")
        assert response.status_code == 200

    def test_health_route_still_works(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_valid_modes_derived_from_mode_panels(self):
        assert VALID_MODES == frozenset(MODES)
        assert len(VALID_MODES) == 6


class TestStaticDashboardShell:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", MODES)
    async def test_static_dashboard_query_serves_index(self, mode):
        result = await serve_dashboard_file(f"/?mode={mode}")
        assert result is not None
        content, mime_type = result
        assert mime_type == "text/html"
        assert "mode-selector" in content
        assert f"mode: '{mode}'" in content

    def test_static_dashboard_mode_catalog_matches_app_modes(self):
        assert CCI09_DASHBOARD_MODES == MODES


class TestDetectActiveMode:
    @pytest.mark.parametrize("mode", MODES)
    def test_cci04_profile_maps_to_mode(self, mode):
        assert _mode_from_profile(f"claude-code-{mode}") == mode

    def test_explicit_profile_takes_priority(self):
        with patch.dict(os.environ, {"TOKENPAK_PROFILE": "claude-code-sdk"}, clear=True):
            assert _detect_active_mode() == "sdk"

    def test_explicit_consumption_env_takes_priority(self):
        with patch.dict(os.environ, {"TOKENPAK_CONSUMPTION_MODE": "sdk"}, clear=True):
            assert _detect_active_mode() == "sdk"

    def test_invalid_env_var_falls_through_to_heuristic(self):
        with patch.dict(os.environ, {"TOKENPAK_MODE": "invalid"}, clear=True):
            assert _detect_active_mode() == "cli"

    def test_tmux_env_detected(self):
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1234/default,123,0"}, clear=True):
            assert _detect_active_mode() == "tmux"

    def test_no_signals_defaults_to_cli(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _detect_active_mode() == "cli"
