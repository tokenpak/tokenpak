"""tests/test_phase5b_integration.py

Phase 5B: Integration Test Suite — Query API
=============================================

Tests all 8 Query API endpoints against deterministic JSONL fixture data:

  GET  /query/entries          — date range, limit, empty range
  GET  /query/stats            — known date, missing date
  GET  /query/rollups          — 5min window, 1hr window
  GET  /query/top-users        — top N, no activity
  GET  /query/cache-trends     — multi-day range
  GET  /query/compression-ratio — per-agent values
  GET  /query/usage-summary    — daily totals
  POST /query/export           — CSV format, correct headers, row count

Edge cases:
  - Empty date range → valid empty response (not 500)
  - Invalid date format → 400
  - limit=0 or negative → safe handling (FastAPI rejects)
  - Future date → empty results
"""
from __future__ import annotations

import io
import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

# Ensure vault package root on sys.path
VAULT_PKG_ROOT = Path(__file__).parent.parent
if str(VAULT_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(VAULT_PKG_ROOT))

from fastapi.testclient import TestClient
from tokenpak.agent.query.api import EntryStore, create_query_app


# ---------------------------------------------------------------------------
# Fixture data constants
# ---------------------------------------------------------------------------

FIXTURE_DATES = ["2025-10-01", "2025-10-02", "2025-10-03"]
AGENTS = ["sue", "cali", "trix"]
MODELS = ["claude-haiku-3-5", "gpt-4o", "gemini-flash"]
PROVIDERS = ["anthropic", "openai", "google"]

# Deterministic entries per day (10 per day, 30 total)
ENTRIES_PER_DAY = 10


def _make_entry(
    date: str,
    idx: int,
    agent: str,
    model: str,
    provider: str,
    tokens: int,
    cost: float,
    cache_tokens: int = 0,
    compression_pct: float = 0.0,
    compression_ratio: float | None = None,
) -> dict[str, Any]:
    """Build a single JSONL entry with deterministic values."""
    ts = datetime.strptime(date, "%Y-%m-%d").replace(
        hour=(idx * 2) % 24,
        minute=(idx * 7) % 60,
        tzinfo=timezone.utc,
    )
    entry: dict[str, Any] = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{date}-{idx}-{agent}")),
        "timestamp": ts.isoformat(),
        "agent": agent,
        "model": model,
        "provider": provider,
        "tokens": tokens,
        "cost": cost,
        "session_id": f"sess-{date}-{idx}",
        "extra": {
            "cache_tokens": cache_tokens,
            "compression_pct": compression_pct,
        },
    }
    if compression_ratio is not None:
        entry["extra"]["compression_ratio"] = compression_ratio
    return entry


def _write_fixtures(entries_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Write deterministic JSONL fixtures and return mapping of date → entries."""
    entries_dir.mkdir(parents=True, exist_ok=True)
    fixture_data: dict[str, list[dict[str, Any]]] = {}

    for date in FIXTURE_DATES:
        day_entries = []
        for i in range(ENTRIES_PER_DAY):
            agent = AGENTS[i % len(AGENTS)]
            model = MODELS[i % len(MODELS)]
            provider = PROVIDERS[i % len(PROVIDERS)]
            tokens = 100 + i * 50          # 100, 150, …, 550
            cost = round(0.001 + i * 0.0005, 6)
            cache_tokens = 20 + i * 5 if i % 2 == 0 else 0   # every other entry has cache hits
            compression_pct = round(i * 2.5, 2)               # 0.0 … 22.5
            compression_ratio = round(1.0 + i * 0.1, 3) if i % 3 == 0 else None
            entry = _make_entry(
                date, i, agent, model, provider, tokens, cost,
                cache_tokens=cache_tokens,
                compression_pct=compression_pct,
                compression_ratio=compression_ratio,
            )
            day_entries.append(entry)

        jsonl_path = entries_dir / f"{date}.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for e in day_entries:
                f.write(json.dumps(e) + "\n")

        fixture_data[date] = day_entries

    return fixture_data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def entries_dir(tmp_path_factory) -> Path:
    """Create a temp directory with deterministic JSONL fixtures."""
    d = tmp_path_factory.mktemp("fixtures") / "entries"
    _write_fixtures(d)
    return d


@pytest.fixture(scope="module")
def store(entries_dir) -> EntryStore:
    """EntryStore pointed at our test fixtures."""
    return EntryStore(entries_dir=entries_dir)


@pytest.fixture(scope="module")
def client(entries_dir) -> TestClient:
    """TestClient backed by fixture data via monkeypatched _store."""
    import tokenpak.agent.query.api as query_module
    test_store = EntryStore(entries_dir=entries_dir)
    # Patch the module-level _store so all router handlers use fixture data
    with patch.object(query_module, "_store", test_store):
        app = create_query_app()
        yield TestClient(app)


@pytest.fixture(scope="module")
def fixture_data(entries_dir) -> dict[str, list[dict[str, Any]]]:
    """Return the deterministic fixture entries keyed by date."""
    return _write_fixtures(entries_dir)  # idempotent — rewrites identical files


# ---------------------------------------------------------------------------
# 1. /query/entries
# ---------------------------------------------------------------------------

class TestQueryEntries:
    """GET /query/entries"""

    def test_entries_happy_path(self, client):
        """TC-E01: Date range with data returns 200 + entries."""
        resp = client.get("/query/entries?start_date=2025-10-01&end_date=2025-10-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["count"] == ENTRIES_PER_DAY
        assert len(data["entries"]) == ENTRIES_PER_DAY

    def test_entries_multi_day(self, client):
        """TC-E02: Multi-day range aggregates all entries."""
        resp = client.get("/query/entries?start_date=2025-10-01&end_date=2025-10-03")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == ENTRIES_PER_DAY * 3
        assert len(data["entries"]) == ENTRIES_PER_DAY * 3

    def test_entries_limit(self, client):
        """TC-E03: limit parameter caps the returned entries."""
        resp = client.get("/query/entries?start_date=2025-10-01&end_date=2025-10-03&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 5
        assert len(data["entries"]) == 5

    def test_entries_empty_range(self, client):
        """TC-E04: Date range with no data returns empty list (not 500)."""
        resp = client.get("/query/entries?start_date=2024-01-01&end_date=2024-01-03")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["entries"] == []

    def test_entries_invalid_date_format(self, client):
        """TC-E05: Invalid date format returns 400."""
        resp = client.get("/query/entries?start_date=not-a-date&end_date=2025-10-01")
        assert resp.status_code == 400

    def test_entries_start_after_end(self, client):
        """TC-E06: start_date > end_date returns 400."""
        resp = client.get("/query/entries?start_date=2025-10-03&end_date=2025-10-01")
        assert resp.status_code == 400

    def test_entries_future_date(self, client):
        """TC-E07: Future date returns empty results (not 500)."""
        resp = client.get("/query/entries?start_date=2099-01-01&end_date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_entries_entry_fields(self, client):
        """TC-E08: Each returned entry has expected fields."""
        resp = client.get("/query/entries?start_date=2025-10-01&end_date=2025-10-01")
        data = resp.json()
        entry = data["entries"][0]
        for field in ["id", "timestamp", "agent", "tokens", "cost"]:
            assert field in entry, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# 2. /query/stats
# ---------------------------------------------------------------------------

class TestQueryStats:
    """GET /query/stats"""

    def test_stats_known_date(self, client):
        """TC-ST01: Stats for a known date return correct aggregates."""
        resp = client.get("/query/stats?date=2025-10-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        stats = data["stats"]
        assert stats["date"] == "2025-10-01"
        assert stats["request_count"] == ENTRIES_PER_DAY

    def test_stats_token_count(self, client):
        """TC-ST02: Stats token_count matches fixture data sum."""
        # Sum tokens: 100+150+...+550 for ENTRIES_PER_DAY=10 entries
        expected_tokens = sum(100 + i * 50 for i in range(ENTRIES_PER_DAY))
        resp = client.get("/query/stats?date=2025-10-01")
        data = resp.json()
        assert data["stats"]["token_count"] == expected_tokens

    def test_stats_missing_date(self, client):
        """TC-ST03: Stats for a date with no data returns empty/zero aggregates (not 404)."""
        resp = client.get("/query/stats?date=2024-06-15")
        assert resp.status_code == 200
        stats = resp.json()["stats"]
        assert stats["request_count"] == 0
        assert stats["token_count"] == 0

    def test_stats_future_date(self, client):
        """TC-ST04: Stats for future date returns zeros."""
        resp = client.get("/query/stats?date=2099-12-31")
        assert resp.status_code == 200
        stats = resp.json()["stats"]
        assert stats["request_count"] == 0

    def test_stats_has_cache_hit_pct(self, client):
        """TC-ST05: Stats include cache_hit_pct field."""
        resp = client.get("/query/stats?date=2025-10-01")
        stats = resp.json()["stats"]
        assert "cache_hit_pct" in stats
        # Some entries have cache_tokens so hit_pct > 0
        assert stats["cache_hit_pct"] >= 0.0

    def test_stats_invalid_date(self, client):
        """TC-ST06: Invalid date format returns 400."""
        resp = client.get("/query/stats?date=October-1st")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 3. /query/rollups
# ---------------------------------------------------------------------------

class TestQueryRollups:
    """GET /query/rollups"""

    def test_rollups_5min_window(self, client):
        """TC-R01: 5-minute rollup returns bucketed list."""
        resp = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-01&window_minutes=5"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["window_minutes"] == 5
        assert isinstance(data["rollups"], list)
        assert len(data["rollups"]) > 0

    def test_rollups_1hr_window(self, client):
        """TC-R02: 60-minute rollup returns fewer buckets than 5-minute."""
        resp5 = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-01&window_minutes=5"
        )
        resp60 = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-01&window_minutes=60"
        )
        assert resp60.status_code == 200
        assert resp60.json()["window_minutes"] == 60
        # 60-min buckets should be ≤ 5-min buckets
        assert len(resp60.json()["rollups"]) <= len(resp5.json()["rollups"])

    def test_rollups_bucket_fields(self, client):
        """TC-R03: Each rollup bucket has expected fields."""
        resp = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-01&window_minutes=5"
        )
        bucket = resp.json()["rollups"][0]
        for field in ["timestamp", "total_tokens", "request_count"]:
            assert field in bucket, f"Missing bucket field: {field}"

    def test_rollups_multi_day(self, client):
        """TC-R04: Multi-day rollup aggregates correctly."""
        resp = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-03&window_minutes=60"
        )
        assert resp.status_code == 200
        data = resp.json()
        total_requests = sum(b["request_count"] for b in data["rollups"])
        assert total_requests == ENTRIES_PER_DAY * 3

    def test_rollups_empty_range(self, client):
        """TC-R05: Empty date range returns empty rollups list."""
        resp = client.get(
            "/query/rollups?start_date=2024-01-01&end_date=2024-01-01&window_minutes=5"
        )
        assert resp.status_code == 200
        assert resp.json()["rollups"] == []

    def test_rollups_invalid_window(self, client):
        """TC-R06: window_minutes < 1 returns 400."""
        resp = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-01&window_minutes=0"
        )
        # FastAPI validates ge=1 → 422; or our custom check → 400
        assert resp.status_code in (400, 422)

    def test_rollups_sorted_ascending(self, client):
        """TC-R07: Rollup buckets are sorted chronologically."""
        resp = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-03&window_minutes=60"
        )
        timestamps = [b["timestamp"] for b in resp.json()["rollups"]]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# 4. /query/top-users
# ---------------------------------------------------------------------------

class TestQueryTopUsers:
    """GET /query/top-users"""

    def test_top_users_happy_path(self, client):
        """TC-TU01: Top users for a date with activity returns list."""
        resp = client.get("/query/top-users?date=2025-10-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        users = data["users"]
        assert isinstance(users, list)
        assert len(users) > 0

    def test_top_users_ordered_descending(self, client):
        """TC-TU02: Users are ordered by request_count descending."""
        resp = client.get("/query/top-users?date=2025-10-01")
        users = resp.json()["users"]
        counts = [u["request_count"] for u in users]
        assert counts == sorted(counts, reverse=True)

    def test_top_users_limit(self, client):
        """TC-TU03: limit=1 returns at most 1 user."""
        resp = client.get("/query/top-users?date=2025-10-01&limit=1")
        assert resp.status_code == 200
        users = resp.json()["users"]
        assert len(users) <= 1

    def test_top_users_all_agents_represented(self, client):
        """TC-TU04: All 3 agents appear in top-users (no activity filtering)."""
        resp = client.get("/query/top-users?date=2025-10-01&limit=10")
        users = resp.json()["users"]
        agent_ids = {u["agent_id"] for u in users}
        for agent in AGENTS:
            assert agent in agent_ids, f"Agent {agent} missing from top-users"

    def test_top_users_no_activity(self, client):
        """TC-TU05: No activity on date returns empty list (not 500)."""
        resp = client.get("/query/top-users?date=2024-01-01")
        assert resp.status_code == 200
        assert resp.json()["users"] == []

    def test_top_users_user_fields(self, client):
        """TC-TU06: Each user entry has agent_id and request_count."""
        resp = client.get("/query/top-users?date=2025-10-01")
        user = resp.json()["users"][0]
        assert "agent_id" in user
        assert "request_count" in user
        assert user["request_count"] > 0


# ---------------------------------------------------------------------------
# 5. /query/cache-trends
# ---------------------------------------------------------------------------

class TestQueryCacheTrends:
    """GET /query/cache-trends"""

    def test_cache_trends_multi_day(self, client):
        """TC-CT01: Multi-day range returns one trend point per day with data."""
        resp = client.get(
            "/query/cache-trends?start_date=2025-10-01&end_date=2025-10-03"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        trends = data["trends"]
        assert len(trends) == 3  # one per FIXTURE_DATES day

    def test_cache_trends_fields(self, client):
        """TC-CT02: Each trend point has timestamp, hit_rate, miss_rate."""
        resp = client.get(
            "/query/cache-trends?start_date=2025-10-01&end_date=2025-10-01"
        )
        trend = resp.json()["trends"][0]
        for field in ["timestamp", "hit_rate", "miss_rate"]:
            assert field in trend, f"Missing trend field: {field}"

    def test_cache_trends_rates_sum_to_one(self, client):
        """TC-CT03: hit_rate + miss_rate ≈ 1.0 for each point."""
        resp = client.get(
            "/query/cache-trends?start_date=2025-10-01&end_date=2025-10-03"
        )
        for trend in resp.json()["trends"]:
            total = trend["hit_rate"] + trend["miss_rate"]
            assert abs(total - 1.0) < 1e-6, f"Rates don't sum to 1.0: {trend}"

    def test_cache_trends_empty_range(self, client):
        """TC-CT04: Empty date range returns empty trends."""
        resp = client.get(
            "/query/cache-trends?start_date=2024-01-01&end_date=2024-01-05"
        )
        assert resp.status_code == 200
        assert resp.json()["trends"] == []

    def test_cache_trends_positive_hit_rate(self, client):
        """TC-CT05: Days with cache_tokens entries have hit_rate > 0."""
        resp = client.get(
            "/query/cache-trends?start_date=2025-10-01&end_date=2025-10-01"
        )
        trend = resp.json()["trends"][0]
        # Fixture data has cache_tokens for even-indexed entries
        assert trend["hit_rate"] > 0.0

    def test_cache_trends_sorted_ascending(self, client):
        """TC-CT06: Trend points are in chronological order."""
        resp = client.get(
            "/query/cache-trends?start_date=2025-10-01&end_date=2025-10-03"
        )
        timestamps = [t["timestamp"] for t in resp.json()["trends"]]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# 6. /query/compression-ratio
# ---------------------------------------------------------------------------

class TestQueryCompressionRatio:
    """GET /query/compression-ratio"""

    def test_compression_ratio_happy_path(self, client):
        """TC-CR01: Returns per-agent compression ratios."""
        resp = client.get("/query/compression-ratio?date=2025-10-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        compression = data["compression"]
        assert isinstance(compression, list)

    def test_compression_ratio_fields(self, client):
        """TC-CR02: Each entry has agent_id, avg_compression_ratio, sample_count."""
        resp = client.get("/query/compression-ratio?date=2025-10-01")
        compression = resp.json()["compression"]
        if compression:
            item = compression[0]
            assert "agent_id" in item
            assert "avg_compression_ratio" in item
            assert "sample_count" in item

    def test_compression_ratio_only_entries_with_data(self, client):
        """TC-CR03: Only agents with compression_ratio in extra appear."""
        resp = client.get("/query/compression-ratio?date=2025-10-01")
        compression = resp.json()["compression"]
        # Fixture: compression_ratio only set for i % 3 == 0 → some agents have data
        # All avg_compression_ratio values should be > 0
        for item in compression:
            assert item["avg_compression_ratio"] > 0

    def test_compression_ratio_no_activity(self, client):
        """TC-CR04: Date with no data returns empty list."""
        resp = client.get("/query/compression-ratio?date=2024-01-01")
        assert resp.status_code == 200
        assert resp.json()["compression"] == []

    def test_compression_ratio_sample_count_positive(self, client):
        """TC-CR05: sample_count is positive for all returned agents."""
        resp = client.get("/query/compression-ratio?date=2025-10-01")
        for item in resp.json()["compression"]:
            assert item["sample_count"] > 0


# ---------------------------------------------------------------------------
# 7. /query/usage-summary
# ---------------------------------------------------------------------------

class TestQueryUsageSummary:
    """GET /query/usage-summary"""

    def test_usage_summary_happy_path(self, client):
        """TC-US01: Returns correct daily totals for a known date."""
        resp = client.get("/query/usage-summary?date=2025-10-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        summary = data["summary"]
        assert summary["date"] == "2025-10-01"
        assert summary["total_requests"] == ENTRIES_PER_DAY

    def test_usage_summary_token_total(self, client):
        """TC-US02: total_tokens matches fixture sum."""
        expected = sum(100 + i * 50 for i in range(ENTRIES_PER_DAY))
        resp = client.get("/query/usage-summary?date=2025-10-01")
        summary = resp.json()["summary"]
        assert summary["total_tokens"] == expected

    def test_usage_summary_cache_tokens(self, client):
        """TC-US03: cache_tokens is non-zero (fixture has cache hits)."""
        resp = client.get("/query/usage-summary?date=2025-10-01")
        summary = resp.json()["summary"]
        assert summary["cache_tokens"] > 0

    def test_usage_summary_unique_agents(self, client):
        """TC-US04: unique_agents equals number of distinct agents in fixture."""
        resp = client.get("/query/usage-summary?date=2025-10-01")
        summary = resp.json()["summary"]
        assert summary["unique_agents"] == len(AGENTS)

    def test_usage_summary_empty_date(self, client):
        """TC-US05: Date with no data returns zero totals."""
        resp = client.get("/query/usage-summary?date=2024-01-01")
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["total_requests"] == 0
        assert summary["total_tokens"] == 0
        assert summary["unique_agents"] == 0

    def test_usage_summary_fields(self, client):
        """TC-US06: Summary has all required fields."""
        resp = client.get("/query/usage-summary?date=2025-10-01")
        summary = resp.json()["summary"]
        for field in ["date", "total_requests", "total_tokens", "cache_tokens",
                      "avg_compression", "unique_agents"]:
            assert field in summary, f"Missing summary field: {field}"


# ---------------------------------------------------------------------------
# 8. POST /query/export
# ---------------------------------------------------------------------------

class TestQueryExport:
    """POST /query/export"""

    def test_export_returns_csv(self, client):
        """TC-EX01: Export returns CSV content-type."""
        resp = client.post(
            "/query/export",
            json={"start_date": "2025-10-01", "end_date": "2025-10-01"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/csv" in ct

    def test_export_csv_headers(self, client):
        """TC-EX02: CSV export contains expected column headers."""
        resp = client.post(
            "/query/export",
            json={"start_date": "2025-10-01", "end_date": "2025-10-01"},
        )
        assert resp.status_code == 200
        lines = resp.text.strip().splitlines()
        header = lines[0]
        for col in ["id", "timestamp", "agent", "model", "provider", "tokens", "cost"]:
            assert col in header, f"Missing CSV header: {col}"

    def test_export_csv_row_count(self, client):
        """TC-EX03: CSV export row count = header + entries."""
        resp = client.post(
            "/query/export",
            json={"start_date": "2025-10-01", "end_date": "2025-10-01"},
        )
        lines = resp.text.strip().splitlines()
        # 1 header + ENTRIES_PER_DAY data rows
        assert len(lines) == ENTRIES_PER_DAY + 1, \
            f"Expected {ENTRIES_PER_DAY + 1} lines, got {len(lines)}"

    def test_export_multi_day_row_count(self, client):
        """TC-EX04: Multi-day CSV export includes all entries."""
        resp = client.post(
            "/query/export",
            json={"start_date": "2025-10-01", "end_date": "2025-10-03"},
        )
        assert resp.status_code == 200
        lines = resp.text.strip().splitlines()
        assert len(lines) == ENTRIES_PER_DAY * 3 + 1

    def test_export_csv_content_disposition(self, client):
        """TC-EX05: Content-Disposition header includes filename."""
        resp = client.post(
            "/query/export",
            json={"start_date": "2025-10-01", "end_date": "2025-10-01"},
        )
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "filename" in cd

    def test_export_empty_range_404(self, client):
        """TC-EX06: Export for date range with no data returns 404."""
        resp = client.post(
            "/query/export",
            json={"start_date": "2024-01-01", "end_date": "2024-01-01"},
        )
        assert resp.status_code == 404

    def test_export_csv_valid_data(self, client):
        """TC-EX07: Each CSV data row has non-empty id and parseable tokens."""
        import csv
        resp = client.post(
            "/query/export",
            json={"start_date": "2025-10-01", "end_date": "2025-10-01"},
        )
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == ENTRIES_PER_DAY
        for row in rows:
            assert row["id"], "id should not be empty"
            assert int(row["tokens"]) >= 0, f"tokens not parseable: {row['tokens']}"


# ---------------------------------------------------------------------------
# 9. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Misc edge cases across all endpoints."""

    def test_entries_limit_zero_rejected(self, client):
        """TC-EC01: limit=0 is rejected (FastAPI ge=1 constraint → 422)."""
        resp = client.get("/query/entries?start_date=2025-10-01&end_date=2025-10-01&limit=0")
        assert resp.status_code == 422

    def test_entries_single_day(self, client):
        """TC-EC02: start_date == end_date returns exactly that day's entries."""
        resp = client.get("/query/entries?start_date=2025-10-02&end_date=2025-10-02")
        data = resp.json()
        assert data["count"] == ENTRIES_PER_DAY
        for entry in data["entries"]:
            assert entry["timestamp"].startswith("2025-10-02")

    def test_stats_total_cost_positive(self, client):
        """TC-EC03: Stats total_cost > 0 for day with entries."""
        resp = client.get("/query/stats?date=2025-10-01")
        stats = resp.json()["stats"]
        assert stats["total_cost"] > 0

    def test_rollups_tokens_match_entries(self, client):
        """TC-EC04: Sum of rollup total_tokens = sum of all entry tokens for that day."""
        expected_tokens = sum(100 + i * 50 for i in range(ENTRIES_PER_DAY))
        resp = client.get(
            "/query/rollups?start_date=2025-10-01&end_date=2025-10-01&window_minutes=5"
        )
        rollup_tokens = sum(b["total_tokens"] for b in resp.json()["rollups"])
        assert rollup_tokens == expected_tokens

    def test_top_users_request_count_sum(self, client):
        """TC-EC05: Sum of top-user request counts = total entries for that day."""
        resp = client.get("/query/top-users?date=2025-10-01&limit=100")
        total = sum(u["request_count"] for u in resp.json()["users"])
        assert total == ENTRIES_PER_DAY

    def test_usage_summary_future_date(self, client):
        """TC-EC06: Usage summary for a future date returns zeros gracefully."""
        resp = client.get("/query/usage-summary?date=2099-01-01")
        assert resp.status_code == 200
        assert resp.json()["summary"]["total_requests"] == 0

    def test_cache_trends_future_range(self, client):
        """TC-EC07: Cache trends for future range returns empty list."""
        resp = client.get(
            "/query/cache-trends?start_date=2099-01-01&end_date=2099-01-31"
        )
        assert resp.status_code == 200
        assert resp.json()["trends"] == []

    def test_health_endpoint(self, client):
        """TC-EC08: /health endpoint returns 200."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
