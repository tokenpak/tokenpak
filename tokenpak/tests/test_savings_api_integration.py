"""Integration tests for the savings/compare/leaderboard read path.

The previous version of this file built two schemas (``audit_log``,
``monitor_log``) that no production code reads or writes, and every
assertion checked properties of hand-written example dicts rather than
exercising any real code path — it could not have caught the defect this
replaces: ``tokenpak savings`` / ``compare`` / ``leaderboard`` reading a
database the live proxy never writes to.

These tests instead seed the real ``requests`` table — the schema created by
``tokenpak.proxy.monitor.Monitor`` and populated by the live proxy on every
completed request — and assert that ``tokenpak.telemetry.query_dsl`` (and the
CLI verbs built on it) surface that data correctly.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tokenpak.proxy.monitor import Monitor
from tokenpak.telemetry import query_dsl
from tokenpak.telemetry.pricing_rates import get_rates

REPO_ROOT = Path(__file__).resolve().parents[2]


def _insert_request(
    db_path,
    *,
    timestamp: str | None = None,
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost: float = 0.0,
    compressed_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_origin: str = "proxy",
    status_code: int = 200,
    agent_id: str = "",
) -> None:
    """Seed one row of the real ``requests`` schema.

    Inserted directly via SQL (bypassing ``Monitor``'s async write queue) for
    determinism, the same pattern used by ``tests/proxy/test_monitor_write_truth.py``.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, "
            "estimated_cost, compressed_tokens, cache_read_tokens, cache_origin, "
            "status_code, agent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp or datetime.now().isoformat(),
                model,
                input_tokens,
                output_tokens,
                estimated_cost,
                compressed_tokens,
                cache_read_tokens,
                cache_origin,
                status_code,
                agent_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def monitor_db(tmp_path: Path) -> Path:
    """A real monitor.db: schema created the same way the live proxy creates it."""
    db_path = tmp_path / "monitor.db"
    Monitor(db_path=str(db_path))  # builds schema via _init_db(); inserts no rows
    return db_path


class TestQueryDslReadsRealSchema:
    """``query_dsl`` must read the schema the live proxy actually writes."""

    def test_get_savings_report_reads_seeded_requests(self, monitor_db: Path) -> None:
        _insert_request(
            monitor_db,
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=200,
            estimated_cost=0.05,
            compressed_tokens=5000,
            cache_read_tokens=2000,
            cache_origin="proxy",
        )

        report = query_dsl.get_savings_report(db_path=monitor_db, days=30)

        assert report.available is True
        assert report.observations == 1
        assert report.total_cost == pytest.approx(0.05)
        assert report.savings_amount > 0
        assert report.estimated_without_compression == pytest.approx(
            report.total_cost + report.savings_amount
        )

    def test_get_savings_report_excludes_non_proxy_cache_origin(self, monitor_db: Path) -> None:
        """Only cache_origin='proxy' rows are credited toward savings_amount."""
        _insert_request(
            monitor_db,
            model="claude-sonnet-4-6",
            estimated_cost=0.01,
            compressed_tokens=1000,
            cache_read_tokens=500,
            cache_origin="proxy",
        )
        _insert_request(
            monitor_db,
            model="claude-sonnet-4-6",
            estimated_cost=0.01,
            compressed_tokens=1000,
            cache_read_tokens=500,
            cache_origin="client",
        )

        report = query_dsl.get_savings_report(db_path=monitor_db, days=30)

        rates = get_rates("claude-sonnet-4-6")
        expected = (1000 / 1_000_000) * rates["input"] + (500 / 1_000_000) * max(
            rates["input"] - rates["cached"], 0.0
        )
        # Both rows count toward total_cost/observations; only the proxy-origin
        # row's compression/cache-read tokens are credited as savings.
        assert report.observations == 2
        assert report.total_cost == pytest.approx(0.02)
        assert report.savings_amount == pytest.approx(expected)

    def test_get_model_usage_reads_seeded_requests(self, monitor_db: Path) -> None:
        _insert_request(monitor_db, model="claude-sonnet-4-6", input_tokens=100, output_tokens=50)
        _insert_request(monitor_db, model="claude-sonnet-4-6", input_tokens=200, output_tokens=75)
        _insert_request(monitor_db, model="claude-haiku-4-5", input_tokens=10, output_tokens=5)

        usage = query_dsl.get_model_usage(db_path=monitor_db, days=30)
        by_model = {u.model: u for u in usage}

        assert by_model["claude-sonnet-4-6"].request_count == 2
        assert by_model["claude-sonnet-4-6"].total_input_tokens == 300
        assert by_model["claude-sonnet-4-6"].total_output_tokens == 125
        assert by_model["claude-haiku-4-5"].request_count == 1

    def test_get_recent_events_reads_seeded_requests(self, monitor_db: Path) -> None:
        _insert_request(
            monitor_db,
            model="claude-sonnet-4-6",
            input_tokens=42,
            output_tokens=7,
            estimated_cost=0.002,
            status_code=200,
        )

        events = query_dsl.get_recent_events(db_path=monitor_db, limit=10)

        assert len(events) == 1
        evt = events[0]
        assert evt["model"] == "claude-sonnet-4-6"
        assert evt["input_tokens"] == 42
        assert evt["output_tokens"] == 7
        assert evt["cost"] == pytest.approx(0.002)
        assert evt["status"] == "ok"

    def test_get_recent_events_marks_error_status(self, monitor_db: Path) -> None:
        _insert_request(monitor_db, model="claude-sonnet-4-6", status_code=500)

        events = query_dsl.get_recent_events(db_path=monitor_db, limit=10)

        assert events[0]["status"] == "error"
        assert events[0]["error_class"] == "http_500"

    def test_get_cost_summary_reads_seeded_requests(self, monitor_db: Path) -> None:
        _insert_request(monitor_db, model="claude-sonnet-4-6", estimated_cost=0.10)
        _insert_request(monitor_db, model="claude-haiku-4-5", estimated_cost=0.02)

        summary = query_dsl.get_cost_summary(db_path=monitor_db, days=30)

        assert summary.total_cost == pytest.approx(0.12)
        assert summary.by_model["claude-sonnet-4-6"] == pytest.approx(0.10)
        assert summary.by_model["claude-haiku-4-5"] == pytest.approx(0.02)

    def test_get_daily_trend_reads_seeded_requests(self, monitor_db: Path) -> None:
        _insert_request(
            monitor_db,
            model="claude-sonnet-4-6",
            estimated_cost=0.03,
            input_tokens=10,
            output_tokens=5,
        )

        trend = query_dsl.get_daily_trend(db_path=monitor_db, days=30)

        assert len(trend) == 1
        assert trend[0].cost == pytest.approx(0.03)
        assert trend[0].request_count == 1

    def test_get_model_compression_breakdown_reads_seeded_requests(self, monitor_db: Path) -> None:
        _insert_request(
            monitor_db,
            model="claude-sonnet-4-6",
            input_tokens=100,
            compressed_tokens=400,
            cache_origin="proxy",
        )

        breakdown = query_dsl.get_model_compression_breakdown(db_path=monitor_db, days=1)

        assert len(breakdown) == 1
        b = breakdown[0]
        assert b.model == "claude-sonnet-4-6"
        assert b.tokens_saved == 400
        assert b.avg_raw_tokens == pytest.approx(500.0)
        assert b.avg_final_tokens == pytest.approx(100.0)
        assert b.savings_amount > 0

    def test_stale_rows_excluded_by_days_window(self, monitor_db: Path) -> None:
        stale = (datetime.now() - timedelta(days=400)).isoformat()
        _insert_request(monitor_db, timestamp=stale, model="claude-sonnet-4-6", estimated_cost=1.0)

        report = query_dsl.get_savings_report(db_path=monitor_db, days=30)

        assert report.available is True
        assert report.observations == 0
        assert report.total_cost == 0.0

    def test_error_status_rows_excluded(self, monitor_db: Path) -> None:
        """status_code >= 400 (failed requests) must not count as billed usage."""
        _insert_request(monitor_db, model="claude-sonnet-4-6", estimated_cost=5.0, status_code=500)

        report = query_dsl.get_savings_report(db_path=monitor_db, days=30)

        assert report.observations == 0
        assert report.total_cost == 0.0


class TestCliSurfacesRealMonitorData:
    """The exact deviation the audit found: a user with real proxy traffic in
    monitor.db got "no data yet" from these CLI verbs regardless. Seed the
    real store the proxy would have written and confirm each verb reports
    the data instead.
    """

    @pytest.fixture()
    def home_with_monitor_db(self, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        tpk_dir = home / ".tpk"
        tpk_dir.mkdir(parents=True)
        (tpk_dir / ".seen_intro").touch()
        db_path = tpk_dir / "monitor.db"
        Monitor(db_path=str(db_path))
        _insert_request(
            db_path,
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=200,
            estimated_cost=0.05,
            compressed_tokens=5000,
            cache_read_tokens=2000,
            cache_origin="proxy",
            status_code=200,
        )
        return home

    def _run_cli(self, home: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "tokenpak.cli", *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={
                "HOME": str(home),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "NO_COLOR": "1",
                "TERM": "dumb",
                "TOKENPAK_PORT": "8899",
            },
            timeout=180,
        )

    def test_savings_surfaces_seeded_data(self, home_with_monitor_db: Path) -> None:
        result = self._run_cli(home_with_monitor_db, "savings")
        combined = result.stdout + result.stderr

        assert result.returncode == 0, combined
        assert "No savings data yet" not in combined, combined

    def test_compare_surfaces_seeded_data(self, home_with_monitor_db: Path) -> None:
        result = self._run_cli(home_with_monitor_db, "compare")
        combined = result.stdout + result.stderr

        assert result.returncode == 0, combined
        assert "No recent requests found" not in combined, combined
        assert "claude-sonnet-4-6" in combined

    def test_leaderboard_surfaces_seeded_data(self, home_with_monitor_db: Path) -> None:
        result = self._run_cli(home_with_monitor_db, "leaderboard")
        combined = result.stdout + result.stderr

        assert result.returncode == 0, combined
        assert "No model usage data available" not in combined, combined
        assert "claude-sonnet-4-6" in combined


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
