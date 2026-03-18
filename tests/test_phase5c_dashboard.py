"""tests/test_phase5c_dashboard.py

Phase 5C: Dashboard UI — Integration Tests
==========================================
Tests all three dashboard pages and HTMX partials.
"""
import json
import sys
import os

import pytest
from fastapi.testclient import TestClient

# Resolve vault pypi package path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tokenpak.agent.dashboard.app import create_dashboard_app, create_combined_app
from tokenpak.agent.query.api import EntryStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DATE = "2025-11-01"
FIXTURE_DATE2 = "2025-11-02"

FIXTURE_ENTRIES = [
    {
        "id": f"test-{i:03d}",
        "timestamp": f"2025-11-01T0{i%9}:00:00Z",
        "agent": f"agent-{i % 3}",
        "model": "claude-haiku" if i % 2 == 0 else "gpt-4o",
        "provider": "anthropic" if i % 2 == 0 else "openai",
        "tokens": 1000 + i * 100,
        "cost": 0.001 * (i + 1),
        "session_id": f"sess-{i}",
        "extra": {"cache_tokens": 200 if i % 2 == 0 else 0, "compression_ratio": 0.1 * (i % 5)},
    }
    for i in range(10)
]

FIXTURE_ENTRIES_D2 = [
    {
        "id": f"test-d2-{i:03d}",
        "timestamp": f"2025-11-02T0{i%9}:00:00Z",
        "agent": f"agent-{i % 2}",
        "model": "claude-sonnet",
        "provider": "anthropic",
        "tokens": 500 + i * 50,
        "cost": 0.0005 * (i + 1),
        "session_id": f"sess-d2-{i}",
        "extra": None,
    }
    for i in range(5)
]


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("entries")
    with open(d / f"{FIXTURE_DATE}.jsonl", "w") as f:
        for e in FIXTURE_ENTRIES:
            f.write(json.dumps(e) + "\n")
    with open(d / f"{FIXTURE_DATE2}.jsonl", "w") as f:
        for e in FIXTURE_ENTRIES_D2:
            f.write(json.dumps(e) + "\n")
    return d


@pytest.fixture(scope="module")
def client(fixture_dir, monkeypatch_session):
    from tokenpak.agent.query.api import EntryStore as ES, _store as orig_store
    import tokenpak.agent.dashboard.app as dash_app
    store = ES(entries_dir=fixture_dir)
    monkeypatch_session.setattr(dash_app, "_store", store)
    app = create_dashboard_app()
    return TestClient(app)


@pytest.fixture(scope="module")
def monkeypatch_session():
    """Module-scoped monkeypatch."""
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "tokenpak-dashboard"


# ---------------------------------------------------------------------------
# /dashboard (overview)
# ---------------------------------------------------------------------------

class TestOverview:
    def test_overview_200(self, client):
        r = client.get(f"/dashboard?date={FIXTURE_DATE}")
        assert r.status_code == 200

    def test_overview_html_structure(self, client):
        r = client.get(f"/dashboard?date={FIXTURE_DATE}")
        assert "Overview" in r.text
        assert "TokenPak" in r.text
        assert "stat-card" in r.text

    def test_overview_contains_nav(self, client):
        r = client.get(f"/dashboard?date={FIXTURE_DATE}")
        assert "/dashboard/time-series" in r.text
        assert "/dashboard/agents" in r.text

    def test_overview_shows_date(self, client):
        r = client.get(f"/dashboard?date={FIXTURE_DATE}")
        assert FIXTURE_DATE in r.text

    def test_overview_empty_date(self, client):
        r = client.get("/dashboard?date=1990-01-01")
        assert r.status_code == 200
        assert "0" in r.text

    def test_overview_no_date_uses_today(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /dashboard/time-series
# ---------------------------------------------------------------------------

class TestTimeSeries:
    def test_time_series_200(self, client):
        r = client.get(f"/dashboard/time-series?start={FIXTURE_DATE}&end={FIXTURE_DATE}&window=60")
        assert r.status_code == 200

    def test_time_series_html_structure(self, client):
        r = client.get(f"/dashboard/time-series?start={FIXTURE_DATE}&end={FIXTURE_DATE}&window=5")
        assert "Time Series" in r.text
        assert "tokensChart" in r.text

    def test_time_series_contains_chart_data(self, client):
        r = client.get(f"/dashboard/time-series?start={FIXTURE_DATE}&end={FIXTURE_DATE}&window=60")
        # Chart data is embedded as JSON
        assert "chart_labels" not in r.text  # template var resolved
        assert "tokensChart" in r.text

    def test_time_series_multiday(self, client):
        r = client.get(f"/dashboard/time-series?start={FIXTURE_DATE}&end={FIXTURE_DATE2}&window=1440")
        assert r.status_code == 200
        assert "2 buckets" in r.text or "rollup" in r.text.lower()

    def test_time_series_window_select(self, client):
        for window in [1, 5, 15, 30, 60, 360, 1440]:
            r = client.get(f"/dashboard/time-series?start={FIXTURE_DATE}&end={FIXTURE_DATE}&window={window}")
            assert r.status_code == 200

    def test_time_series_empty_range(self, client):
        r = client.get("/dashboard/time-series?start=1990-01-01&end=1990-01-02&window=60")
        assert r.status_code == 200
        assert "0 buckets" in r.text or "rollup_count" not in r.text

    def test_time_series_shows_dates(self, client):
        r = client.get(f"/dashboard/time-series?start={FIXTURE_DATE}&end={FIXTURE_DATE2}&window=1440")
        assert FIXTURE_DATE in r.text
        assert FIXTURE_DATE2 in r.text


# ---------------------------------------------------------------------------
# /dashboard/agents
# ---------------------------------------------------------------------------

class TestAgents:
    def test_agents_200(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}")
        assert r.status_code == 200

    def test_agents_html_structure(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}")
        assert "Leaderboard" in r.text
        assert "agent-badge" in r.text

    def test_agents_shows_agents(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}")
        assert "agent-0" in r.text

    def test_agents_sort_requests(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}&sort=requests")
        assert r.status_code == 200
        # Active sort link has "active" class
        assert "active" in r.text

    def test_agents_sort_tokens(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}&sort=tokens")
        assert r.status_code == 200

    def test_agents_sort_compression(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}&sort=compression")
        assert r.status_code == 200

    def test_agents_empty_date(self, client):
        r = client.get("/dashboard/agents?date=1990-01-01")
        assert r.status_code == 200
        assert "No agent activity" in r.text

    def test_agents_shows_rank_numbers(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}")
        assert '<td class="rank">1</td>' in r.text

    def test_agents_token_count_column(self, client):
        r = client.get(f"/dashboard/agents?date={FIXTURE_DATE}")
        assert "Tokens" in r.text


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------

class TestHTMXPartials:
    def test_htmx_stats_200(self, client):
        r = client.get(f"/dashboard/htmx/stats?date={FIXTURE_DATE}")
        assert r.status_code == 200

    def test_htmx_stats_returns_stat_cards(self, client):
        r = client.get(f"/dashboard/htmx/stats?date={FIXTURE_DATE}")
        assert "stat-card" in r.text
        assert "stat-cards" in r.text

    def test_htmx_stats_contains_values(self, client):
        r = client.get(f"/dashboard/htmx/stats?date={FIXTURE_DATE}")
        # 10 entries in fixture
        assert "10" in r.text

    def test_htmx_stats_empty_date(self, client):
        r = client.get("/dashboard/htmx/stats?date=1990-01-01")
        assert r.status_code == 200
        assert "0" in r.text

    def test_htmx_top_users_200(self, client):
        r = client.get(f"/dashboard/htmx/top-users?date={FIXTURE_DATE}")
        assert r.status_code == 200

    def test_htmx_top_users_returns_table(self, client):
        r = client.get(f"/dashboard/htmx/top-users?date={FIXTURE_DATE}")
        assert "top-users-table" in r.text
        assert "agent-badge" in r.text

    def test_htmx_top_users_shows_agents(self, client):
        r = client.get(f"/dashboard/htmx/top-users?date={FIXTURE_DATE}")
        assert "agent-0" in r.text

    def test_htmx_top_users_empty(self, client):
        r = client.get("/dashboard/htmx/top-users?date=1990-01-01")
        assert r.status_code == 200
        assert "No agent activity" in r.text


# ---------------------------------------------------------------------------
# Combined app
# ---------------------------------------------------------------------------

class TestCombinedApp:
    @pytest.fixture(scope="class")
    def combined_client(self, fixture_dir, monkeypatch_session):
        import tokenpak.agent.dashboard.app as dash_app
        import tokenpak.agent.query.api as query_app
        import tokenpak.agent.ingest.api as ingest_app
        store = EntryStore(entries_dir=fixture_dir)
        monkeypatch_session.setattr(dash_app, "_store", store)
        monkeypatch_session.setattr(query_app, "_store", store)
        app = create_combined_app()
        return TestClient(app)

    def test_combined_health(self, combined_client):
        r = combined_client.get("/health")
        assert r.status_code == 200
        assert r.json()["version"] == "5.3.0"

    def test_combined_has_ingest_route(self, combined_client):
        r = combined_client.post("/ingest", json={
            "model": "test-model", "tokens": 100, "cost": 0.001,
            "timestamp": "2025-11-01T12:00:00Z", "agent": "test-agent",
        })
        # Should succeed or return validation error (not 404)
        assert r.status_code in (200, 201, 422)

    def test_combined_has_query_route(self, combined_client):
        r = combined_client.get(f"/query/entries?start_date={FIXTURE_DATE}&end_date={FIXTURE_DATE}")
        assert r.status_code == 200

    def test_combined_has_dashboard_route(self, combined_client):
        r = combined_client.get(f"/dashboard?date={FIXTURE_DATE}")
        assert r.status_code == 200

    def test_combined_all_pages_reachable(self, combined_client):
        for path in ["/dashboard", "/dashboard/time-series", "/dashboard/agents"]:
            r = combined_client.get(f"{path}?date={FIXTURE_DATE}")
            assert r.status_code == 200, f"Failed: {path}"
