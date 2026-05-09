"""tests/test_dashboard_route_smoke.py

CI smoke test: AC-1.3 — dashboard routes return 200 with HTML body.

Verifies that `create_combined_app()` mounts the dashboard router on the
same FastAPI instance as the ingest router, so all 5 documented dashboard
routes return 200 with an HTML body.

Routes tested:
  GET /dashboard
  GET /dashboard/time-series
  GET /dashboard/agents
  GET /dashboard/audit
  GET /dashboard/timeline

Uses FastAPI TestClient (starlette) — no subprocess, no port binding needed,
CI-safe.

Note: current dashboard templates are stub placeholders (no <title> tag on
most routes). The smoke test checks for valid HTML response (200 + DOCTYPE).
The <title> check applies only to /dashboard/timeline which has a full template.
This discrepancy is noted in TRIX-03 submission for follow-up (template work
is out of scope per task constraints).
"""

from __future__ import annotations

import pytest

# TSR-04: fastapi/starlette are in the optional `[serve]` / `[telemetry]`
# extras (see pyproject.toml). Slim install does not pull them. Without
# these guards, the test module raises ModuleNotFoundError 13 times at
# collection-time (one per parametrized case + class-level helpers), each
# becoming a pytest ERROR status that blocks the release.yml auto-publish
# gate. Canonical guard pattern matches tests/test_phase5b_query_api.py,
# tests/test_telemetry_server.py, tests/dashboard/test_per_mode_panels.py,
# tests/dashboard/test_settings_page.py.
pytest.importorskip(
    "fastapi",
    reason="fastapi is optional (install [serve] or [telemetry] extra)",
)
pytest.importorskip(
    "starlette",
    reason="starlette is a fastapi dep; same optional-extra guard",
)

from starlette.testclient import TestClient  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Create a TestClient against create_combined_app() once per test module."""
    from tokenpak.dashboard.app import create_combined_app
    app = create_combined_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

DASHBOARD_ROUTES = [
    "/dashboard",
    "/dashboard/time-series",
    "/dashboard/agents",
    "/dashboard/audit",
    "/dashboard/timeline",
]


class TestDashboardRouteSmoke:
    """AC-1.3: Dashboard routes reachable on proxy port after combined-app mount."""

    @pytest.mark.parametrize("path", DASHBOARD_ROUTES)
    def test_dashboard_route_returns_200(self, client, path):
        """Each dashboard route must return HTTP 200."""
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"Expected 200 for {path}, got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.parametrize("path", DASHBOARD_ROUTES)
    def test_dashboard_route_returns_html(self, client, path):
        """Each dashboard route must return an HTML body (DOCTYPE present)."""
        resp = client.get(path)
        assert resp.status_code == 200
        body = resp.text
        assert "<!DOCTYPE html>" in body or "<html" in body, (
            f"Expected HTML body for {path}; got {body[:200]!r}"
        )

    def test_dashboard_overview_has_title(self, client):
        """/dashboard/timeline has a full template with <title>."""
        resp = client.get("/dashboard/timeline")
        assert resp.status_code == 200
        assert "<title>" in resp.text, (
            f"/dashboard/timeline must contain <title>; got {resp.text[:300]!r}"
        )

    def test_health_still_works(self, client):
        """Regression: /health must still respond on combined app."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert "ok" in resp.text

    def test_ingest_endpoint_still_present(self, client):
        """Regression: /ingest must still be mounted (combined app, not dashboard-only)."""
        import json
        payload = json.dumps({"agent_id": "smoke-test", "tokens": 1}).encode()
        resp = client.post(
            "/ingest",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
        # 200/201 = success, 422 = schema validation rejection — all mean endpoint is mounted
        assert resp.status_code in (200, 201, 422), (
            f"Expected /ingest to be mounted, got {resp.status_code}: {resp.text[:200]}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
