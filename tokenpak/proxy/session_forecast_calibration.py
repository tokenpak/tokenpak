# SPDX-License-Identifier: Apache-2.0
"""Calibrated remaining-token forecast: bucketed quantiles + split conformal.

Re-derivation of the archived offline research harness for production use,
with the model class deliberately simplified to dependency-free empirical
quantiles so the proxy carries no ML runtime. What is kept from the research
is the part that made its numbers honest:

- the target is the LOG REMAINING MULTIPLIER ``y = log(max(total/spent, 1))``
  of *finished* sessions, never a point blend with already-spent tokens;
- quantile bands are fit on a chronological train split and widened by
  split-conformal correction measured on the most recent calibration split,
  so the band's label is made true by construction rather than asserted;
- cells (model × effort) partially pool toward the global sample with a
  strength that fades as the cell's own history deepens;
- coverage is MEASURED by a walk-forward replay over the cell's own history
  (fit strictly on the past, score strictly on the future) and reported as
  observed coverage — a band whose measured coverage drifts is reported
  drifting and refit on the recent window, never relabeled nominal;
- a cold cell borrows the versioned built-in prior for internal readiness
  but renders ``learning`` — predictions appear only once the cell's own
  measured history clears the trust floor.

Everything here is a pure function of the ledger rows plus the caller's
evaluation time: no clock reads, no writes, no network. That is what lets
the non-self-metering restart proof hold for the calibrated path too.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from tokenpak.core.contracts.session_economics import (
    Coverage,
    DriftState,
    Forecast,
    ForecastStatus,
    GuardState,
    IntervalEstimate,
    NumericValue,
    Runway,
    RunwayStatus,
    ValueState,
)

logger = logging.getLogger(__name__)

#: Version label for the built-in cold-start prior. The prior's constants are
#: conservative shape parameters, not published claims; the version string is
#: surfaced in the learning reason so a borrowed prior is always attributable.
PRIOR_VERSION = "tokenpak-builtin-prior/1"

#: Trust floor: a cell forecasts only after this many of its own finished
#: sessions have been measured by the walk-forward replay.
MIN_CELL_SESSIONS = 20
#: Minimum scored walk-forward points before observed coverage is trusted.
MIN_SCORED_POINTS = 20
#: A session counts as finished once idle past this horizon.
COMPLETION_IDLE_SECONDS = 6 * 3600
#: Turn-index ceiling; deeper turns share the last bucket.
KMAX = 15
#: Neighbor window when gathering samples for a turn index.
K_WINDOW = 1
#: Partial-pooling strength (in samples) toward the global pool.
POOL_STRENGTH = 24.0
#: Central band target for the 50% likely range.
TARGET_50 = 0.50
#: One-sided ceiling target.
TARGET_90 = 0.90
#: Walk-forward scoring block (sessions per step).
WF_BLOCK = 8
#: Recent-window size for the drift arm.
RECENT_WINDOW = 60
#: Coverage shortfall (percentage points) that flags drift.
DRIFT_TOLERANCE = 12.0
#: Bound history reads; newest sessions win.
MAX_SESSIONS = 400
#: Minimum finished-session turn count to enter the corpus (research floor).
MIN_TURNS = 4

#: Built-in prior samples of the log remaining multiplier, expressed per
#: turn-bucket as conservative wide shapes. Used only for internal pooling
#: while a cell is cold; a cold cell still renders ``learning``.
_PRIOR_Y_BY_K: dict[int, tuple[float, ...]] = {
    1: (0.1, 0.4, 0.8, 1.3, 1.9, 2.6),
    3: (0.05, 0.25, 0.55, 0.95, 1.5, 2.1),
    6: (0.02, 0.15, 0.35, 0.65, 1.05, 1.6),
    10: (0.01, 0.08, 0.2, 0.4, 0.7, 1.1),
    KMAX: (0.0, 0.05, 0.12, 0.25, 0.45, 0.8),
}


@dataclass(frozen=True)
class HistorySession:
    """One finished session's per-turn total-token sequence."""

    model: str
    effort: str
    ended_at: datetime
    turn_costs: tuple[float, ...]

    @property
    def total(self) -> float:
        return float(sum(self.turn_costs))

    @property
    def turns(self) -> int:
        return len(self.turn_costs)


@dataclass(frozen=True)
class CellReadiness:
    sessions: int
    scored_points: int
    observed_coverage_50: float | None
    observed_coverage_90: float | None
    drift_state: DriftState


def _row_total(row: sqlite3.Row) -> float:
    """Per-turn total token weight, preferring provider-observed counts."""
    for cols in (
        (
            "provider_input_tokens",
            "provider_output_tokens",
            "provider_cache_read_tokens",
            "provider_cache_creation_tokens",
        ),
        ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"),
    ):
        values = [row[c] for c in cols]
        if any(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in values):
            return float(
                sum(
                    float(v)
                    for v in values
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
                )
            )
    return 0.0


def _parse_ts_utc(value: object) -> datetime | None:
    """Ledger timestamp → UTC (naive local wall-clock strings get the host zone)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    from datetime import timezone

    return parsed.astimezone(timezone.utc)


def read_history(
    monitor_db_path: str | None,
    *,
    now: datetime,
    exclude_session: str = "",
) -> list[HistorySession]:
    """Finished-session corpus from the same ledger table the engine reads.

    Read-only and deterministic given the database contents and ``now``:
    a session is finished when its last completed row is idle past the
    completion horizon. The active session is excluded — its total is not
    yet ground truth.
    """
    if not monitor_db_path:
        return []
    try:
        conn = sqlite3.connect(monitor_db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
            ).fetchone()
            if table is None:
                return []
            rows = conn.execute(
                "SELECT session_id, model, reasoning_effort, timestamp, "
                "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
                "provider_input_tokens, provider_output_tokens, "
                "provider_cache_read_tokens, provider_cache_creation_tokens "
                "FROM requests "
                "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
                "AND status_code BETWEEN 200 AND 599 "
                "ORDER BY timestamp ASC, id ASC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("calibration history read failed: %s", exc)
        return []

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        sid = str(row["session_id"]).strip()
        if sid and sid != exclude_session:
            grouped.setdefault(sid, []).append(row)

    horizon = now - timedelta(seconds=COMPLETION_IDLE_SECONDS)
    sessions: list[HistorySession] = []
    for sid, srows in grouped.items():
        last_ts = _parse_ts_utc(srows[-1]["timestamp"])
        if last_ts is None or last_ts > horizon:
            continue  # unfinished or unparseable — not ground truth
        costs = tuple(c for c in (_row_total(r) for r in srows) if c > 0)
        if len(costs) < MIN_TURNS:
            continue
        model = str(srows[-1]["model"] or "unknown").strip() or "unknown"
        effort = str(srows[-1]["reasoning_effort"] or "unknown").strip() or "unknown"
        sessions.append(
            HistorySession(model=model, effort=effort, ended_at=last_ts, turn_costs=costs)
        )
    sessions.sort(key=lambda s: s.ended_at)
    return sessions[-MAX_SESSIONS:]


def _cell_key(model: str, effort: str) -> tuple[str, str]:
    return (model.strip() or "unknown", effort.strip() or "unknown")


def _samples(sessions: Sequence[HistorySession]) -> list[tuple[int, float]]:
    """(turn-index, y) samples; y = log remaining multiplier at that turn."""
    out: list[tuple[int, float]] = []
    for s in sessions:
        total = s.total
        spent = 0.0
        for k, cost in enumerate(s.turn_costs, start=1):
            spent += cost
            if k >= s.turns:
                break  # at the final turn nothing remains — degenerate sample
            if k > KMAX or spent <= 0:
                continue
            out.append((k, math.log(max(total / spent, 1.0))))
    return out


def _near(samples: Sequence[tuple[int, float]], k: int) -> list[float]:
    kk = min(k, KMAX)
    lo, hi = kk - K_WINDOW, kk + K_WINDOW
    return [y for sk, y in samples if lo <= sk <= hi]


def _prior_near(k: int) -> list[float]:
    kk = min(k, KMAX)
    key = min(_PRIOR_Y_BY_K, key=lambda p: abs(p - kk))
    return list(_PRIOR_Y_BY_K[key])


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile of empty sample")
    pos = (len(ordered) - 1) * min(max(q, 0.0), 1.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(ordered[lo])
    frac = pos - lo
    return float(ordered[lo] * (1 - frac) + ordered[hi] * frac)


def _pooled(cell: list[float], pool: list[float], prior: list[float]) -> list[float]:
    """Partial pooling: the cell's own samples plus a fading share of others."""
    n = len(cell)
    weight = POOL_STRENGTH / (n + POOL_STRENGTH)
    take_pool = int(round(weight * min(len(pool), int(POOL_STRENGTH * 2))))
    borrowed = pool[:take_pool] if take_pool else []
    base = cell + borrowed
    if len(base) < 8:
        base = base + prior
    return base


@dataclass(frozen=True)
class _Band:
    lo_y: float
    hi_y: float


def _conformal_band(
    train: Sequence[float],
    calib: Sequence[float],
    target: float,
    *,
    one_sided: bool = False,
) -> _Band:
    """Empirical quantile band widened by the calibration-split miss quantile."""
    if one_sided:
        lo_q, hi_q = 0.0, target
    else:
        alpha = 1.0 - target
        lo_q, hi_q = alpha / 2.0, 1.0 - alpha / 2.0
    lo = 0.0 if one_sided else _quantile(train, lo_q)
    hi = _quantile(train, hi_q)
    if calib:
        misses = [max(lo - y, y - hi) for y in calib]
        rank = min(1.0, target * (1.0 + 1.0 / max(1, len(calib))))
        widen = max(0.0, _quantile(misses, rank))
    else:
        widen = 0.0
    lo_adj = 0.0 if one_sided else max(0.0, lo - widen)
    return _Band(lo_y=lo_adj, hi_y=max(hi + widen, lo_adj))


def _split(sessions: Sequence[HistorySession]) -> tuple[list[HistorySession], list[HistorySession]]:
    """Chronological train/calibration split (most recent quarter calibrates)."""
    c = max(3, len(sessions) // 4)
    return list(sessions[:-c]), list(sessions[-c:])


def _band_for(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
    k: int,
    target: float,
    *,
    one_sided: bool = False,
) -> _Band | None:
    train_s, calib_s = _split(cell_sessions)
    cell_train = _near(_samples(train_s), k)
    cell_calib = _near(_samples(calib_s), k)
    pool = _near(_samples(pool_sessions), k)
    base = _pooled(cell_train, pool, _prior_near(k))
    if len(base) < 6:
        return None
    return _conformal_band(base, cell_calib, target, one_sided=one_sided)


def walk_forward_coverage(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
    target: float,
    *,
    one_sided: bool = False,
    score_tail: int | None = None,
) -> tuple[float | None, int]:
    """Measured coverage of this exact procedure, fit on past / scored on future.

    Returns (coverage percent, scored points). Chronological blocks: for each
    step the band is built from sessions strictly before the block and scored
    on the block's turn samples. ``score_tail`` restricts SCORING to the most
    recent N sessions while still fitting on all prior history — that is the
    drift instrument: a full-history fit that no longer covers recent reality
    is drifting, however self-consistent the recent window looks on its own.
    """
    inside = 0
    scored = 0
    first_scored = 0 if score_tail is None else max(0, len(cell_sessions) - score_tail)
    for i in range(max(MIN_CELL_SESSIONS // 2, 6), len(cell_sessions), WF_BLOCK):
        history = cell_sessions[:i]
        block = [
            s for j, s in enumerate(cell_sessions[i : i + WF_BLOCK], start=i) if j >= first_scored
        ]
        for s in block:
            total = s.total
            spent = 0.0
            for k, cost in enumerate(s.turn_costs, start=1):
                spent += cost
                if k >= s.turns or k > KMAX or spent <= 0:
                    continue
                band = _band_for(history, pool_sessions, k, target, one_sided=one_sided)
                if band is None:
                    continue
                lo_t = spent * math.exp(band.lo_y)
                hi_t = spent * math.exp(band.hi_y)
                scored += 1
                if (one_sided and total <= hi_t) or (not one_sided and lo_t <= total <= hi_t):
                    inside += 1
    if scored == 0:
        return None, 0
    return 100.0 * inside / scored, scored


def cell_readiness(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
) -> CellReadiness:
    """Trust assessment for a cell: measured coverage and drift state."""
    n = len(cell_sessions)
    if n < MIN_CELL_SESSIONS:
        return CellReadiness(n, 0, None, None, DriftState.UNKNOWN)
    cov50, pts50 = walk_forward_coverage(cell_sessions, pool_sessions, TARGET_50)
    cov90, _pts90 = walk_forward_coverage(cell_sessions, pool_sessions, TARGET_90, one_sided=True)
    drift = DriftState.UNKNOWN
    if cov50 is not None and pts50 >= MIN_SCORED_POINTS:
        if n > RECENT_WINDOW:
            # Score the full-history fit on the recent tail only: if the
            # accumulated model no longer covers recent sessions, forget.
            cov_recent, pts_recent = walk_forward_coverage(
                cell_sessions, pool_sessions, TARGET_50, score_tail=RECENT_WINDOW
            )
            if cov_recent is not None and pts_recent >= MIN_SCORED_POINTS // 2:
                shortfall = (TARGET_50 * 100.0) - cov_recent
                drift = DriftState.DRIFTING if shortfall > DRIFT_TOLERANCE else DriftState.STABLE
            else:
                drift = DriftState.STABLE
        else:
            drift = DriftState.STABLE
    return CellReadiness(n, pts50, cov50, cov90, drift)


def _expected_turns(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
    k: int,
) -> tuple[int, int] | None:
    """Central-50% remaining-turn interval from finished-session lengths."""
    remaining = [s.turns - k for s in cell_sessions if s.turns > k]
    weight = POOL_STRENGTH / (len(remaining) + POOL_STRENGTH)
    borrow = [s.turns - k for s in pool_sessions if s.turns > k]
    remaining += borrow[: int(round(weight * min(len(borrow), 48)))]
    if len(remaining) < 6:
        return None
    lo = max(1, int(round(_quantile(remaining, 0.25))))
    hi = max(lo, int(round(_quantile(remaining, 0.75))))
    return lo, hi


def _source(readiness: CellReadiness) -> str:
    return f"walk-forward split-conformal empirical quantiles ({readiness.sessions} sessions)"


def build_calibrated_forecast(
    *,
    monitor_db_path: str | None,
    now: datetime,
    session_id: str,
    model: str,
    effort: str,
    turn_index: int,
    spent_tokens: float,
    runway: Runway,
    burn_tokens_per_turn: float | None,
    session_blended_usd_rate: float | None,
) -> Forecast:
    """Contract-shaped calibrated forecast, honest about every gap.

    ``session_blended_usd_rate`` must come from a fresh rate-card estimated
    cost (USD per token); ``None`` means USD is unavailable while token
    ranges remain intact. Any internal failure degrades to a learning /
    unavailable status — never an exception, never a fabricated number.
    """
    reason_unavailable = ""
    if spent_tokens <= 0 or turn_index < 1:
        return _status_forecast(ForecastStatus.UNAVAILABLE, "no completed spend to forecast from")
    history = read_history(monitor_db_path, now=now, exclude_session=session_id)
    key = _cell_key(model, effort)
    cell = [s for s in history if _cell_key(s.model, s.effort) == key]
    pool = [s for s in history if _cell_key(s.model, s.effort) != key]

    readiness = cell_readiness(cell, pool)
    if readiness.sessions < MIN_CELL_SESSIONS or readiness.scored_points < MIN_SCORED_POINTS:
        return _status_forecast(
            ForecastStatus.LEARNING,
            (
                f"learning: {readiness.sessions} finished sessions for this "
                f"model/effort (needs {MIN_CELL_SESSIONS}); borrowing "
                f"{PRIOR_VERSION} until the cell's own coverage is measured"
            ),
        )
    if readiness.observed_coverage_50 is None:
        return _status_forecast(
            ForecastStatus.LEARNING, "learning: walk-forward coverage not yet measurable"
        )

    fit_cell = cell[-RECENT_WINDOW:] if readiness.drift_state is DriftState.DRIFTING else cell
    band50 = _band_for(fit_cell, pool, turn_index, TARGET_50)
    band90 = _band_for(fit_cell, pool, turn_index, TARGET_90, one_sided=True)
    turns_iv = _expected_turns(fit_cell, pool, turn_index)
    if band50 is None or band90 is None or turns_iv is None:
        return _status_forecast(
            ForecastStatus.LEARNING, "learning: insufficient samples at this turn depth"
        )

    lo_rem = max(0.0, spent_tokens * (math.exp(band50.lo_y) - 1.0))
    hi_rem = max(lo_rem, spent_tokens * (math.exp(band50.hi_y) - 1.0))
    ceil_rem = max(hi_rem, spent_tokens * (math.exp(band90.hi_y) - 1.0))
    source = _source(readiness)

    tokens_50 = IntervalEstimate(
        state=ValueState.ESTIMATED,
        low=round(lo_rem),
        high=round(hi_rem),
        source=source,
        unit="tokens",
    )
    tokens_90 = NumericValue.estimated(round(ceil_rem), source=source, unit="tokens")
    if session_blended_usd_rate is not None and session_blended_usd_rate > 0:
        usd_50 = IntervalEstimate(
            state=ValueState.ESTIMATED,
            low=round(lo_rem * session_blended_usd_rate, 4),
            high=round(hi_rem * session_blended_usd_rate, 4),
            source=source,
            unit="usd",
        )
        usd_90 = NumericValue.estimated(
            round(ceil_rem * session_blended_usd_rate, 4), source=source, unit="usd"
        )
    else:
        usd_50 = IntervalEstimate(
            state=ValueState.UNAVAILABLE,
            reason="rate provenance is stale or unknown",
            unit="usd",
        )
        usd_90 = NumericValue.unavailable("rate provenance is stale or unknown", unit="usd")

    expected = IntervalEstimate(
        state=ValueState.ESTIMATED,
        low=turns_iv[0],
        high=turns_iv[1],
        source=source,
        unit="turns",
    )

    block_prob = _predicted_block(
        fit_cell, pool, turn_index, spent_tokens, runway, burn_tokens_per_turn, source
    )

    coverage = Coverage(
        method="walk-forward split-conformal empirical-quantile v1",
        observed=round(min(readiness.observed_coverage_50, 100.0) / 100.0, 4),
        history_n=readiness.sessions,
        drift_state=readiness.drift_state,
    )
    return Forecast(
        status=ForecastStatus.AVAILABLE,
        remaining_tokens_likely_50=tokens_50,
        remaining_tokens_ceiling_90=tokens_90,
        remaining_cost_usd_likely_50=usd_50,
        remaining_cost_usd_ceiling_90=usd_90,
        expected_turns=expected,
        coverage=coverage,
        predicted_block_probability=block_prob,
        reason=reason_unavailable,
    )


def _predicted_block(
    fit_cell: Sequence[HistorySession],
    pool: Sequence[HistorySession],
    turn_index: int,
    spent_tokens: float,
    runway: Runway,
    burn_tokens_per_turn: float | None,
    source: str,
) -> NumericValue:
    """P(remaining consumption crosses the binding limit) from the y-sample.

    The limit distance comes from the deterministic runway (turns × burn):
    the forecast NEVER re-derives or overrides guard decisions, and a hard
    stop is already a fact — probability adds nothing to it.
    """
    if runway.guard_state is GuardState.HARD_STOP:
        return NumericValue.unavailable(
            "guard hard stop is active; probability is not applicable",
            unit="probability",
        )
    if (
        runway.status is not RunwayStatus.AVAILABLE
        or runway.turns is None
        or burn_tokens_per_turn is None
        or burn_tokens_per_turn <= 0
    ):
        return NumericValue.unavailable("binding runway or burn is unavailable", unit="probability")
    limit_remaining = float(runway.turns) * float(burn_tokens_per_turn)
    ys = _near(_samples(fit_cell), turn_index)
    weight = POOL_STRENGTH / (len(ys) + POOL_STRENGTH)
    borrow = _near(_samples(pool), turn_index)
    ys = ys + borrow[: int(round(weight * min(len(borrow), 48)))]
    if len(ys) < 8:
        return NumericValue.unavailable(
            "insufficient samples for block probability", unit="probability"
        )
    crossing = sum(1 for y in ys if spent_tokens * (math.exp(y) - 1.0) >= limit_remaining)
    return NumericValue.estimated(round(crossing / len(ys), 4), source=source, unit="probability")


def _status_forecast(status: ForecastStatus, reason: str) -> Forecast:
    def interval(unit: str) -> IntervalEstimate:
        return IntervalEstimate(state=ValueState.UNAVAILABLE, unit=unit)

    def numeric(unit: str) -> NumericValue:
        return NumericValue.unavailable(unit=unit)

    return Forecast(
        status=status,
        remaining_tokens_likely_50=interval("tokens"),
        remaining_tokens_ceiling_90=numeric("tokens"),
        remaining_cost_usd_likely_50=interval("usd"),
        remaining_cost_usd_ceiling_90=numeric("usd"),
        expected_turns=interval("turns"),
        coverage=Coverage(),
        predicted_block_probability=numeric("probability"),
        reason=reason,
    )


__all__ = [
    "COMPLETION_IDLE_SECONDS",
    "KMAX",
    "MIN_CELL_SESSIONS",
    "MIN_SCORED_POINTS",
    "PRIOR_VERSION",
    "CellReadiness",
    "HistorySession",
    "build_calibrated_forecast",
    "cell_readiness",
    "read_history",
    "walk_forward_coverage",
]
