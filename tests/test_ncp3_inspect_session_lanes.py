# SPDX-License-Identifier: Apache-2.0
"""NCP-3 — read-only session-lane inspection harness tests.

Coverage:

  1. session-collapse verdict — collapsed / rotating / partial / indeterminate
  2. time-clustering verdict — concurrent / serialized / mixed
  3. status distribution — 200 / 429 / 5xx / other
  4. per-session duration percentiles
  5. provider audit + I-0 violation detection
  6. retry-count lower bound
  7. token usage cache_hit_ratio
  8. interleaving score
  9. JSON + markdown render parity
  10. CLI subprocess smokes
  11. missing telemetry.db handled cleanly
  12. structural — script imports no dispatch / runtime primitives
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    path = SCRIPT_DIR / name
    spec = importlib.util.spec_from_file_location(
        f"_ncp3_test_{path.stem}", path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


inspect = _load("inspect_session_lanes.py")


# ── Fixtures ──────────────────────────────────────────────────────────


def _seed(
    db: Path,
    *,
    rows: list,  # list of dicts: ts, session_id, request_id, status, duration_ms, provider, route, error_class, trace_id
) -> None:
    """Seed tp_events + (optionally) tp_usage."""
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tp_events (
            request_id TEXT, trace_id TEXT, ts TEXT, provider TEXT,
            model TEXT, agent_id TEXT, api TEXT, stop_reason TEXT,
            session_id TEXT, duration_ms INTEGER, status TEXT,
            error_class TEXT, payload TEXT, span_id TEXT,
            node_id TEXT, route TEXT
        );
        """
    )
    for r in rows:
        conn.execute(
            "INSERT INTO tp_events (request_id, trace_id, ts, provider, "
            "model, session_id, duration_ms, status, error_class, route) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                r.get("request_id"),
                r.get("trace_id"),
                r["ts"],
                r.get("provider", "tokenpak-claude-code"),
                r.get("model", "claude-3-5-sonnet"),
                r.get("session_id"),
                r.get("duration_ms"),
                r.get("status", "200"),
                r.get("error_class"),
                r.get("route", "claude-code"),
            ),
        )
    conn.commit()
    conn.close()


# ── 1. session collapse verdict ───────────────────────────────────────


class TestSessionCollapseVerdict:

    def test_collapsed_one_session_many_requests(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s-1", "request_id": f"r-{i}",
             "trace_id": f"t-{i}", "duration_ms": 1000}
            for i in range(8)
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d1 = rep["dim1_session_collapse"]
        assert d1["verdict"] == "collapsed"
        assert d1["distinct_session_ids"] == 1
        assert d1["distinct_request_ids"] == 8

    def test_rotating_session_per_request(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": f"s-{i}", "request_id": f"r-{i}",
             "trace_id": f"t-{i}", "duration_ms": 1000}
            for i in range(8)
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        assert rep["dim1_session_collapse"]["verdict"] == "rotating"

    def test_no_data_when_empty(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d1 = rep["dim1_session_collapse"]
        assert d1["verdict"] == "no_data"
        assert d1["distinct_session_ids"] == 0


# ── 2. time clustering verdict ────────────────────────────────────────


class TestTimeClustering:

    def test_concurrent_short_inter_request_gap(self, tmp_path):
        db = tmp_path / "telemetry.db"
        # Inter-request gap (0.001s) MUCH less than 10% of duration (1s):
        # 0.001 < 0.1 * 1.0 → concurrent
        _seed(db, rows=[
            {"ts": str(1772562000.0 + i * 0.001),
             "session_id": "s-x", "request_id": f"r-{i}",
             "trace_id": f"t-{i}", "duration_ms": 1000}
            for i in range(5)
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        assert rep["dim2_time_clustering"]["verdict"] == "concurrent"

    def test_serialized_inter_request_gap_dominates(self, tmp_path):
        db = tmp_path / "telemetry.db"
        # Inter-request gap (2s) > 50% of duration (1s) → serialized
        _seed(db, rows=[
            {"ts": str(1772562000.0 + i * 2.0),
             "session_id": "s-x", "request_id": f"r-{i}",
             "trace_id": f"t-{i}", "duration_ms": 1000}
            for i in range(5)
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        assert rep["dim2_time_clustering"]["verdict"] == "serialized_or_throttled"


# ── 3. status distribution ────────────────────────────────────────────


class TestStatusDistribution:

    def test_counts_429_5xx_200(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s", "request_id": "r1", "trace_id": "t1",
             "duration_ms": 100, "status": "200"},
            {"ts": ts, "session_id": "s", "request_id": "r2", "trace_id": "t2",
             "duration_ms": 100, "status": "429"},
            {"ts": ts, "session_id": "s", "request_id": "r3", "trace_id": "t3",
             "duration_ms": 100, "status": "503"},
            {"ts": ts, "session_id": "s", "request_id": "r4", "trace_id": "t4",
             "duration_ms": 100, "status": "500"},
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d3 = rep["dim3_status_distribution"]
        assert d3.get("200") == 1
        assert d3.get("429") == 1
        assert d3.get("5xx") == 2


# ── 4. per-session duration percentiles ───────────────────────────────


class TestPerSessionDurations:

    def test_percentiles_per_session(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "fast", "request_id": f"f{i}",
             "trace_id": f"f{i}t", "duration_ms": 100}
            for i in range(10)
        ] + [
            {"ts": ts, "session_id": "slow", "request_id": f"s{i}",
             "trace_id": f"s{i}t", "duration_ms": 5000}
            for i in range(10)
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d4 = rep["dim4_per_session_durations"]
        assert d4["fast"]["p50_ms"] == 100
        assert d4["slow"]["p50_ms"] == 5000


# ── 5. provider audit / I-0 violation ─────────────────────────────────


class TestProviderAudit:

    def test_no_violation_when_only_oauth(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s", "request_id": "r1", "trace_id": "t1",
             "duration_ms": 100, "provider": "tokenpak-claude-code"},
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d5 = rep["dim5_provider_audit"]
        assert d5["i0_violation"] is False
        assert d5["non_oauth_providers"] == []

    def test_violation_when_anthropic_api_key_route(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s", "request_id": "r1", "trace_id": "t1",
             "duration_ms": 100, "provider": "tokenpak-claude-code"},
            {"ts": ts, "session_id": "s", "request_id": "r2", "trace_id": "t2",
             "duration_ms": 100, "provider": "anthropic"},
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d5 = rep["dim5_provider_audit"]
        assert d5["i0_violation"] is True
        assert "anthropic" in d5["non_oauth_providers"]


# ── 6. retry count lower bound ────────────────────────────────────────


class TestRetryCount:

    def test_retry_count_from_error_class(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s", "request_id": "r1", "trace_id": "t1",
             "duration_ms": 100, "error_class": "retry"},
            {"ts": ts, "session_id": "s", "request_id": "r2", "trace_id": "t2",
             "duration_ms": 100, "error_class": "retry"},
            {"ts": ts, "session_id": "s", "request_id": "r3", "trace_id": "t3",
             "duration_ms": 100, "error_class": None},
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        assert rep["dim6_retry_count"]["retry_event_lower_bound"] == 2


# ── 7. token usage / cache hit ratio ──────────────────────────────────


class TestTokenUsage:

    def test_cache_hit_ratio_computed(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s", "request_id": "r1", "trace_id": "t1",
             "duration_ms": 100},
        ])
        # Add tp_usage row
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tp_usage (
                trace_id TEXT, usage_source TEXT, confidence REAL,
                input_billed INTEGER, output_billed INTEGER,
                input_est INTEGER, output_est INTEGER,
                cache_read INTEGER, cache_write INTEGER,
                total_tokens INTEGER, total_tokens_billed INTEGER,
                total_tokens_est INTEGER, provider_usage_raw TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO tp_usage (trace_id, input_billed, output_billed, "
            "cache_read, cache_write) VALUES (?,?,?,?,?)",
            ("t1", 1000, 500, 800, 200),
        )
        conn.commit()
        conn.close()
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d7 = rep["dim7_token_usage"]
        assert d7["available"] is True
        assert d7["cache_hit_ratio"] == 0.8


# ── 8. interleaving score ─────────────────────────────────────────────


class TestInterleaving:

    def test_serialized_when_all_one_session(self, tmp_path):
        db = tmp_path / "telemetry.db"
        # All requests in one session_id → 0% interleave score
        _seed(db, rows=[
            {"ts": str(1772562000.0 + i),
             "session_id": "s-only", "request_id": f"r-{i}",
             "trace_id": f"t-{i}", "duration_ms": 100}
            for i in range(6)
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        assert rep["dim8_interleaving"]["verdict"] == "serialized"
        assert rep["dim8_interleaving"]["interleave_score"] == 0.0

    def test_interleaved_when_alternating(self, tmp_path):
        db = tmp_path / "telemetry.db"
        # Alternating session_id → 100% interleave score
        _seed(db, rows=[
            {"ts": str(1772562000.0 + i),
             "session_id": ("a" if i % 2 == 0 else "b"),
             "request_id": f"r-{i}", "trace_id": f"t-{i}",
             "duration_ms": 100}
            for i in range(6)
        ])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        assert rep["dim8_interleaving"]["verdict"] == "interleaved"


# ── 9. JSON + markdown render parity ──────────────────────────────────


class TestRender:

    def test_main_writes_markdown(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s", "request_id": "r1", "trace_id": "t1",
             "duration_ms": 100},
        ])
        out = tmp_path / "report.md"
        rc = inspect.main([
            "--db-path", str(db),
            "--window-minutes", "0",
            "--output", str(out),
        ])
        assert rc == 0
        md = out.read_text()
        assert "# NCP-3 session-lane trace" in md
        assert "Synthesis" in md
        assert "Q1 — H2" in md

    def test_main_writes_json(self, tmp_path):
        db = tmp_path / "telemetry.db"
        ts = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts, "session_id": "s-1", "request_id": "r1", "trace_id": "t1",
             "duration_ms": 100},
            {"ts": ts, "session_id": "s-1", "request_id": "r2", "trace_id": "t2",
             "duration_ms": 100},
            {"ts": ts, "session_id": "s-1", "request_id": "r3", "trace_id": "t3",
             "duration_ms": 100},
            {"ts": ts, "session_id": "s-1", "request_id": "r4", "trace_id": "t4",
             "duration_ms": 100},
            {"ts": ts, "session_id": "s-1", "request_id": "r5", "trace_id": "t5",
             "duration_ms": 100},
        ])
        out = tmp_path / "report.json"
        rc = inspect.main([
            "--db-path", str(db),
            "--window-minutes", "0",
            "--json",
            "--output", str(out),
        ])
        assert rc == 0
        d = json.loads(out.read_text())
        assert d["dim1_session_collapse"]["verdict"] == "collapsed"


# ── 10. CLI subprocess smoke ──────────────────────────────────────────


class TestCliSmoke:

    def test_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "inspect_session_lanes.py"), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--window-minutes" in result.stdout


# ── 11. missing telemetry.db handled cleanly ──────────────────────────


class TestMissingDb:

    def test_returns_error_dict(self, tmp_path):
        rep = inspect.analyze(
            db_path=tmp_path / "missing.db",
            window_minutes=0,
        )
        assert "error" in rep

    def test_main_returns_2_on_missing_db(self, tmp_path):
        rc = inspect.main([
            "--db-path", str(tmp_path / "missing.db"),
            "--window-minutes", "0",
        ])
        assert rc == 2


# ── 11.5. NCP-3I dim 9 parity-trace coverage ──────────────────────────


class TestParityTraceCoverage:
    """Verify the harness consumes the NCP-3I tp_parity_trace table."""

    def _seed_parity_trace(self, db: Path, rows: list) -> None:
        """Seed tp_parity_trace rows alongside tp_events.

        Each row dict: trace_id, event_type, ts (epoch float).
        """
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tp_parity_trace (
                trace_id TEXT NOT NULL, event_type TEXT NOT NULL,
                ts REAL NOT NULL, pid INTEGER, ppid INTEGER,
                tokenpak_home TEXT, telemetry_db_path TEXT,
                request_id TEXT, session_id TEXT, provider TEXT,
                auth_plane TEXT, credential_class TEXT,
                retry_phase TEXT, retry_owner TEXT, retry_signal TEXT,
                retry_count INTEGER, retry_after_seconds REAL,
                tool_command_first TEXT,
                tool_result_stdout_chars INTEGER,
                tool_result_stderr_chars INTEGER,
                tool_result_tokens_est INTEGER,
                body_bytes INTEGER, companion_added_chars INTEGER,
                intent_guidance_chars INTEGER,
                queue_wait_ms REAL, lock_wait_ms REAL,
                sqlite_write_ms REAL, notes TEXT
            );
            """
        )
        for r in rows:
            conn.execute(
                "INSERT INTO tp_parity_trace (trace_id, event_type, ts) "
                "VALUES (?,?,?)",
                (r["trace_id"], r["event_type"], r["ts"]),
            )
        conn.commit()
        conn.close()

    def test_dim9_unavailable_when_table_missing(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d9 = rep["dim9_parity_trace_coverage"]
        assert d9["available"] is False

    def test_dim9_zero_rows_when_window_empty(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        # Create the table but no rows.
        self._seed_parity_trace(db, rows=[])
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d9 = rep["dim9_parity_trace_coverage"]
        assert d9["available"] is True
        assert d9["row_count"] == 0

    def test_dim9_interp_b_supported_entry_without_completion(self, tmp_path):
        # 3 traces have handler_entry; 0 ever land in tp_events.
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])  # empty tp_events
        ts = 1772562000.0
        self._seed_parity_trace(
            db,
            rows=[
                {"trace_id": f"trace-{i}", "event_type": "handler_entry",
                 "ts": ts + i}
                for i in range(3)
            ],
        )
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d9 = rep["dim9_parity_trace_coverage"]
        assert d9["available"] is True
        assert d9["traces_with_handler_entry"] == 3
        assert d9["traces_with_completion_in_tp_events"] == 0
        assert d9["interp_b_count"] == 3
        assert d9["verdict"] == "interp_b_supported"

    def test_dim9_interp_a_clean_when_completions_match(self, tmp_path):
        # 3 traces with handler_entry, all also in tp_events.
        db = tmp_path / "telemetry.db"
        ts_iso = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts_iso, "session_id": "s", "request_id": f"trace-{i}",
             "trace_id": f"trace-{i}", "duration_ms": 100}
            for i in range(3)
        ])
        ts = 1772562000.0
        self._seed_parity_trace(
            db,
            rows=[
                {"trace_id": f"trace-{i}", "event_type": "handler_entry",
                 "ts": ts + i}
                for i in range(3)
            ],
        )
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d9 = rep["dim9_parity_trace_coverage"]
        assert d9["traces_with_handler_entry"] == 3
        assert d9["traces_with_completion_in_tp_events"] == 3
        assert d9["interp_b_count"] == 0
        assert d9["verdict"] == "interp_a_or_clean"

    def test_dim9_renders_in_synthesis(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(
            db,
            rows=[
                {"trace_id": f"trace-{i}", "event_type": "handler_entry",
                 "ts": ts + i}
                for i in range(2)
            ],
        )
        out = tmp_path / "report.md"
        rc = inspect.main([
            "--db-path", str(db),
            "--window-minutes", "0",
            "--output", str(out),
        ])
        assert rc == 0
        md = out.read_text()
        assert "Q8 — NCP-3I parity trace" in md


# ── 12. structural — no dispatch / behavior imports ───────────────────


class TestNoBehaviorChanges:

    def test_imports_no_dispatch_primitives(self):
        text = (SCRIPT_DIR / "inspect_session_lanes.py").read_text()
        forbidden = (
            "from tokenpak.proxy.client import",
            "from tokenpak.proxy.server import",
            "from tokenpak.proxy.connection_pool import",
            "from tokenpak.companion",
            "credential_injector",
            "forward_headers",
            "pool.request",
            "pool.stream",
            "RoutingService",
        )
        for f in forbidden:
            assert f not in text, (
                f"NCP-3 inspection script must not import dispatch primitive: {f}"
            )

    def test_does_not_write_to_telemetry_tables(self):
        text = (SCRIPT_DIR / "inspect_session_lanes.py").read_text()
        for forbidden in (
            "INSERT INTO tp_",
            "UPDATE tp_",
            "DELETE FROM tp_",
            "DROP TABLE",
            "ALTER TABLE",
        ):
            assert forbidden not in text, (
                f"NCP-3 script must not modify telemetry: {forbidden}"
            )
