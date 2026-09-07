# SPDX-License-Identifier: Apache-2.0
"""Offline walk-forward measurement tools: ``read_time_history``,
``cell_readiness_time``, ``walk_forward_coverage_time`` against a real sqlite
corpus.

These are the offline evidence-publishing job's tools (see
``time_forecast_calibration.py``'s module docstring) — never called by the
live forecast path, but they must independently read the same
``monitor.db.requests`` table the proxy writes to, gate on the same
timing-facts-only input list, and measure coverage honestly against a corpus
with a KNOWN generating process.
"""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokenpak.core.contracts.session_economics import DriftState, TimeForecastStreamMode
from tokenpak.proxy import time_forecast_calibration as tf_cal

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
_HISTORY_END = NOW - timedelta(days=2)


def _mk_session(
    rng: random.Random,
    *,
    scale: float = 1.0,
    growth: float = 1.0,
    model: str = "model-a",
    effort: str = "unknown",
    stream_mode: TimeForecastStreamMode = TimeForecastStreamMode.STREAMING,
    ended_at: datetime,
) -> tf_cal.TimeHistorySession:
    turns = rng.randint(6, 16)
    durations = tuple(
        max(200.0, rng.lognormvariate(math.log(4000.0 * scale), 0.6)) * (growth**i)
        for i in range(turns)
    )
    return tf_cal.TimeHistorySession(
        model=model,
        effort=effort,
        stream_mode=stream_mode,
        ended_at=ended_at,
        turn_durations_ms=durations,
    )


def _corpus(
    n: int, *, seed: int = 7, scale: float = 1.0, growth: float = 1.0, model: str = "model-a"
) -> list[tf_cal.TimeHistorySession]:
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


def _seed_ledger(
    path: Path,
    sessions: list[tf_cal.TimeHistorySession],
    *,
    active_rows: int = 0,
    active_session_id: str = "active",
) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY, session_id TEXT, model TEXT,"
        " reasoning_effort TEXT, timestamp TEXT, status_code INTEGER,"
        " stream_mode TEXT, ttfb_ms INTEGER, stream_duration_ms INTEGER)"
    )
    for si, s in enumerate(sessions):
        start = s.ended_at - timedelta(minutes=len(s.turn_durations_ms))
        stream_col = "sse" if s.stream_mode is TimeForecastStreamMode.STREAMING else "json"
        for ti, duration in enumerate(s.turn_durations_ms):
            conn.execute(
                "INSERT INTO requests (session_id, model, reasoning_effort, timestamp,"
                " status_code, stream_mode, ttfb_ms, stream_duration_ms)"
                " VALUES (?,?,?,?,200,?,?,?)",
                (
                    f"hist-{si}",
                    s.model,
                    s.effort,
                    (start + timedelta(minutes=ti)).isoformat(),
                    stream_col,
                    int(duration * 0.1),
                    int(duration * 0.9),
                ),
            )
    for ti in range(active_rows):
        conn.execute(
            "INSERT INTO requests (session_id, model, reasoning_effort, timestamp,"
            " status_code, stream_mode, ttfb_ms, stream_duration_ms)"
            " VALUES (?,'model-a','unknown',?,200,'sse',100,900)",
            (
                active_session_id,
                (NOW - timedelta(minutes=active_rows - ti)).isoformat(),
            ),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# read_time_history
# ---------------------------------------------------------------------------


def test_read_time_history_groups_gates_and_excludes_active(tmp_path: Path) -> None:
    db = tmp_path / "monitor.db"
    sessions = _corpus(6)
    # One too-short session (below MIN_TURNS populated-duration turns) and one
    # still-active (too-recent) session must both be excluded.
    short = tf_cal.TimeHistorySession(
        "model-a", "unknown", TimeForecastStreamMode.STREAMING, _HISTORY_END, (500.0, 600.0)
    )
    recent = tf_cal.TimeHistorySession(
        "model-a",
        "unknown",
        TimeForecastStreamMode.STREAMING,
        NOW - timedelta(minutes=30),
        (500.0,) * 6,
    )
    _seed_ledger(db, sessions + [short, recent], active_rows=3)

    out = tf_cal.read_time_history(str(db), now=NOW, exclude_session="active")

    assert len(out) == 6
    assert all(s.turns >= tf_cal.MIN_TURNS for s in out)
    assert all(s.ended_at <= NOW - timedelta(seconds=tf_cal.COMPLETION_IDLE_SECONDS) for s in out)


@pytest.mark.parametrize(
    ("has_source", "has_raw", "expected_effort"),
    [
        (False, False, "unknown"),
        (False, True, None),
        (True, False, None),
        (True, True, "high"),
    ],
)
def test_read_time_history_supports_every_optional_effort_provenance_schema(
    tmp_path: Path,
    has_source: bool,
    has_raw: bool,
    expected_effort: str | None,
) -> None:
    db = tmp_path / "monitor.db"
    _seed_ledger(db, _corpus(1))
    conn = sqlite3.connect(str(db))
    try:
        if has_source:
            conn.execute("ALTER TABLE requests ADD COLUMN reasoning_effort_source TEXT")
        if has_raw:
            conn.execute("ALTER TABLE requests ADD COLUMN reasoning_effort_raw TEXT")
        if has_source and has_raw:
            conn.execute(
                "UPDATE requests SET reasoning_effort = 'high', "
                "reasoning_effort_source = 'request_body', reasoning_effort_raw = 'high'"
            )
        elif has_source:
            conn.execute(
                "UPDATE requests SET reasoning_effort_source = 'request_body_unrecognized'"
            )
        elif has_raw:
            conn.execute("UPDATE requests SET reasoning_effort_raw = 'xhigh'")
        conn.commit()
    finally:
        conn.close()

    out = tf_cal.read_time_history(str(db), now=NOW)

    if expected_effort is None:
        assert out == []
    else:
        assert len(out) == 1
        assert out[0].effort == expected_effort


def test_read_time_history_exclude_session_removes_only_that_session(tmp_path: Path) -> None:
    db = tmp_path / "monitor.db"
    _seed_ledger(db, _corpus(6))
    # A history session is finished and excludable by its ledger session_id
    # ("hist-0" .. "hist-5"), matching _seed_ledger's naming.
    included = tf_cal.read_time_history(str(db), now=NOW)
    excluded = tf_cal.read_time_history(str(db), now=NOW, exclude_session="hist-0")
    assert len(included) == 6
    assert len(excluded) == len(included) - 1


def test_read_time_history_missing_db_is_empty() -> None:
    assert tf_cal.read_time_history(None, now=NOW) == []
    assert tf_cal.read_time_history("/nonexistent/monitor.db", now=NOW) == []


def test_read_time_history_missing_table_is_empty(tmp_path: Path) -> None:
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE other (id INTEGER)")
    conn.commit()
    conn.close()
    assert tf_cal.read_time_history(str(db), now=NOW) == []


def test_read_time_history_never_creates_a_database(tmp_path: Path) -> None:
    db = tmp_path / "absent.db"
    assert tf_cal.read_time_history(str(db), now=NOW) == []
    assert not db.exists()


def test_read_time_history_ignores_rows_missing_both_timing_facts(tmp_path: Path) -> None:
    """A row with neither ttfb_ms nor stream_duration_ms contributes no
    duration sample — never a fabricated zero, per the closed timing-facts
    input list."""
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY, session_id TEXT, model TEXT,"
        " reasoning_effort TEXT, timestamp TEXT, status_code INTEGER,"
        " stream_mode TEXT, ttfb_ms INTEGER, stream_duration_ms INTEGER)"
    )
    start = _HISTORY_END - timedelta(minutes=10)
    for ti in range(8):
        # Every other row has no timing facts at all.
        ttfb = None if ti % 2 == 0 else 100
        dur = None if ti % 2 == 0 else 900
        conn.execute(
            "INSERT INTO requests (session_id, model, reasoning_effort, timestamp,"
            " status_code, stream_mode, ttfb_ms, stream_duration_ms)"
            " VALUES ('hist-0','model-a','unknown',?,200,'sse',?,?)",
            ((start + timedelta(minutes=ti)).isoformat(), ttfb, dur),
        )
    conn.commit()
    conn.close()

    out = tf_cal.read_time_history(str(db), now=NOW)
    # Only 4 of the 8 rows carry any timing fact; MIN_TURNS is 4, so the
    # session is right at the floor and must still be included with exactly
    # those 4 durations (never padded with an invented duration for the rest).
    assert len(out) == 1
    assert out[0].turns == 4


def test_read_time_history_excludes_mixed_stream_mode_sessions(tmp_path: Path) -> None:
    """A session whose rows map to more than one distinct ``stream_mode``
    (streaming AND non-streaming turns) must be excluded from the corpus
    entirely, never classified by its last row.

    Eight turns, alternating streaming/non-streaming, all with populated
    timing facts — comfortably past MIN_TURNS on its own, so a last-row
    classification bug would silently admit the whole session into whichever
    cell its final row happened to land in.
    """
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY, session_id TEXT, model TEXT,"
        " reasoning_effort TEXT, timestamp TEXT, status_code INTEGER,"
        " stream_mode TEXT, ttfb_ms INTEGER, stream_duration_ms INTEGER)"
    )
    start = _HISTORY_END - timedelta(minutes=10)
    for ti in range(8):
        stream_col = "sse" if ti % 2 == 0 else "json"
        conn.execute(
            "INSERT INTO requests (session_id, model, reasoning_effort, timestamp,"
            " status_code, stream_mode, ttfb_ms, stream_duration_ms)"
            " VALUES ('mixed-0','model-a','unknown',?,200,?,100,900)",
            ((start + timedelta(minutes=ti)).isoformat(), stream_col),
        )
    conn.commit()
    conn.close()

    out = tf_cal.read_time_history(str(db), now=NOW)
    assert out == []


def test_read_time_history_excludes_mixed_model_and_effort_sessions(tmp_path: Path) -> None:
    db = tmp_path / "monitor.db"
    _seed_ledger(db, _corpus(3))
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE requests SET model = 'model-b' "
            "WHERE id = (SELECT id FROM requests WHERE session_id = 'hist-1' ORDER BY id LIMIT 1)"
        )
        conn.execute(
            "UPDATE requests SET reasoning_effort = 'high' "
            "WHERE id = (SELECT id FROM requests WHERE session_id = 'hist-2' ORDER BY id LIMIT 1)"
        )
        conn.commit()
    finally:
        conn.close()

    out = tf_cal.read_time_history(str(db), now=NOW)

    assert len(out) == 1
    assert out[0].model == "model-a"
    assert out[0].effort == "unknown"


# ---------------------------------------------------------------------------
# walk_forward_coverage_time / cell_readiness_time — measured, not nominal
# ---------------------------------------------------------------------------


def test_cold_cell_is_not_ready(tmp_path: Path) -> None:
    readiness = tf_cal.cell_readiness_time(_corpus(5), [])
    assert readiness.sessions == 5
    assert readiness.scored_points == 0
    assert readiness.observed_coverage_50 is None
    assert readiness.drift_state is DriftState.UNKNOWN


def test_warm_cell_measures_plausible_coverage() -> None:
    cell = _corpus(120, seed=11)
    measured, scored = tf_cal.walk_forward_coverage_time(cell, [], tf_cal.TARGET_50)
    assert scored > 100
    # A conformal 50% central band on a stationary corpus sits in a broad
    # honesty window around its label — never wildly off, never pinned to
    # 100 by construction.
    assert 30.0 <= measured <= 85.0, measured


def test_readiness_reports_the_same_measured_coverage() -> None:
    cell = _corpus(80, seed=9)
    readiness = tf_cal.cell_readiness_time(cell, [])
    measured, scored = tf_cal.walk_forward_coverage_time(cell, [], tf_cal.TARGET_50)
    assert scored >= tf_cal.MIN_SCORED_POINTS
    assert readiness.scored_points == scored
    assert readiness.observed_coverage_50 == measured


def test_one_sided_90_requires_matching_target() -> None:
    cell = _corpus(80, seed=9)
    with pytest.raises(ValueError, match="TARGET_90"):
        tf_cal.walk_forward_coverage_time(cell, [], tf_cal.TARGET_50, one_sided=True)


def test_central_50_path_rejects_a_mismatched_target() -> None:
    cell = _corpus(80, seed=9)
    with pytest.raises(ValueError, match="TARGET_50"):
        tf_cal.walk_forward_coverage_time(cell, [], tf_cal.TARGET_90)


def test_score_tail_requires_recent_window_and_target_50() -> None:
    cell = _corpus(80, seed=9)
    with pytest.raises(ValueError, match="RECENT_WINDOW"):
        tf_cal.walk_forward_coverage_time(cell, [], tf_cal.TARGET_50, score_tail=1)
    with pytest.raises(ValueError, match="score_tail"):
        tf_cal.walk_forward_coverage_time(
            cell, [], tf_cal.TARGET_90, score_tail=tf_cal.RECENT_WINDOW
        )


def test_stationary_corpus_is_stable() -> None:
    readiness = tf_cal.cell_readiness_time(_corpus(120, seed=11), [])
    assert readiness.drift_state is DriftState.STABLE
    assert readiness.sessions == 120


def test_distribution_shift_flags_drifting() -> None:
    stable = _corpus(70, seed=3)
    # Shape shift, not scale shift: the log-remaining-multiplier target is
    # scale-invariant, so a pure duration rescale is NOT drift. Steep
    # per-turn growth changes the multiplier distribution at every depth.
    shifted_base = _corpus(60, seed=4, growth=1.8)
    shifted = [
        tf_cal.TimeHistorySession(
            s.model,
            s.effort,
            s.stream_mode,
            _HISTORY_END - timedelta(minutes=60 - i),
            s.turn_durations_ms,
        )
        for i, s in enumerate(shifted_base)
    ]
    readiness = tf_cal.cell_readiness_time(stable + shifted, [])
    assert readiness.drift_state is DriftState.DRIFTING


# ---------------------------------------------------------------------------
# Shipped published entry — structural consistency with these same primitives
# ---------------------------------------------------------------------------


def test_shipped_sonnet_entry_bucket_keys_are_valid_for_kmax() -> None:
    """The one reviewed cell's published bands were produced by these exact
    offline primitives (``_TimeStepBands``/``KMAX``), never hand-edited — this
    guards that every bucket key stays inside ``1..KMAX`` and every band is a
    well-formed (lo <= hi) interval, so a future KMAX change can't silently
    leave stale out-of-range buckets in the shipped table."""
    evidence = tf_cal._PUBLISHED_TIME_PRIOR[("claude-sonnet-5", "unknown", "streaming")]
    assert evidence.band50_y_by_k, "shipped entry must publish at least one bucket"
    for k, (lo, hi) in evidence.band50_y_by_k.items():
        assert 1 <= k <= tf_cal.KMAX
        assert lo <= hi
    for k in evidence.band90_hi_y_by_k:
        assert 1 <= k <= tf_cal.KMAX
    assert evidence.history_n >= tf_cal.MIN_CELL_SESSIONS
    assert evidence.observed_coverage_50 >= tf_cal.TARGET_50 * 100.0
    assert evidence.observed_coverage_90 >= tf_cal.TARGET_90 * 100.0
