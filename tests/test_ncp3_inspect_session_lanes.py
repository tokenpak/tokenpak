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
                "INSERT INTO tp_parity_trace "
                "(trace_id, event_type, ts, notes) "
                "VALUES (?,?,?,?)",
                (r["trace_id"], r["event_type"], r["ts"],
                 r.get("notes")),
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

    def test_dim9_interp_b_supported_when_no_terminal_event(self, tmp_path):
        # Issue #73: Q8 verdict is now driven by tp_parity_trace
        # terminal events. 3 handler_entry traces with NO canonical
        # terminal (stream_complete / dispatch_subprocess_complete /
        # request_rejected / stream_abort) → silent-death cohort.
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])  # empty tp_events — irrelevant to verdict
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
        assert d9["traces_without_terminal_event"] == 3
        assert d9["traces_with_wire_completion"] == 0
        assert d9["interp_b_count"] == 3
        assert d9["verdict"] == "interp_b_supported"

    def test_dim9_interp_a_clean_via_stream_complete(self, tmp_path):
        # Issue #73: cleanliness is determined by tp_parity_trace
        # stream_complete events, NOT tp_events rows.
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])  # zero tp_events rows — verdict still clean
        ts = 1772562000.0
        rows = []
        for i in range(3):
            rows.append({"trace_id": f"trace-{i}",
                         "event_type": "handler_entry", "ts": ts + i})
            rows.append({"trace_id": f"trace-{i}",
                         "event_type": "stream_complete", "ts": ts + i + 0.5})
        self._seed_parity_trace(db, rows=rows)
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d9 = rep["dim9_parity_trace_coverage"]
        assert d9["traces_with_handler_entry"] == 3
        assert d9["traces_with_clean_wire_completion"] == 3
        assert d9["traces_with_wire_completion"] == 3
        assert d9["traces_without_terminal_event"] == 0
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
        assert "Q8 — wire-side completion" in md

    def test_dim9_v3_pre_dispatch_death_localization(self, tmp_path):
        """V3 — when traces die at different stages, the harness reports
        a per-stage breakdown that pinpoints where requests stop."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        # Three traces:
        #   trace-1: died at handler_entry only
        #   trace-2: died at body_read_complete (got past auth + route)
        #   trace-3: reached upstream_attempt_start (no death pre-dispatch)
        rows = []
        rows.append({"trace_id": "trace-1", "event_type": "handler_entry",
                     "ts": ts + 0})
        for evt in ("handler_entry", "auth_gate_pass", "route_resolved",
                    "body_read_complete"):
            rows.append({"trace_id": "trace-2", "event_type": evt,
                         "ts": ts + 10})
        for evt in ("handler_entry", "auth_gate_pass", "route_resolved",
                    "body_read_complete", "adapter_detected",
                    "before_dispatch", "upstream_attempt_start"):
            rows.append({"trace_id": "trace-3", "event_type": evt,
                         "ts": ts + 20})
        self._seed_parity_trace(db, rows=rows)
        rep = inspect.analyze(db_path=db, window_minutes=0)
        d9 = rep["dim9_parity_trace_coverage"]

        # last_stage_distribution should show trace-1 died at
        # handler_entry, trace-2 at body_read_complete, trace-3 at
        # upstream_attempt_start.
        last = d9["last_stage_distribution"]
        assert last.get("handler_entry") == 1
        assert last.get("body_read_complete") == 1
        assert last.get("upstream_attempt_start") == 1

        # Of the 3, exactly 2 died before upstream_attempt_start
        # (trace-1 at handler_entry, trace-2 at body_read_complete).
        assert d9["pre_upstream_death_count"] == 2
        pre = d9["pre_upstream_death_stage_distribution"]
        assert pre.get("handler_entry") == 1
        assert pre.get("body_read_complete") == 1

    def test_dim9_v3_synthesis_renders_q9(self, tmp_path):
        """When pre-dispatch deaths exist, Q9 summary line appears."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": f"trace-{i}", "event_type": "handler_entry",
             "ts": ts + i}
            for i in range(3)
        ])
        out = tmp_path / "report.md"
        rc = inspect.main([
            "--db-path", str(db),
            "--window-minutes", "0",
            "--output", str(out),
        ])
        assert rc == 0
        md = out.read_text()
        assert "Q9 — NCP-3I-v3 pre-dispatch death" in md
        assert "handler_entry=3" in md

    def test_dim9_ncp3a_terminal_early_returns_excluded_from_deaths(self, tmp_path):
        """NCP-3A — traces ending at request_rejected or
        dispatch_subprocess_complete are intentional terminations and
        must NOT be counted in pre_upstream_death_count.
        """
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        rows = []
        # trace-die-1: silent death at adapter_detected (no terminal event).
        for evt in ("handler_entry", "auth_gate_pass", "route_resolved",
                    "body_read_complete", "adapter_detected"):
            rows.append({"trace_id": "trace-die", "event_type": evt, "ts": ts})
        # trace-rej: ends at request_rejected (auth/circuit/validation).
        for evt in ("handler_entry", "auth_gate_pass", "route_resolved",
                    "body_read_complete", "adapter_detected",
                    "request_rejected"):
            rows.append({"trace_id": "trace-rej", "event_type": evt, "ts": ts + 10})
        # trace-sub: ends at dispatch_subprocess_complete (subprocess success).
        for evt in ("handler_entry", "auth_gate_pass", "route_resolved",
                    "body_read_complete", "adapter_detected",
                    "dispatch_subprocess_complete"):
            rows.append({"trace_id": "trace-sub", "event_type": evt, "ts": ts + 20})
        self._seed_parity_trace(db, rows=rows)

        rep = inspect.analyze(db_path=db, window_minutes=0)
        d9 = rep["dim9_parity_trace_coverage"]

        # last_stage_distribution still includes ALL terminals.
        last = d9["last_stage_distribution"]
        assert last.get("adapter_detected") == 1
        assert last.get("request_rejected") == 1
        assert last.get("dispatch_subprocess_complete") == 1

        # Only the silent-death trace counts as a pre-upstream death.
        assert d9["pre_upstream_death_count"] == 1
        pre = d9["pre_upstream_death_stage_distribution"]
        assert pre == {"adapter_detected": 1}

        # The other two are reported under early_return_*.
        assert d9["early_return_count"] == 2
        er = d9["early_return_stage_distribution"]
        assert er.get("request_rejected") == 1
        assert er.get("dispatch_subprocess_complete") == 1

    def test_dim9_ncp3a_synthesis_renders_q10(self, tmp_path):
        """When early-return terminals are present, Q10 summary line
        appears alongside Q9.
        """
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        rows = []
        for evt in ("handler_entry", "adapter_detected", "request_rejected"):
            rows.append({"trace_id": "trace-rej", "event_type": evt, "ts": ts})
        for evt in ("handler_entry", "adapter_detected",
                    "dispatch_subprocess_complete"):
            rows.append({"trace_id": "trace-sub", "event_type": evt, "ts": ts + 1})
        self._seed_parity_trace(db, rows=rows)

        out = tmp_path / "report.md"
        rc = inspect.main([
            "--db-path", str(db),
            "--window-minutes", "0",
            "--output", str(out),
        ])
        assert rc == 0
        md = out.read_text()
        assert "Q10 — NCP-3A-enrichment terminal early-returns" in md
        assert "request_rejected=1" in md
        assert "dispatch_subprocess_complete=1" in md

    # ── Issue #73 — wire-side completion canonicality ─────────────────

    def test_wire_completion_via_stream_complete(self, tmp_path):
        """handler_entry + stream_complete → counted as clean wire
        completion; not silent death; verdict interp_a_or_clean."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        rows = [
            {"trace_id": "t-clean", "event_type": "handler_entry", "ts": ts},
            {"trace_id": "t-clean", "event_type": "stream_complete",
             "ts": ts + 0.5},
        ]
        self._seed_parity_trace(db, rows=rows)
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["traces_with_clean_wire_completion"] == 1
        assert d9["traces_with_terminal_fast_fail"] == 0
        assert d9["traces_with_terminal_abort"] == 0
        assert d9["traces_with_wire_completion"] == 1
        assert d9["traces_without_terminal_event"] == 0
        assert d9["verdict"] == "interp_a_or_clean"

    def test_wire_completion_via_request_rejected(self, tmp_path):
        """handler_entry + request_rejected → terminal_fast_fail; not a
        silent death; verdict interp_a_or_clean."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        rows = [
            {"trace_id": "t-rej", "event_type": "handler_entry", "ts": ts},
            {"trace_id": "t-rej", "event_type": "request_rejected",
             "ts": ts + 0.5},
        ]
        self._seed_parity_trace(db, rows=rows)
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["traces_with_terminal_fast_fail"] == 1
        assert d9["traces_with_clean_wire_completion"] == 0
        assert d9["traces_with_terminal_abort"] == 0
        assert d9["traces_with_wire_completion"] == 1
        assert d9["traces_without_terminal_event"] == 0
        assert d9["verdict"] == "interp_a_or_clean"

    def test_wire_completion_via_stream_abort(self, tmp_path):
        """handler_entry + stream_abort → terminal_abort; not silent
        death; verdict interp_a_or_clean."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        rows = [
            {"trace_id": "t-ab", "event_type": "handler_entry", "ts": ts},
            {"trace_id": "t-ab", "event_type": "stream_abort",
             "ts": ts + 0.5},
        ]
        self._seed_parity_trace(db, rows=rows)
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["traces_with_terminal_abort"] == 1
        assert d9["traces_with_clean_wire_completion"] == 0
        assert d9["traces_with_terminal_fast_fail"] == 0
        assert d9["traces_with_wire_completion"] == 1
        assert d9["traces_without_terminal_event"] == 0
        assert d9["verdict"] == "interp_a_or_clean"

    def test_wire_completion_via_subprocess_complete(self, tmp_path):
        """handler_entry + dispatch_subprocess_complete → counted in
        traces_with_wire_completion (subprocess class); verdict clean."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        rows = [
            {"trace_id": "t-sub", "event_type": "handler_entry", "ts": ts},
            {"trace_id": "t-sub", "event_type": "dispatch_subprocess_complete",
             "ts": ts + 0.5},
        ]
        self._seed_parity_trace(db, rows=rows)
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        # Subprocess does not increment the clean/fast_fail/abort
        # buckets; it contributes only to the aggregate
        # traces_with_wire_completion + the lifecycle event
        # distribution.
        assert d9["traces_with_clean_wire_completion"] == 0
        assert d9["traces_with_terminal_fast_fail"] == 0
        assert d9["traces_with_terminal_abort"] == 0
        assert d9["traces_with_wire_completion"] == 1
        assert d9["traces_without_terminal_event"] == 0
        assert d9["verdict"] == "interp_a_or_clean"

    def test_traces_without_terminal_event_is_silent_death(self, tmp_path):
        """handler_entry without any of the four canonical terminals →
        traces_without_terminal_event; verdict interp_b_supported."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        # 2 silent deaths + 1 clean completion → verdict still
        # interp_b_supported because n_silent > 0.
        rows = [
            {"trace_id": "t-die-1", "event_type": "handler_entry", "ts": ts},
            {"trace_id": "t-die-2", "event_type": "handler_entry",
             "ts": ts + 1},
            {"trace_id": "t-ok", "event_type": "handler_entry", "ts": ts + 2},
            {"trace_id": "t-ok", "event_type": "stream_complete",
             "ts": ts + 2.5},
        ]
        self._seed_parity_trace(db, rows=rows)
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["traces_with_handler_entry"] == 3
        assert d9["traces_without_terminal_event"] == 2
        assert d9["traces_with_clean_wire_completion"] == 1
        assert d9["interp_b_count"] == 2
        assert d9["verdict"] == "interp_b_supported"

    def test_legacy_tp_events_field_kept_as_deprecated(self, tmp_path):
        """The old tp_events stitch is preserved as
        traces_with_completion_in_tp_events_deprecated for diagnostic
        context, but does NOT influence the Q8 verdict."""
        db = tmp_path / "telemetry.db"
        # Seed a tp_events row that DOES match — old logic would have
        # said "completion present, clean". We still need a parity-
        # trace stream_complete to drive the new verdict to clean.
        ts_iso = "2026-04-27T12:00:00"
        _seed(db, rows=[
            {"ts": ts_iso, "session_id": "s", "request_id": "t-x",
             "trace_id": "t-x", "duration_ms": 100},
        ])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-x", "event_type": "handler_entry", "ts": ts},
            {"trace_id": "t-x", "event_type": "stream_complete",
             "ts": ts + 0.5},
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert (
            d9["traces_with_completion_in_tp_events_deprecated"] == 1
        ), "deprecated field must remain available"
        assert d9["traces_with_clean_wire_completion"] == 1
        assert d9["verdict"] == "interp_a_or_clean"

    # ── Issue #74 phase 1 — stream_abort phase classification ─────────

    def _abort_row(self, trace_id, ts, notes):
        return {"trace_id": trace_id, "event_type": "stream_abort",
                "ts": ts, "notes": notes}

    def test_stream_abort_phase_before_headers_classified(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-bh", "event_type": "handler_entry", "ts": ts},
            self._abort_row("t-bh", ts + 0.5,
                            "abort_phase=before_headers"),
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["stream_abort_phase_distribution"] == {"before_headers": 1}
        assert d9["traces_with_terminal_abort"] == 1

    def test_stream_abort_phase_after_headers_before_first_byte_classified(
        self, tmp_path
    ):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-ahbf", "event_type": "handler_entry", "ts": ts},
            self._abort_row("t-ahbf", ts + 0.5,
                            "abort_phase=after_headers_before_first_byte"),
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["stream_abort_phase_distribution"] == {
            "after_headers_before_first_byte": 1
        }

    def test_stream_abort_phase_mid_stream_classified(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-ms", "event_type": "handler_entry", "ts": ts},
            self._abort_row("t-ms", ts + 0.5, "abort_phase=mid_stream"),
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["stream_abort_phase_distribution"] == {"mid_stream": 1}

    def test_stream_abort_phase_client_disconnect_classified(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-cd", "event_type": "handler_entry", "ts": ts},
            self._abort_row("t-cd", ts + 0.5,
                            "abort_phase=client_disconnect"),
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["stream_abort_phase_distribution"] == {
            "client_disconnect": 1
        }

    def test_stream_abort_phase_upstream_protocol_error_classified(
        self, tmp_path
    ):
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-upe", "event_type": "handler_entry", "ts": ts},
            self._abort_row("t-upe", ts + 0.5,
                            "abort_phase=upstream_protocol_error"),
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["stream_abort_phase_distribution"] == {
            "upstream_protocol_error": 1
        }

    def test_stream_abort_legacy_notes_classified_unknown(self, tmp_path):
        """Legacy stream_abort rows pre-#74 phase 1 (no abort_phase=
        prefix in notes, or null notes) classify as unknown."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-legacy", "event_type": "handler_entry",
             "ts": ts},
            # notes=None (legacy rows have no notes field set)
            self._abort_row("t-legacy", ts + 0.5, None),
            {"trace_id": "t-other", "event_type": "handler_entry",
             "ts": ts + 1},
            # notes set but no abort_phase= prefix
            self._abort_row("t-other", ts + 1.5,
                            "circuit_breaker_open:anthropic"),
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["stream_abort_phase_distribution"] == {"unknown": 2}

    def test_stream_abort_phase_distribution_aggregates_multiple(
        self, tmp_path
    ):
        """Multi-class distribution sums correctly."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        rows = [
            {"trace_id": f"t-{i}", "event_type": "handler_entry",
             "ts": ts + i}
            for i in range(5)
        ]
        rows.append(self._abort_row("t-0", ts + 0.5,
                                    "abort_phase=upstream_protocol_error"))
        rows.append(self._abort_row("t-1", ts + 1.5,
                                    "abort_phase=upstream_protocol_error"))
        rows.append(self._abort_row("t-2", ts + 2.5,
                                    "abort_phase=upstream_protocol_error"))
        rows.append(self._abort_row("t-3", ts + 3.5,
                                    "abort_phase=client_disconnect"))
        rows.append(self._abort_row("t-4", ts + 4.5, None))  # legacy
        self._seed_parity_trace(db, rows=rows)
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["stream_abort_phase_distribution"] == {
            "upstream_protocol_error": 3,
            "client_disconnect": 1,
            "unknown": 1,
        }

    def test_q11_synthesis_renders_phase_breakdown(self, tmp_path):
        """Markdown synthesis must include the Q11 line with the phase
        distribution when stream_abort events are present."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": "t-x", "event_type": "handler_entry", "ts": ts},
            self._abort_row("t-x", ts + 0.5,
                            "abort_phase=upstream_protocol_error"),
        ])
        out = tmp_path / "report.md"
        rc = inspect.main([
            "--db-path", str(db),
            "--window-minutes", "0",
            "--output", str(out),
        ])
        assert rc == 0
        md = out.read_text()
        assert "Q11 — NCP-3A-streaming-connect stream_abort phase" in md
        assert "upstream_protocol_error=1" in md

    def test_q8_verdict_unaffected_when_tp_events_empty(self, tmp_path):
        """Issue #73 invariant: handler_entry + stream_complete with
        ZERO tp_events rows still yields interp_a_or_clean. The old
        logic would have fired interp_b_supported here (the bug)."""
        db = tmp_path / "telemetry.db"
        _seed(db, rows=[])  # zero tp_events rows
        ts = 1772562000.0
        self._seed_parity_trace(db, rows=[
            {"trace_id": f"t-{i}", "event_type": "handler_entry",
             "ts": ts + i}
            for i in range(5)
        ] + [
            {"trace_id": f"t-{i}", "event_type": "stream_complete",
             "ts": ts + i + 0.5}
            for i in range(5)
        ])
        d9 = inspect.analyze(
            db_path=db, window_minutes=0
        )["dim9_parity_trace_coverage"]
        assert d9["traces_with_handler_entry"] == 5
        assert d9["traces_with_clean_wire_completion"] == 5
        assert d9["traces_with_completion_in_tp_events_deprecated"] == 0
        # The deprecated count is 0/5 — old logic would have fired
        # interp_b_supported. New logic: stream_complete present →
        # clean.
        assert d9["verdict"] == "interp_a_or_clean"


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
