# SPDX-License-Identifier: Apache-2.0
"""NCP-1 — capture + diff parity baseline scripts.

Coverage:

  1. capture script — writes a tokenpak baseline from a fixture telemetry.db
  2. capture script — writes an empty native template
  3. diff script — H1 supported (cache hit ratio gap)
  4. diff script — H1 not supported (parity)
  5. diff script — H2 supported (rotation collapse)
  6. diff script — H2 not supported (parity)
  7. diff script — both H1 + H2 supported
  8. diff script — inconclusive on missing data
  9. JSON + markdown render parity
  10. CLI entry point smokes
  11. capture script: missing telemetry.db handled cleanly
  12. structural test — no runtime behavior changes (scripts have
      no imports from proxy / companion business logic that could
      mutate state)
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# Load the scripts as modules so we can call their main() directly.
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(name: str):
    path = SCRIPT_DIR / name
    spec = importlib.util.spec_from_file_location(
        f"_ncp1_test_{path.stem}", path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


capture = _load_script("capture_parity_baseline.py")
diff = _load_script("diff_parity_baselines.py")


# ── Fixtures ──────────────────────────────────────────────────────────


def _seed_telemetry(
    db: Path,
    *,
    n_requests: int = 10,
    cache_read_per_req: int = 100,
    cache_write_per_req: int = 50,
    distinct_sessions: int = 1,
    status_codes: tuple = (200,),
    provider: str = "tokenpak-claude-code",
    timestamp_iso: str = "2026-04-26T12:00:00",
) -> None:
    """Seed a tp_events + tp_usage pair under ``db``."""
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
    for i in range(n_requests):
        trace_id = f"trace-{i}"
        session_id = f"session-{i % max(1, distinct_sessions)}"
        status = str(status_codes[i % len(status_codes)])
        conn.execute(
            "INSERT INTO tp_events (request_id, trace_id, ts, provider, "
            "model, agent_id, api, stop_reason, session_id, duration_ms, "
            "status, error_class, route) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"req-{i}",
                trace_id,
                timestamp_iso,
                provider,
                "claude-3-5-sonnet",
                "agent-x",
                "messages",
                "end_turn",
                session_id,
                500,
                status,
                None,
                "claude-code",
            ),
        )
        conn.execute(
            "INSERT INTO tp_usage (trace_id, usage_source, confidence, "
            "input_billed, output_billed, input_est, output_est, "
            "cache_read, cache_write, total_tokens, total_tokens_billed, "
            "total_tokens_est, provider_usage_raw) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trace_id,
                "billed",
                1.0,
                1000,
                500,
                1000,
                500,
                cache_read_per_req,
                cache_write_per_req,
                1500,
                1500,
                1500,
                "{}",
            ),
        )
    conn.commit()
    conn.close()


# ── 1. capture script — tokenpak baseline ─────────────────────────────


class TestCaptureTokenpakBaseline:

    def test_writes_baseline_with_metrics(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed_telemetry(db, n_requests=10, distinct_sessions=2)
        out = tmp_path / "tokenpak.json"
        rc = capture.main([
            "--label", "tokenpak",
            "--db-path", str(db),
            "--window-days", "0",  # all rows
            "--output", str(out),
        ])
        assert rc == 0
        d = json.loads(out.read_text())
        assert d["schema_version"] == capture.SCHEMA_VERSION
        assert d["label"] == "tokenpak"
        assert d["metrics"]["request_count"] == 10
        assert d["metrics"]["cache_read_tokens"] == 1000
        assert d["metrics"]["cache_creation_tokens"] == 500
        # 1000 / (1000 + 500) == 0.6667
        assert abs(d["metrics"]["cache_hit_ratio"] - 0.6667) < 0.001
        assert d["session"]["distinct_session_id_count"] == 2

    def test_unavailable_metrics_have_reasons(self, tmp_path):
        db = tmp_path / "telemetry.db"
        _seed_telemetry(db)
        out = tmp_path / "tokenpak.json"
        capture.main([
            "--label", "tokenpak",
            "--db-path", str(db),
            "--window-days", "0",
            "--output", str(out),
        ])
        d = json.loads(out.read_text())
        # Every unavailable metric must have a reason string.
        for k in capture.UNAVAILABLE_REASONS:
            assert k in d["_unavailable"]
            assert d["_unavailable"][k], (
                f"unavailable metric {k!r} must have a non-empty reason"
            )


# ── 2. capture script — native template ───────────────────────────────


class TestCaptureNativeTemplate:

    def test_native_emits_empty_template(self, tmp_path):
        out = tmp_path / "native.json"
        rc = capture.main([
            "--label", "native",
            "--window-days", "1",
            "--output", str(out),
        ])
        assert rc == 0
        d = json.loads(out.read_text())
        assert d["label"] == "native"
        for k in capture.METRIC_KEYS:
            assert d["metrics"][k] is None
        # The note + _unavailable both flag the operator should
        # fill it in.
        assert "operator" in d["note"].lower() or "fill" in d["note"].lower()

    def test_native_does_not_read_db(self, tmp_path):
        # Native template should work even when telemetry.db is
        # absent (since native traffic doesn't go through TokenPak).
        out = tmp_path / "native.json"
        rc = capture.main([
            "--label", "native",
            "--db-path", str(tmp_path / "missing.db"),
            "--output", str(out),
        ])
        assert rc == 0


# ── 3. diff script — H1 supported ─────────────────────────────────────


class TestDiffH1Supported:

    def _build(self, *, native_hr: float, tokenpak_hr: float) -> tuple:
        native = {
            "schema_version": capture.SCHEMA_VERSION,
            "label": "native",
            "metrics": {
                "cache_hit_ratio": native_hr,
                "cache_read_tokens": 9000,
                "cache_creation_tokens": 1000,
            },
            "session": {
                "session_id_rotations_per_hour": 2.0,
                "distinct_session_id_count": 5,
            },
        }
        tokenpak = {
            "schema_version": capture.SCHEMA_VERSION,
            "label": "tokenpak",
            "metrics": {
                "cache_hit_ratio": tokenpak_hr,
                "cache_read_tokens": 100,
                "cache_creation_tokens": 9900,
            },
            "session": {
                "session_id_rotations_per_hour": 2.0,
                "distinct_session_id_count": 5,
            },
        }
        return native, tokenpak

    def test_supported_when_delta_above_threshold(self):
        native, tokenpak = self._build(native_hr=0.9, tokenpak_hr=0.05)
        h1 = diff._evaluate_h1(native, tokenpak)
        assert h1["verdict"] == "supported"
        assert h1["delta"] >= diff.H1_CACHE_HIT_DELTA_THRESHOLD

    def test_not_supported_when_delta_below_threshold(self):
        native, tokenpak = self._build(native_hr=0.9, tokenpak_hr=0.85)
        h1 = diff._evaluate_h1(native, tokenpak)
        assert h1["verdict"] == "not_supported"

    def test_inconclusive_when_native_missing(self):
        native, tokenpak = self._build(native_hr=0.0, tokenpak_hr=0.05)
        native["metrics"]["cache_hit_ratio"] = None
        h1 = diff._evaluate_h1(native, tokenpak)
        assert h1["verdict"] == "inconclusive"


# ── 4. diff script — H2 verdicts ──────────────────────────────────────


class TestDiffH2:

    def _build(self, *, nat_rot, tok_rot, nat_count=10, tok_count=1):
        return (
            {
                "metrics": {},
                "session": {
                    "session_id_rotations_per_hour": nat_rot,
                    "distinct_session_id_count": nat_count,
                },
            },
            {
                "metrics": {},
                "session": {
                    "session_id_rotations_per_hour": tok_rot,
                    "distinct_session_id_count": tok_count,
                },
            },
        )

    def test_supported_when_native_rotates_much_faster(self):
        native, tokenpak = self._build(nat_rot=10.0, tok_rot=1.0)
        h2 = diff._evaluate_h2(native, tokenpak)
        assert h2["verdict"] == "supported"

    def test_supported_when_tokenpak_rotation_zero(self):
        native, tokenpak = self._build(nat_rot=2.0, tok_rot=0.0)
        h2 = diff._evaluate_h2(native, tokenpak)
        assert h2["verdict"] == "supported"
        assert h2["ratio"] == float("inf")

    def test_not_supported_when_rotation_parity(self):
        native, tokenpak = self._build(nat_rot=2.0, tok_rot=1.5)
        h2 = diff._evaluate_h2(native, tokenpak)
        assert h2["verdict"] == "not_supported"

    def test_falls_back_to_distinct_count(self):
        native, tokenpak = self._build(
            nat_rot=None, tok_rot=None, nat_count=20, tok_count=1
        )
        h2 = diff._evaluate_h2(native, tokenpak)
        assert h2["verdict"] == "supported"
        assert h2["count_ratio"] == 20.0

    def test_inconclusive_when_data_missing(self):
        native, tokenpak = self._build(
            nat_rot=None, tok_rot=None, nat_count=None, tok_count=None
        )
        h2 = diff._evaluate_h2(native, tokenpak)
        assert h2["verdict"] == "inconclusive"


# ── 5. dominant cause synthesis ───────────────────────────────────────


class TestDominantCauseSynthesis:

    def test_both_supported(self):
        h1 = {"verdict": "supported"}
        h2 = {"verdict": "supported"}
        syn = diff._dominant_cause(h1, h2)
        assert "H1+H2" in syn["dominant_cause"]
        assert syn["confidence"] == "high"

    def test_only_h1_supported(self):
        h1 = {"verdict": "supported"}
        h2 = {"verdict": "not_supported"}
        syn = diff._dominant_cause(h1, h2)
        assert "H1" in syn["dominant_cause"]
        assert "cache" in syn["rationale"].lower()

    def test_only_h2_supported(self):
        h1 = {"verdict": "not_supported"}
        h2 = {"verdict": "supported"}
        syn = diff._dominant_cause(h1, h2)
        assert "H2" in syn["dominant_cause"]
        assert "session" in syn["rationale"].lower()

    def test_neither_supported(self):
        h1 = {"verdict": "not_supported"}
        h2 = {"verdict": "not_supported"}
        syn = diff._dominant_cause(h1, h2)
        assert "neither" in syn["dominant_cause"].lower()
        assert "h3" in syn["rationale"].lower()

    def test_inconclusive(self):
        h1 = {"verdict": "inconclusive"}
        h2 = {"verdict": "supported"}
        syn = diff._dominant_cause(h1, h2)
        assert syn["dominant_cause"] == "inconclusive"


# ── 6. fix recommendations ────────────────────────────────────────────


class TestFixRecommendations:

    def test_recommends_ncp2_when_h1(self):
        recs = diff._recommend_fix(
            {"verdict": "supported"}, {"verdict": "not_supported"}
        )
        assert any("NCP-2" in r for r in recs)
        assert any("cache" in r.lower() for r in recs)

    def test_recommends_ncp3_when_h2(self):
        recs = diff._recommend_fix(
            {"verdict": "not_supported"}, {"verdict": "supported"}
        )
        assert any("NCP-3" in r for r in recs)
        assert any("session" in r.lower() for r in recs)

    def test_no_fix_when_neither(self):
        recs = diff._recommend_fix(
            {"verdict": "not_supported"}, {"verdict": "not_supported"}
        )
        assert any("H3" in r or "H4" in r for r in recs)


# ── 7. JSON + markdown render parity ──────────────────────────────────


class TestRenderParity:

    def test_diff_main_json_emits_valid_json(self, tmp_path):
        nat = tmp_path / "native.json"
        tok = tmp_path / "tokenpak.json"
        nat.write_text(json.dumps({
            "metrics": {"cache_hit_ratio": 0.9, "cache_read_tokens": 9, "cache_creation_tokens": 1},
            "session": {"session_id_rotations_per_hour": 5.0, "distinct_session_id_count": 5},
        }))
        tok.write_text(json.dumps({
            "metrics": {"cache_hit_ratio": 0.05, "cache_read_tokens": 1, "cache_creation_tokens": 19},
            "session": {"session_id_rotations_per_hour": 0.0, "distinct_session_id_count": 1},
        }))
        out = tmp_path / "report.json"
        rc = diff.main([
            "--native", str(nat),
            "--tokenpak", str(tok),
            "--json",
            "--output", str(out),
        ])
        assert rc == 0
        d = json.loads(out.read_text())
        assert d["h1"]["verdict"] == "supported"
        assert d["h2"]["verdict"] == "supported"
        assert "dominant_cause" in d["synthesis"]

    def test_diff_main_markdown_default(self, tmp_path):
        nat = tmp_path / "native.json"
        tok = tmp_path / "tokenpak.json"
        nat.write_text(json.dumps({
            "metrics": {"cache_hit_ratio": 0.9, "cache_read_tokens": 9, "cache_creation_tokens": 1},
            "session": {"session_id_rotations_per_hour": 5.0, "distinct_session_id_count": 5},
        }))
        tok.write_text(json.dumps({
            "metrics": {"cache_hit_ratio": 0.05, "cache_read_tokens": 1, "cache_creation_tokens": 19},
            "session": {"session_id_rotations_per_hour": 0.0, "distinct_session_id_count": 1},
        }))
        out = tmp_path / "report.md"
        rc = diff.main([
            "--native", str(nat),
            "--tokenpak", str(tok),
            "--output", str(out),
        ])
        assert rc == 0
        md = out.read_text()
        assert "# NCP-1 parity A/B results" in md
        assert "**H1" in md
        assert "**H2" in md
        assert "Dominant cause" in md


# ── 8. CLI entrypoints (subprocess smokes) ────────────────────────────


class TestCliSmoke:

    def test_capture_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "capture_parity_baseline.py"), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--label" in result.stdout

    def test_diff_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "diff_parity_baselines.py"), "--help"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--native" in result.stdout
        assert "--tokenpak" in result.stdout


# ── 9. capture: missing telemetry.db handled cleanly ──────────────────


class TestCaptureMissingDB:

    def test_missing_db_returns_2(self, tmp_path, capsys):
        out = tmp_path / "tokenpak.json"
        rc = capture.main([
            "--label", "tokenpak",
            "--db-path", str(tmp_path / "missing.db"),
            "--output", str(out),
        ])
        assert rc == 2
        captured = capsys.readouterr()
        assert "telemetry.db not found" in captured.err


# ── 10. structural — no runtime mutation imports ──────────────────────


class TestNoRuntimeBehaviorChanges:

    def test_capture_does_not_import_proxy_dispatch(self):
        text = (SCRIPT_DIR / "capture_parity_baseline.py").read_text()
        for forbidden in (
            "from tokenpak.proxy.client import",
            "from tokenpak.proxy.server import",
            "from tokenpak.proxy.connection_pool import",
            "forward_headers",
            "pool.request",
            "pool.stream",
            "credential_injector",
        ):
            assert forbidden not in text, (
                f"NCP-1 capture script must not import dispatch primitive: {forbidden}"
            )

    def test_diff_does_not_import_proxy_dispatch(self):
        text = (SCRIPT_DIR / "diff_parity_baselines.py").read_text()
        for forbidden in (
            "from tokenpak.proxy.client import",
            "from tokenpak.proxy.server import",
            "from tokenpak.companion",
            "forward_headers",
            "credential_injector",
        ):
            assert forbidden not in text, (
                f"NCP-1 diff script must not import dispatch primitive: {forbidden}"
            )

    def test_capture_does_not_write_to_telemetry_tables(self):
        # SQL-keyword scan: capture must read, never write.
        text = (SCRIPT_DIR / "capture_parity_baseline.py").read_text()
        for forbidden in (
            "INSERT INTO tp_",
            "UPDATE tp_",
            "DELETE FROM tp_",
            "DROP TABLE",
            "ALTER TABLE",
        ):
            assert forbidden not in text, (
                f"NCP-1 capture script must not modify telemetry: {forbidden}"
            )
