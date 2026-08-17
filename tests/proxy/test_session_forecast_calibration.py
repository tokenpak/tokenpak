# SPDX-License-Identifier: Apache-2.0
"""Calibration unit tests: honest bands, pooling, drift, price gating, guard risk.

The synthetic corpus has a KNOWN generating process, so every honesty claim
is checked against ground truth the production code never sees: measured
coverage must reflect reality (not the nominal label), cold cells must say
``learning``, stale rates must blank USD while token ranges survive, and the
block probability must follow the deterministic runway without ever
overriding a hard stop.
"""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tokenpak.core.contracts.session_economics import (
    BindingConstraint,
    DriftState,
    ForecastStatus,
    GuardState,
    Runway,
    RunwayStatus,
    ValueState,
)
from tokenpak.proxy import session_forecast_calibration as cal

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
_HISTORY_END = NOW - timedelta(days=2)


def _mk_session(
    rng: random.Random,
    *,
    scale: float = 1.0,
    growth: float = 1.0,
    model: str = "model-a",
    effort: str = "unknown",
    ended_at: datetime,
) -> cal.HistorySession:
    turns = rng.randint(6, 16)
    costs = tuple(
        max(200.0, rng.lognormvariate(math.log(4000.0 * scale), 0.6)) * (growth**i)
        for i in range(turns)
    )
    return cal.HistorySession(model=model, effort=effort, ended_at=ended_at, turn_costs=costs)


def _corpus(
    n: int, *, seed: int = 7, scale: float = 1.0, growth: float = 1.0, model: str = "model-a"
) -> list:
    rng = random.Random(seed)
    return [
        _mk_session(
            rng,
            scale=scale,
            growth=growth,
            model=model,
            ended_at=_HISTORY_END - timedelta(hours=n - i),
        )
        for i in range(n)
    ]


def _runway(
    turns: int | None = 14,
    status: RunwayStatus = RunwayStatus.AVAILABLE,
    guard: GuardState = GuardState.ALLOW,
) -> Runway:
    binding = (
        BindingConstraint.CONTEXT_SOFT
        if status is RunwayStatus.AVAILABLE
        else BindingConstraint.UNKNOWN
    )
    return Runway(
        status=status,
        turns=turns if status is RunwayStatus.AVAILABLE else None,
        binding_constraint=binding,
        guard_state=guard,
    )


def _forecast(cell, *, rate=None, runway=None, spent=25000.0, k=4, burn=6000.0, monkeypatch=None):
    assert monkeypatch is not None
    monkeypatch.setattr(cal, "read_history", lambda *a, **kw: list(cell))
    return cal.build_calibrated_forecast(
        monitor_db_path="ignored",
        now=NOW,
        session_id="active",
        model="model-a",
        effort="unknown",
        turn_index=k,
        spent_tokens=spent,
        runway=runway or _runway(),
        burn_tokens_per_turn=burn,
        session_blended_usd_rate=rate,
    )


# ---------------------------------------------------------------------------
# History reader
# ---------------------------------------------------------------------------


def _seed_history_db(path, sessions, *, active_rows=0):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY, session_id TEXT, model TEXT,"
        " reasoning_effort TEXT, timestamp TEXT, status_code INTEGER,"
        " input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,"
        " cache_creation_tokens INTEGER, provider_input_tokens INTEGER,"
        " provider_output_tokens INTEGER, provider_cache_read_tokens INTEGER,"
        " provider_cache_creation_tokens INTEGER)"
    )
    for si, s in enumerate(sessions):
        start = s.ended_at - timedelta(minutes=len(s.turn_costs))
        for ti, cost in enumerate(s.turn_costs):
            conn.execute(
                "INSERT INTO requests (session_id, model, reasoning_effort, timestamp,"
                " status_code, input_tokens, output_tokens, cache_read_tokens,"
                " cache_creation_tokens) VALUES (?,?,?,?,200,?,?,0,0)",
                (
                    f"hist-{si}",
                    s.model,
                    s.effort,
                    (start + timedelta(minutes=ti)).isoformat(),
                    int(cost * 0.8),
                    int(cost * 0.2),
                ),
            )
    for ti in range(active_rows):
        conn.execute(
            "INSERT INTO requests (session_id, model, reasoning_effort, timestamp,"
            " status_code, input_tokens, output_tokens, cache_read_tokens,"
            " cache_creation_tokens) VALUES ('active','model-a','unknown',?,200,4000,1000,0,0)",
            ((NOW - timedelta(minutes=active_rows - ti)).isoformat(),),
        )
    conn.commit()
    conn.close()


def test_read_history_groups_and_gates(tmp_path):
    db = tmp_path / "monitor.db"
    sessions = _corpus(6)
    # one too-short session and one still-active session must be excluded
    short = cal.HistorySession("model-a", "unknown", _HISTORY_END, (500.0, 600.0))
    recent = cal.HistorySession("model-a", "unknown", NOW - timedelta(minutes=30), (500.0,) * 6)
    _seed_history_db(db, sessions + [short, recent], active_rows=3)
    out = cal.read_history(str(db), now=NOW, exclude_session="active")
    ids = {(s.model, s.turns) for s in out}
    assert len(out) == 6, ids
    assert all(s.turns >= cal.MIN_TURNS for s in out)
    assert all(s.ended_at <= NOW - timedelta(seconds=cal.COMPLETION_IDLE_SECONDS) for s in out)


def test_read_history_missing_db_is_empty():
    assert cal.read_history(None, now=NOW) == []
    assert cal.read_history("/nonexistent/monitor.db", now=NOW) == []


# ---------------------------------------------------------------------------
# Cold / warm gating
# ---------------------------------------------------------------------------


def test_cold_cell_learns_and_names_the_prior(monkeypatch):
    forecast = _forecast(_corpus(5), monkeypatch=monkeypatch)
    assert forecast.status is ForecastStatus.LEARNING
    assert cal.PRIOR_VERSION in forecast.reason
    assert forecast.remaining_tokens_likely_50.state is ValueState.UNAVAILABLE


def test_warm_cell_is_available_and_contract_ordered(monkeypatch):
    forecast = _forecast(_corpus(80), monkeypatch=monkeypatch)
    assert forecast.status is ForecastStatus.AVAILABLE
    iv = forecast.remaining_tokens_likely_50
    ceil = forecast.remaining_tokens_ceiling_90
    assert iv.state is ValueState.ESTIMATED and ceil.state is ValueState.ESTIMATED
    assert 0 <= iv.low <= iv.high <= ceil.value
    assert forecast.expected_turns.state is ValueState.ESTIMATED
    assert forecast.coverage.method
    assert forecast.coverage.history_n == 80
    assert forecast.coverage.observed is not None


def test_no_spend_is_unavailable(monkeypatch):
    forecast = _forecast(_corpus(80), spent=0.0, monkeypatch=monkeypatch)
    assert forecast.status is ForecastStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Coverage honesty
# ---------------------------------------------------------------------------


def test_observed_coverage_is_the_measured_number(monkeypatch):
    cell = _corpus(80)
    forecast = _forecast(cell, monkeypatch=monkeypatch)
    measured, scored = cal.walk_forward_coverage(cell, [], cal.TARGET_50)
    assert scored >= cal.MIN_SCORED_POINTS
    assert forecast.coverage.observed == round(min(measured, 100.0) / 100.0, 4)


def test_measured_coverage_is_plausible_for_stationary_corpus():
    cell = _corpus(120, seed=11)
    measured, scored = cal.walk_forward_coverage(cell, [], cal.TARGET_50)
    assert scored > 100
    # A conformal 50% central band on a stationary corpus must sit in a
    # broad honesty window around its label — never wildly off, never pinned
    # to 100 by construction.
    assert 30.0 <= measured <= 85.0, measured


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def test_distribution_shift_flags_drifting():
    stable = _corpus(70, seed=3)
    # Shape shift, not scale shift: the log-remaining-multiplier target is
    # scale-invariant, so a pure cost rescale is NOT drift. Steep per-turn
    # growth changes the multiplier distribution at every depth.
    shifted = _corpus(60, seed=4, growth=1.8)
    for i, s in enumerate(shifted):
        shifted[i] = cal.HistorySession(
            s.model,
            s.effort,
            _HISTORY_END - timedelta(minutes=60 - i),
            s.turn_costs,
        )
    readiness = cal.cell_readiness(stable + shifted, [])
    assert readiness.drift_state is DriftState.DRIFTING


def test_stationary_corpus_is_stable():
    readiness = cal.cell_readiness(_corpus(120, seed=11), [])
    assert readiness.drift_state is DriftState.STABLE
    assert readiness.sessions == 120


# ---------------------------------------------------------------------------
# Price gating
# ---------------------------------------------------------------------------


def test_stale_rate_blanks_usd_but_keeps_tokens(monkeypatch):
    forecast = _forecast(_corpus(80), rate=None, monkeypatch=monkeypatch)
    assert forecast.status is ForecastStatus.AVAILABLE
    assert forecast.remaining_tokens_likely_50.state is ValueState.ESTIMATED
    assert forecast.remaining_cost_usd_likely_50.state is ValueState.UNAVAILABLE
    assert forecast.remaining_cost_usd_ceiling_90.state is ValueState.UNAVAILABLE
    assert "stale or unknown" in forecast.remaining_cost_usd_likely_50.reason


def test_fresh_rate_scales_token_band(monkeypatch):
    rate = 1e-5
    forecast = _forecast(_corpus(80), rate=rate, monkeypatch=monkeypatch)
    iv, usd = forecast.remaining_tokens_likely_50, forecast.remaining_cost_usd_likely_50
    assert usd.state is ValueState.ESTIMATED
    assert usd.low == pytest.approx(iv.low * rate, rel=1e-3, abs=1e-3)
    assert usd.high == pytest.approx(iv.high * rate, rel=1e-3, abs=1e-3)


# ---------------------------------------------------------------------------
# Predicted block probability vs the deterministic guard
# ---------------------------------------------------------------------------


def test_block_probability_tracks_limit_distance(monkeypatch):
    cell = _corpus(80)
    near = _forecast(cell, runway=_runway(turns=1), burn=500.0, monkeypatch=monkeypatch)
    far = _forecast(cell, runway=_runway(turns=500), burn=50000.0, monkeypatch=monkeypatch)
    p_near = near.predicted_block_probability
    p_far = far.predicted_block_probability
    assert p_near.state is ValueState.ESTIMATED and p_far.state is ValueState.ESTIMATED
    assert p_near.value > p_far.value
    assert p_near.value >= 0.5
    assert p_far.value <= 0.1


def test_hard_stop_is_never_overridden(monkeypatch):
    forecast = _forecast(
        _corpus(80),
        runway=Runway(
            status=RunwayStatus.AVAILABLE,
            turns=0,
            binding_constraint=BindingConstraint.ROLLING_CAP,
            guard_state=GuardState.HARD_STOP,
        ),
        monkeypatch=monkeypatch,
    )
    prob = forecast.predicted_block_probability
    assert prob.state is ValueState.UNAVAILABLE
    assert "hard stop" in prob.reason


def test_unavailable_runway_blanks_probability(monkeypatch):
    forecast = _forecast(
        _corpus(80),
        runway=_runway(status=RunwayStatus.UNAVAILABLE),
        monkeypatch=monkeypatch,
    )
    assert forecast.predicted_block_probability.state is ValueState.UNAVAILABLE


# ---------------------------------------------------------------------------
# Performance and cache honesty (QA finding F-1)
# ---------------------------------------------------------------------------


def test_capacity_corpus_stays_inside_surface_budget(tmp_path, monkeypatch):
    """At the module's own MAX_SESSIONS cap the forecast must stay far below
    the 5s client timeout of the default-on surfaces, and a repeat evaluation
    must be near-free via the fingerprint cache while returning identical
    values."""
    import time as _time

    db = tmp_path / "monitor.db"
    _seed_history_db(db, _corpus(cal.MAX_SESSIONS, seed=41))
    kwargs = dict(
        monitor_db_path=str(db),
        now=NOW,
        session_id="active",
        model="model-a",
        effort="unknown",
        turn_index=4,
        spent_tokens=25000.0,
        runway=_runway(),
        burn_tokens_per_turn=6000.0,
        session_blended_usd_rate=None,
    )
    t0 = _time.monotonic()
    first = cal.build_calibrated_forecast(**kwargs)
    cold = _time.monotonic() - t0
    t0 = _time.monotonic()
    second = cal.build_calibrated_forecast(**kwargs)
    warm = _time.monotonic() - t0
    assert first.status is ForecastStatus.AVAILABLE
    assert second.to_dict() == first.to_dict()  # cache returns identical values
    assert cold < 5.0, f"cold evaluation took {cold:.2f}s"
    assert warm < 0.5, f"cached evaluation took {warm:.2f}s"


def test_cache_invalidates_on_ledger_append(tmp_path):
    db = tmp_path / "monitor.db"
    _seed_history_db(db, _corpus(30, seed=43))
    h1 = cal.read_history(str(db), now=NOW)
    _seed_history_db_append_one(db)
    h2 = cal.read_history(str(db), now=NOW)
    assert len(h2) == len(h1) + 1  # fingerprint change forced a fresh parse


def _seed_history_db_append_one(path):
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(str(path))
    base = _HISTORY_END - timedelta(days=1)
    for ti in range(6):
        conn.execute(
            "INSERT INTO requests (session_id, model, reasoning_effort, timestamp,"
            " status_code, input_tokens, output_tokens, cache_read_tokens,"
            " cache_creation_tokens) VALUES ('hist-extra','model-a','unknown',?,200,4000,1000,0,0)",
            ((base + timedelta(minutes=ti)).isoformat(),),
        )
    conn.commit()
    conn.close()


def test_read_history_never_creates_a_database(tmp_path):
    missing = tmp_path / "definitely-absent.db"
    assert cal.read_history(str(missing), now=NOW) == []
    assert not missing.exists()  # read-only open must not create the file


def test_drift_refit_shifts_the_emitted_band(monkeypatch):
    """Pin the adaptive-refit behavior end-to-end: once drifting, the emitted
    band must reflect the recent regime, not the stale accumulated fit."""
    stable = _corpus(70, seed=3)
    shifted = _corpus(60, seed=4, growth=1.8)
    for i, s in enumerate(shifted):
        shifted[i] = cal.HistorySession(
            s.model, s.effort, _HISTORY_END - timedelta(minutes=60 - i), s.turn_costs
        )
    drifted_cell = stable + shifted
    forecast = _forecast(drifted_cell, monkeypatch=monkeypatch)
    assert forecast.status is ForecastStatus.AVAILABLE
    assert forecast.coverage.drift_state.value == "drifting"
    # The all-history band, for contrast:
    full_band = cal._band_for(drifted_cell, [], 4, cal.TARGET_50)
    recent_band = cal._band_for(drifted_cell[-cal.RECENT_WINDOW :], [], 4, cal.TARGET_50)
    assert full_band is not None and recent_band is not None
    # Growth-shifted regime has larger remaining multipliers: the refit band's
    # upper edge must sit above the stale full-history one, and the emitted
    # interval must match the refit fit, not the stale one.
    assert recent_band.hi_y > full_band.hi_y
    emitted_hi = forecast.remaining_tokens_likely_50.high
    assert emitted_hi == round(25000.0 * (math.exp(recent_band.hi_y) - 1.0))


def test_replay_bands_equal_the_deployed_path_on_pooled_corpora():
    """QA equivalence pin: the one-pass replay's band table must produce
    exactly the bands the live per-query path produces — on POOLED corpora,
    where the order-sensitive pooling borrow can silently diverge."""
    cell = _corpus(60, seed=51)
    pool = _corpus(100, seed=52, model="model-b")
    pool_near = cal._pool_near_table(pool)
    compared = 0
    diverged = 0
    for i in range(max(cal.MIN_CELL_SESSIONS // 2, 6), len(cell), cal.WF_BLOCK):
        step = cal._StepBands(cell[:i], pool_near)
        for k in range(1, cal.KMAX + 1):
            for target, one_sided in ((cal.TARGET_50, False), (cal.TARGET_90, True)):
                fast = step.band(k, target, one_sided=one_sided)
                live = cal._band_for(cell[:i], pool, k, target, one_sided=one_sided)
                compared += 1
                if (fast is None) != (live is None):
                    diverged += 1
                elif fast is not None and (fast.lo_y != live.lo_y or fast.hi_y != live.hi_y):
                    diverged += 1
    assert compared > 200
    assert diverged == 0, f"{diverged}/{compared} band queries diverge from the live path"
