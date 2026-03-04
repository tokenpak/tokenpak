"""Tests for TokenPak Dashboard Session Filter & Search.

Covers all acceptance criteria:
  AC 1 — Filter bar UI component exists on disk
  AC 2 — GET /v1/sessions API endpoint integration (via SessionFilter directly)
  AC 3 — Frontend FilterBar.tsx references correct endpoint
  AC 4 — Result count metadata returned
  AC 5 — No regressions (existing imports still work)
  AC 6 — Empty results without crash

Test groups:
  1.  Filter by model (exact match)
  2.  Filter by date range (from/to)
  3.  Filter by status (success/error/partial/all)
  4.  Combined filters
  5.  Empty results (no crash)
  6.  Pagination (limit/offset)
  7.  FilterParams validation
  8.  distinct_models()
  9.  Regression: existing proxy imports
  10. FilterBar TSX component existence
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator, List

import pytest

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------
try:
    from tokenpak.agent.dashboard.session_filter import (
        FilterParams,
        SessionFilter,
        VALID_STATUSES,
        SESSION_COLUMNS,
    )
except ImportError as exc:
    pytest.fail(f"Failed to import session_filter: {exc}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_db(conn: sqlite3.Connection, rows: List[Dict[str, Any]]) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms INTEGER,
            status_code INTEGER,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER,
            compressed_tokens INTEGER
        )
    """)
    for r in rows:
        conn.execute(
            """INSERT INTO requests
               (timestamp, model, request_type, input_tokens, output_tokens,
                estimated_cost, latency_ms, status_code, endpoint, compilation_mode,
                protected_tokens, compressed_tokens)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("timestamp", "2026-03-01T12:00:00"),
                r.get("model", "gpt-4o"),
                r.get("request_type", "chat"),
                r.get("input_tokens", 1000),
                r.get("output_tokens", 200),
                r.get("estimated_cost", 0.005),
                r.get("latency_ms", 300),
                r.get("status_code", 200),
                r.get("endpoint", "https://api.openai.com/v1/chat/completions"),
                r.get("compilation_mode", "hybrid"),
                r.get("protected_tokens", 0),
                r.get("compressed_tokens", 0),
            ),
        )
    conn.commit()


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Yields path to a temp SQLite DB seeded with test rows."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    rows = [
        # success rows
        {"timestamp": "2026-03-01T10:00:00", "model": "gpt-4o",            "status_code": 200, "input_tokens": 1000},
        {"timestamp": "2026-03-02T11:00:00", "model": "gpt-4o",            "status_code": 200, "input_tokens": 2000},
        {"timestamp": "2026-03-03T12:00:00", "model": "claude-sonnet-4-6", "status_code": 200, "input_tokens": 3000},
        {"timestamp": "2026-03-04T09:00:00", "model": "claude-sonnet-4-6", "status_code": 200, "input_tokens": 500},
        # error rows
        {"timestamp": "2026-03-01T14:00:00", "model": "gpt-4o",            "status_code": 400, "input_tokens": 100},
        {"timestamp": "2026-03-02T15:00:00", "model": "claude-opus-4-5",   "status_code": 401, "input_tokens": 200},
        {"timestamp": "2026-03-03T16:00:00", "model": "claude-opus-4-5",   "status_code": 404, "input_tokens": 300},
        # partial row (3xx)
        {"timestamp": "2026-03-02T08:00:00", "model": "gpt-4o",            "status_code": 301, "input_tokens": 50},
        # older row (before March)
        {"timestamp": "2026-02-15T10:00:00", "model": "gpt-4o",            "status_code": 200, "input_tokens": 999},
    ]

    with sqlite3.connect(str(db_path)) as conn:
        _seed_db(conn, rows)

    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def sf(temp_db: Path) -> SessionFilter:
    return SessionFilter(db_path=temp_db)


# ---------------------------------------------------------------------------
# 1 — Filter by model (exact match)
# ---------------------------------------------------------------------------

class TestFilterByModel:
    def test_filter_gpt4o_returns_only_gpt4o(self, sf: SessionFilter):
        result = sf.query(FilterParams(model="gpt-4o"))
        for row in result["sessions"]:
            assert row["model"] == "gpt-4o"

    def test_filter_claude_sonnet_returns_correct_rows(self, sf: SessionFilter):
        result = sf.query(FilterParams(model="claude-sonnet-4-6"))
        assert all(r["model"] == "claude-sonnet-4-6" for r in result["sessions"])

    def test_filter_nonexistent_model_returns_empty(self, sf: SessionFilter):
        result = sf.query(FilterParams(model="nonexistent-model-xyz"))
        assert result["sessions"] == []
        assert result["total"] == 0

    def test_no_model_filter_returns_all(self, sf: SessionFilter):
        result = sf.query(FilterParams())
        assert result["total"] == 9  # all seeded rows


# ---------------------------------------------------------------------------
# 2 — Filter by date range
# ---------------------------------------------------------------------------

class TestFilterByDateRange:
    def test_from_date_excludes_older_rows(self, sf: SessionFilter):
        result = sf.query(FilterParams(from_dt="2026-03-01T00:00:00"))
        for row in result["sessions"]:
            assert row["timestamp"] >= "2026-03-01T00:00:00"

    def test_to_date_excludes_newer_rows(self, sf: SessionFilter):
        result = sf.query(FilterParams(to_dt="2026-03-01T23:59:59"))
        for row in result["sessions"]:
            assert row["timestamp"] <= "2026-03-01T23:59:59"

    def test_from_and_to_window(self, sf: SessionFilter):
        result = sf.query(FilterParams(
            from_dt="2026-03-02T00:00:00",
            to_dt="2026-03-02T23:59:59",
        ))
        assert result["total"] == 3  # 3 rows on 2026-03-02
        for row in result["sessions"]:
            assert row["timestamp"].startswith("2026-03-02")

    def test_date_range_before_all_data_returns_empty(self, sf: SessionFilter):
        result = sf.query(FilterParams(
            from_dt="2020-01-01T00:00:00",
            to_dt="2020-12-31T23:59:59",
        ))
        assert result["sessions"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# 3 — Filter by status
# ---------------------------------------------------------------------------

class TestFilterByStatus:
    def test_status_success_returns_200_only(self, sf: SessionFilter):
        result = sf.query(FilterParams(status="success"))
        for row in result["sessions"]:
            assert 200 <= row["status_code"] <= 299

    def test_status_error_returns_4xx_5xx(self, sf: SessionFilter):
        result = sf.query(FilterParams(status="error"))
        for row in result["sessions"]:
            assert row["status_code"] >= 400

    def test_status_partial_returns_3xx(self, sf: SessionFilter):
        result = sf.query(FilterParams(status="partial"))
        for row in result["sessions"]:
            assert 300 <= row["status_code"] <= 399

    def test_status_all_returns_everything(self, sf: SessionFilter):
        result = sf.query(FilterParams(status="all"))
        assert result["total"] == 9

    def test_status_error_count_matches_seed(self, sf: SessionFilter):
        result = sf.query(FilterParams(status="error"))
        assert result["total"] == 3  # 3 error rows seeded

    def test_status_partial_count_matches_seed(self, sf: SessionFilter):
        result = sf.query(FilterParams(status="partial"))
        assert result["total"] == 1  # 1 partial row seeded


# ---------------------------------------------------------------------------
# 4 — Combined filters
# ---------------------------------------------------------------------------

class TestCombinedFilters:
    def test_model_and_status(self, sf: SessionFilter):
        result = sf.query(FilterParams(model="gpt-4o", status="success"))
        assert all(r["model"] == "gpt-4o" for r in result["sessions"])
        assert all(200 <= r["status_code"] <= 299 for r in result["sessions"])

    def test_model_and_date_range(self, sf: SessionFilter):
        result = sf.query(FilterParams(
            model="gpt-4o",
            from_dt="2026-03-01T00:00:00",
            to_dt="2026-03-01T23:59:59",
        ))
        assert all(r["model"] == "gpt-4o" for r in result["sessions"])
        assert all(r["timestamp"].startswith("2026-03-01") for r in result["sessions"])

    def test_model_date_and_status(self, sf: SessionFilter):
        result = sf.query(FilterParams(
            model="gpt-4o",
            from_dt="2026-03-01T00:00:00",
            to_dt="2026-03-04T23:59:59",
            status="success",
        ))
        for row in result["sessions"]:
            assert row["model"] == "gpt-4o"
            assert 200 <= row["status_code"] <= 299

    def test_combined_no_results(self, sf: SessionFilter):
        result = sf.query(FilterParams(
            model="claude-sonnet-4-6",
            status="error",
        ))
        assert result["sessions"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# 5 — Empty results (no crash)
# ---------------------------------------------------------------------------

class TestEmptyResults:
    def test_filter_that_matches_nothing_returns_empty_list(self, sf: SessionFilter):
        result = sf.query(FilterParams(model="i-do-not-exist"))
        assert isinstance(result["sessions"], list)
        assert len(result["sessions"]) == 0

    def test_nonexistent_db_returns_empty(self, tmp_path: Path):
        sf_missing = SessionFilter(db_path=tmp_path / "missing.db")
        result = sf_missing.query(FilterParams())
        assert result["sessions"] == []
        assert result["total"] == 0

    def test_empty_result_has_required_keys(self, sf: SessionFilter):
        result = sf.query(FilterParams(model="nobody"))
        assert "sessions" in result
        assert "total" in result
        assert "limit" in result
        assert "offset" in result


# ---------------------------------------------------------------------------
# 6 — Pagination (limit/offset)
# ---------------------------------------------------------------------------

class TestPagination:
    def test_limit_reduces_rows_returned(self, sf: SessionFilter):
        result = sf.query(FilterParams(limit=3))
        assert len(result["sessions"]) <= 3

    def test_offset_skips_rows(self, sf: SessionFilter):
        result_all = sf.query(FilterParams(limit=500))
        result_offset = sf.query(FilterParams(limit=500, offset=3))
        assert result_offset["sessions"] == result_all["sessions"][3:]

    def test_total_reflects_unfiltered_count(self, sf: SessionFilter):
        result = sf.query(FilterParams(limit=2))
        assert result["total"] == 9  # total in DB
        assert len(result["sessions"]) == 2

    def test_offset_beyond_total_returns_empty(self, sf: SessionFilter):
        result = sf.query(FilterParams(offset=9999))
        assert result["sessions"] == []
        assert result["total"] == 9  # total unchanged

    def test_limit_capped_at_max(self):
        params = FilterParams(limit=99999)
        assert params.limit <= 500

    def test_pagination_order_is_desc_timestamp(self, sf: SessionFilter):
        result = sf.query(FilterParams(limit=500))
        timestamps = [r["timestamp"] for r in result["sessions"]]
        assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# 7 — FilterParams validation
# ---------------------------------------------------------------------------

class TestFilterParamsValidation:
    def test_invalid_status_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid status"):
            FilterParams(status="banana")

    def test_negative_offset_clamps_to_zero(self):
        params = FilterParams(offset=-10)
        assert params.offset == 0

    def test_from_query_string_parses_correctly(self):
        params = FilterParams.from_query_string("model=gpt-4o&status=success&limit=25&offset=10")
        assert params.model == "gpt-4o"
        assert params.status == "success"
        assert params.limit == 25
        assert params.offset == 10

    def test_from_query_string_empty_is_defaults(self):
        params = FilterParams.from_query_string("")
        assert params.model is None
        assert params.status == "all"
        assert params.limit == 50
        assert params.offset == 0

    def test_valid_statuses_set_is_complete(self):
        assert VALID_STATUSES == {"all", "success", "error", "partial"}


# ---------------------------------------------------------------------------
# 8 — distinct_models
# ---------------------------------------------------------------------------

class TestDistinctModels:
    def test_returns_all_seeded_models(self, sf: SessionFilter):
        models = sf.distinct_models()
        assert "gpt-4o" in models
        assert "claude-sonnet-4-6" in models
        assert "claude-opus-4-5" in models

    def test_models_are_sorted(self, sf: SessionFilter):
        models = sf.distinct_models()
        assert models == sorted(models)

    def test_no_duplicates_in_models(self, sf: SessionFilter):
        models = sf.distinct_models()
        assert len(models) == len(set(models))

    def test_missing_db_returns_empty_models(self, tmp_path: Path):
        sf_missing = SessionFilter(db_path=tmp_path / "nope.db")
        assert sf_missing.distinct_models() == []


# ---------------------------------------------------------------------------
# 9 — No regressions (existing proxy imports)
# ---------------------------------------------------------------------------

class TestRegressions:
    def test_proxy_server_imports_cleanly(self):
        from tokenpak.agent.proxy import ProxyServer, start_proxy
        assert ProxyServer is not None

    def test_session_filter_importable_from_dashboard(self):
        from tokenpak.agent.dashboard.session_filter import SessionFilter, FilterParams
        assert SessionFilter is not None
        assert FilterParams is not None

    def test_export_api_still_works(self):
        from tokenpak.agent.dashboard.export_api import ExportAPI
        body, status, headers = ExportAPI.handle(raw_body=b"{}", traces=[])
        assert status == 200

    def test_server_module_has_session_filter(self):
        import tokenpak.agent.proxy.server as srv
        assert hasattr(srv, "SessionFilter")


# ---------------------------------------------------------------------------
# 10 — FilterBar TSX component file
# ---------------------------------------------------------------------------

class TestFilterBarComponent:
    def _tsx_path(self) -> Path:
        return Path(__file__).parents[2] / "dashboard" / "src" / "components" / "FilterBar.tsx"

    def test_filter_bar_tsx_exists(self):
        assert self._tsx_path().exists(), "FilterBar.tsx must exist"

    def test_filter_bar_references_sessions_endpoint(self):
        content = self._tsx_path().read_text()
        assert "/v1/sessions" in content

    def test_filter_bar_has_model_dropdown(self):
        content = self._tsx_path().read_text()
        assert "model" in content.lower()
        assert "select" in content.lower()

    def test_filter_bar_has_date_inputs(self):
        content = self._tsx_path().read_text()
        assert "date" in content.lower()

    def test_filter_bar_has_status_filter(self):
        content = self._tsx_path().read_text()
        assert "status" in content.lower()

    def test_filter_bar_has_apply_button(self):
        content = self._tsx_path().read_text()
        assert "Apply" in content or "apply" in content

    def test_filter_bar_has_clear_action(self):
        content = self._tsx_path().read_text()
        assert "Clear" in content or "clear" in content

    def test_filter_bar_shows_result_count(self):
        content = self._tsx_path().read_text()
        assert "Showing" in content or "showing" in content or "result" in content.lower()
