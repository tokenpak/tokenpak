# SPDX-License-Identifier: Apache-2.0
"""Walk-forward replay + end-to-end engine integration for the calibrated band.

The replay here is the acceptance instrument: held-out (past-fits-future)
coverage and tails by turn-depth bucket over a synthetic chronology whose
truth the model never sees, plus the full engine path producing a valid,
deterministic contract payload once history is warm — and an honest
``learning`` payload when it is not.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from tests.proxy.test_session_forecast_calibration import (
    NOW,
    _corpus,
    _seed_history_db,
)
from tokenpak.core.contracts.session_economics import ForecastStatus, SessionEconomics
from tokenpak.proxy import session_forecast_calibration as cal
from tokenpak.proxy.session_forecast import _build_session_economics


def test_walk_forward_reports_coverage_and_tails_by_horizon():
    cell = _corpus(140, seed=23)
    report: dict[str, dict[str, float | int]] = {}
    buckets = {"turns 1-3": (1, 3), "turns 4-7": (4, 7), "turns 8+": (8, cal.KMAX)}
    for label, (klo, khi) in buckets.items():
        inside = 0
        scored = 0
        widths: list[float] = []
        for i in range(10, len(cell), cal.WF_BLOCK):
            history = cell[:i]
            for s in cell[i : i + cal.WF_BLOCK]:
                spent = 0.0
                for k, cost in enumerate(s.turn_costs, start=1):
                    spent += cost
                    if k >= s.turns or not (klo <= k <= khi) or spent <= 0:
                        continue
                    band = cal._band_for(history, [], k, cal.TARGET_50)
                    if band is None:
                        continue
                    lo_t = spent * math.exp(band.lo_y)
                    hi_t = spent * math.exp(band.hi_y)
                    scored += 1
                    widths.append(hi_t / max(lo_t, 1.0))
                    if lo_t <= s.total <= hi_t:
                        inside += 1
        assert scored > 30, f"{label}: too few scored points ({scored})"
        coverage = 100.0 * inside / scored
        report[label] = {
            "coverage": round(coverage, 1),
            "scored": scored,
            "median_width": round(sorted(widths)[len(widths) // 2], 2),
        }
        # Honesty window, not a nominal relabel: broad but bounded.
        assert 25.0 <= coverage <= 90.0, (label, coverage)
    # Tails must narrow with depth (research invariant: width shrinks as
    # spent approaches total).
    assert report["turns 8+"]["median_width"] <= report["turns 1-3"]["median_width"]


def test_measured_coverage_is_never_relabeled_nominal():
    cell = _corpus(120, seed=31)
    measured, scored = cal.walk_forward_coverage(cell, [], cal.TARGET_50)
    assert scored > 0
    readiness = cal.cell_readiness(cell, [])
    assert readiness.observed_coverage_50 == measured  # reported, not replaced


# ---------------------------------------------------------------------------
# End-to-end engine path on a real (seeded) ledger
# ---------------------------------------------------------------------------


def _seed_full_ledger(tmp_path, *, warm_sessions):
    """Product-initialized monitor.db + synthetic history + one active session."""
    from tokenpak.proxy.monitor import Monitor

    db = tmp_path / "monitor.db"
    Monitor(str(db))
    import sqlite3

    conn = sqlite3.connect(str(db))
    rng = random.Random(5)
    for si, s in enumerate(warm_sessions):
        start = s.ended_at - timedelta(minutes=s.turns)
        for ti, cost in enumerate(s.turn_costs):
            conn.execute(
                "INSERT INTO requests (timestamp, model, request_type, input_tokens,"
                " output_tokens, estimated_cost, latency_ms, status_code, endpoint,"
                " session_id, reasoning_effort, cache_read_tokens, cache_creation_tokens)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0)",
                (
                    (start + timedelta(minutes=ti)).isoformat(),
                    s.model,
                    "messages",
                    int(cost * 0.8),
                    int(cost * 0.2),
                    0.01,
                    900,
                    200,
                    "/v1/messages",
                    f"hist-{si}",
                    s.effort,
                ),
            )
    active_start = NOW - timedelta(minutes=30)
    for ti in range(5):
        cost = max(500, int(rng.lognormvariate(math.log(4000.0), 0.6)))
        conn.execute(
            "INSERT INTO requests (timestamp, model, request_type, input_tokens,"
            " output_tokens, estimated_cost, latency_ms, status_code, endpoint,"
            " session_id, reasoning_effort, cache_read_tokens, cache_creation_tokens)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0)",
            (
                (active_start + timedelta(minutes=ti * 3)).isoformat(),
                "model-a",
                "messages",
                int(cost * 0.8),
                int(cost * 0.2),
                0.01,
                900,
                200,
                "/v1/messages",
                "active-session",
                "unknown",
            ),
        )
    conn.commit()
    conn.close()
    return db


def test_engine_end_to_end_warm_history_is_available_and_deterministic(tmp_path):
    db = _seed_full_ledger(tmp_path, warm_sessions=_corpus(80, seed=13))
    econ = _build_session_economics("active-session", monitor_db_path=str(db), now=NOW)
    assert econ.forecast.status is ForecastStatus.AVAILABLE, econ.forecast.reason
    assert econ.forecast.coverage.history_n >= cal.MIN_CELL_SESSIONS
    # Contract round-trip and byte-stable determinism across evaluations.
    rebuilt = SessionEconomics.from_dict(econ.to_dict())
    assert rebuilt.to_json() == econ.to_json()
    again = _build_session_economics("active-session", monitor_db_path=str(db), now=NOW)
    assert again.to_json() == econ.to_json()


def test_engine_end_to_end_cold_history_learns(tmp_path):
    db = _seed_full_ledger(tmp_path, warm_sessions=_corpus(4, seed=17))
    econ = _build_session_economics("active-session", monitor_db_path=str(db), now=NOW)
    assert econ.forecast.status is ForecastStatus.LEARNING
    assert cal.PRIOR_VERSION in econ.forecast.reason
    # Learning payloads still validate and stay deterministic.
    assert SessionEconomics.from_dict(econ.to_dict()).to_json() == econ.to_json()


def test_engine_survives_calibration_failure(tmp_path, monkeypatch):
    db = _seed_full_ledger(tmp_path, warm_sessions=_corpus(30, seed=19))

    def _boom(**_kw):
        raise RuntimeError("synthetic calibration failure")

    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast_calibration.build_calibrated_forecast", _boom
    )
    econ = _build_session_economics("active-session", monitor_db_path=str(db), now=NOW)
    assert econ.forecast.status is ForecastStatus.LEARNING
    assert "unavailable this evaluation" in econ.forecast.reason
    # Deterministic facts and runway survive the forecasting failure.
    assert econ.session.id == "active-session"


_ = (datetime, timezone, _seed_history_db)  # re-exported fixture helpers stay importable
