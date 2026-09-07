# SPDX-License-Identifier: Apache-2.0
"""Calibrated wall-clock time-remaining band: bucketed quantiles + split conformal.

Duration-domain sibling of ``session_forecast_calibration.py`` (the token-band
engine). It reuses that module's domain-agnostic statistical primitives
verbatim (``_pooled``, ``_conformal_band``, ``_split``) so both engines
measure coverage the SAME way — the identical walk-forward held-out replay
methodology the activation gate requires. Every other constant, class, and
function here is an independent duration-domain analog: the two engines'
trust floors and tuning knobs must be free to diverge without silently
coupling one domain's calibration to the other's.

The activation gate requires per-cell walk-forward coverage to be MEASURED
and PUBLISHED before a cell may render ``learning``/``available`` — not
merely accumulated by this module watching live traffic. ``_PUBLISHED_TIME_PRIOR``
is the concrete mechanism: only a cell with an explicit, reviewed entry here
may ever render ``learning``/``available``; every cell without one still
renders ``insufficient_data`` (once timing facts exist at all) regardless of
how much raw history the local ledger holds. As of this module's current
state, exactly one cell — ``claude-sonnet-5`` / unknown effort / streaming —
has cleared review and is published; every other cell remains unpublished
and therefore honest-``insufficient_data``. Each additional cell is
populated as its own explicit, reviewed change — never a side effect of this
module observing traffic, and never something a code change alone may flip.
The mechanism's separate default-off master switch
(``TOKENPAK_TIME_FORECAST_BANDS`` / ``time_forecast_bands.enabled``) gates
this table's entries independently: even a fully published, full-confidence
cell serves nothing until that switch is explicitly turned on.

The walk-forward measurement tooling below (``read_time_history``,
``cell_readiness_time``, ``walk_forward_coverage_time``) is what that later
initiative runs OFFLINE, against a real historical corpus, to produce the
evidence that ends up published here — it is deliberately NOT invoked by the
runtime forecast path, which only ever looks up already-published,
already-widened bands. That keeps the hot request path a simple, fast,
side-effect-free lookup, and keeps "measured and published" an explicit batch
step rather than a live one prone to cache/staleness/race conditions in a
proxy request path.

Everything here is a pure function of the ledger rows, the published-evidence
table, and the caller's evaluation time: no clock reads, no writes, no
network. No live-API calls anywhere, per the parent initiative's convention.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from tokenpak.core.contracts.session_economics import (
    Coverage,
    DriftState,
    IntervalEstimate,
    NumericValue,
    TimeForecast,
    TimeForecastCell,
    TimeForecastGate,
    TimeForecastStatus,
    TimeForecastStreamMode,
    ValueState,
)
from tokenpak.proxy.session_forecast_calibration import (
    _conformal_band,
    _pooled,
    _split,
)
from tokenpak.proxy.spend_guard.session_state import _reasoning_effort_cell

logger = logging.getLogger(__name__)

#: Fixed literal basis marker — a literal a test can assert against. Any code
#: path deriving a duration from anything other than the closed input list
#: (started_at / ttfb_ms / stream_duration_ms / turn count / cell key) is a
#: gate violation regardless of how it's presented.
BASIS = "timing-facts-v1"

# Independent, duration-domain trust-floor constants. Deliberately NOT shared
# with session_forecast_calibration.py's identically-named token-domain
# constants — the two domains' tuning knobs must be free to diverge without
# coupling.
#: Trust floor: a cell is scored by the walk-forward replay only after this
#: many of its own inactive session histories are available.
MIN_CELL_SESSIONS = 20
#: Minimum scored walk-forward points before observed coverage is trusted.
MIN_SCORED_POINTS = 20
#: Corpus closure proxy: session inactivity, never verified task completion.
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
#: Minimum turn count for an inactive session history to enter the corpus.
MIN_TURNS = 4

# Schema inspection selects one of these complete, static projections. No
# database-provided identifier is ever interpolated into executable SQL.
_CORPUS_QUERY_BY_EFFORT_PROVENANCE: dict[tuple[bool, bool], str] = {
    (False, False): (
        "SELECT session_id, model, reasoning_effort, "
        "'' AS reasoning_effort_source, '' AS reasoning_effort_raw, timestamp, "
        "stream_mode, ttfb_ms, stream_duration_ms "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
    (False, True): (
        "SELECT session_id, model, reasoning_effort, "
        "'' AS reasoning_effort_source, reasoning_effort_raw, timestamp, "
        "stream_mode, ttfb_ms, stream_duration_ms "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
    (True, False): (
        "SELECT session_id, model, reasoning_effort, "
        "reasoning_effort_source, '' AS reasoning_effort_raw, timestamp, "
        "stream_mode, ttfb_ms, stream_duration_ms "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
    (True, True): (
        "SELECT session_id, model, reasoning_effort, "
        "reasoning_effort_source, reasoning_effort_raw, timestamp, "
        "stream_mode, ttfb_ms, stream_duration_ms "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
}


@dataclass(frozen=True)
class PublishedTimeCellEvidence:
    """One cell's published, already-conformal-widened calibration bands.

    The offline walk-forward publishing job does the model-fitting and
    conformal widening once, using this module's (and the token engine's)
    reusable primitives; only the FINISHED per-turn-bucket bands are
    published here — the runtime path never refits. ``full_confidence`` is
    that job's own published judgement of whether the cell has cleared the
    local full-confidence threshold (``available``) or is still only
    prior-borrow worthy (``learning``); it is not derived from anything this
    module measures live.
    """

    prior_version: str
    #: turn-bucket -> (lo_y, hi_y) for the central 50% band, already widened.
    band50_y_by_k: Mapping[int, tuple[float, float]]
    #: turn-bucket -> hi_y for the one-sided 90% ceiling, already widened.
    band90_hi_y_by_k: Mapping[int, float]
    coverage_method: str
    #: Measured walk-forward coverage of the central 50% band (percent, e.g.
    #: ``64.9`` for 64.9%) — both this and ``observed_coverage_90`` are
    #: independent per-band figures; neither substitutes for the other, and
    #: ``drift_state`` below is a separate (non-coverage) signal.
    observed_coverage_50: float
    #: Measured walk-forward coverage of the one-sided 90% ceiling (percent,
    #: same units/scale as ``observed_coverage_50``). Symmetric sibling field
    #: — every published entry must carry both bands' measured coverage.
    observed_coverage_90: float
    history_n: int
    drift_state: DriftState
    full_confidence: bool


#: The published per-cell calibration evidence. Ships with exactly the cells
#: that have cleared review — see the module docstring for why this table is
#: the gate mechanism, not a placeholder to be filled in casually. Keyed by
#: ``_time_cell_key(...)``. Tests exercise this table via
#: ``build_calibrated_time_forecast(..., published_prior={...})`` (a real,
#: explicit function argument) rather than monkeypatching this module
#: attribute — real-path style, per the #633/#634 testing standard.
#:
#: ("claude-sonnet-5", "unknown", "streaming"): the walk-forward split-
#: conformal calibration re-measured against the live session ledger
#: (history_n=43, observed_coverage_50≈64.9%, observed_coverage_90≈96.1% —
#: each at or above its own 50%/90% target — and drift_state=stable, so this
#: cell is published at full confidence). The per-turn-bucket bands below
#: are the final, full-corpus fit over that same 43-session cohort, produced
#: with this module's own ``_TimeStepBands``/``_pool_near_table_time``
#: primitives — no band value here is invented or interpolated by hand.
_PUBLISHED_TIME_PRIOR: dict[tuple[str, str, str], PublishedTimeCellEvidence] = {
    ("claude-sonnet-5", "unknown", "streaming"): PublishedTimeCellEvidence(
        prior_version="time-prior-2026-08-30",
        band50_y_by_k={
            1: (3.017475, 5.493954),
            2: (2.93118, 5.198149),
            3: (2.574576, 4.704534),
            4: (2.220601, 4.279322),
            5: (2.089676, 3.974796),
            6: (1.936967, 3.700948),
            7: (1.820305, 3.365316),
            8: (1.689397, 3.107356),
            9: (1.564582, 2.942803),
            10: (1.250146, 2.84512),
            11: (1.131861, 2.59791),
            12: (1.040402, 2.513488),
            13: (0.980715, 2.444689),
            14: (0.894103, 2.238339),
            15: (0.894465, 2.135476),
        },
        band90_hi_y_by_k={
            1: 5.81319,
            2: 5.561987,
            3: 4.754316,
            4: 4.597744,
            5: 4.454478,
            6: 4.238913,
            7: 4.160339,
            8: 4.100383,
            9: 3.933622,
            10: 3.838157,
            11: 3.642237,
            12: 3.594404,
            13: 3.586128,
            14: 3.541938,
            15: 3.541034,
        },
        coverage_method="walk-forward-split-conformal",
        observed_coverage_50=64.90872210953347,
        observed_coverage_90=96.14604462474645,
        history_n=43,
        drift_state=DriftState.STABLE,
        full_confidence=True,
    ),
}


@dataclass(frozen=True)
class TimeHistorySession:
    """One inactive session history's per-turn wall-clock duration sequence (ms).

    A turn's duration is ``ttfb_ms + stream_duration_ms`` — the same
    definition ``session_forecast.py`` uses to build ``elapsed_ms`` for the
    active session, so the corpus and the live signal are commensurable.
    Turns with neither figure populated (non-streaming) contribute no
    duration sample; a session with no populated turns never enters the
    corpus at all (see ``_parse_time_corpus``).
    """

    model: str
    effort: str
    stream_mode: TimeForecastStreamMode
    ended_at: datetime
    turn_durations_ms: tuple[float, ...]

    @property
    def total(self) -> float:
        return float(sum(self.turn_durations_ms))

    @property
    def turns(self) -> int:
        return len(self.turn_durations_ms)


@dataclass(frozen=True)
class CellReadinessTime:
    sessions: int
    scored_points: int
    observed_coverage_50: float | None
    observed_coverage_90: float | None
    drift_state: DriftState


def _time_cell_key(
    model: str, effort: str, stream_mode: TimeForecastStreamMode
) -> tuple[str, str, str]:
    return (model.strip() or "unknown", effort.strip() or "unknown", stream_mode.value)


def _map_stream_mode(raw: object) -> TimeForecastStreamMode:
    """Translate the monitor.db ledger's ``stream_mode`` string.

    Independently duplicated from ``session_forecast.py``'s helper of the
    same purpose (mirroring this codebase's existing precedent of small,
    per-module timestamp/lookup helpers — e.g. ``_parse_timestamp`` vs
    ``_parse_ts_utc`` — rather than a reverse-direction cross-import).
    """
    if isinstance(raw, str) and raw == "sse":
        return TimeForecastStreamMode.STREAMING
    if isinstance(raw, str) and raw == "json":
        return TimeForecastStreamMode.NON_STREAMING
    return TimeForecastStreamMode.UNKNOWN


def _parse_ts_utc(value: object) -> datetime | None:
    """Ledger timestamp -> UTC (naive local wall-clock strings get the host zone).

    Independently duplicated from ``session_forecast_calibration.py``'s
    helper of the same name — see ``_map_stream_mode`` above for why.
    """
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


def _row_duration_ms(row: sqlite3.Row) -> float | None:
    """A single row's timing-facts duration, or ``None`` if not populated.

    Strictly ``ttfb_ms + stream_duration_ms`` (the closed timing-facts input
    list) — never a ``timestamp``/``started_at`` wall-clock delta, which
    would be a proxy-invented clock heuristic the activation gate forbids.
    """
    total = 0.0
    saw_any = False
    for key in ("ttfb_ms", "stream_duration_ms"):
        value = row[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            total += float(value)
            saw_any = True
    return total if saw_any else None


def _connect_ro(path: str) -> sqlite3.Connection:
    """Read-only open: an absent file errors instead of being created."""
    from urllib.parse import quote

    return sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)


def _parse_time_corpus(
    conn: sqlite3.Connection, *, exclude_session: str = ""
) -> list[TimeHistorySession]:
    available = {str(row[1]) for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
    query = _CORPUS_QUERY_BY_EFFORT_PROVENANCE[
        (
            "reasoning_effort_source" in available,
            "reasoning_effort_raw" in available,
        )
    ]
    rows = conn.execute(query).fetchall()
    excluded = exclude_session.strip()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        sid = str(row["session_id"]).strip()
        if sid and sid != excluded:
            grouped.setdefault(sid, []).append(row)
    sessions: list[TimeHistorySession] = []
    for sid, srows in grouped.items():
        last_ts = _parse_ts_utc(srows[-1]["timestamp"])
        if last_ts is None:
            continue
        # Mixed-mode sessions are excluded, not blended: a session whose rows
        # map to more than one distinct stream_mode had both streaming and
        # non-streaming turns, and classifying it by its last row alone would
        # silently pool it into one cell while its earlier turns' timing
        # shape belonged to the other — the exact blending the cell
        # definition forbids. Skip the whole session rather than fabricate a
        # single label for it.
        if len({_map_stream_mode(r["stream_mode"]) for r in srows}) > 1:
            continue
        models = {str(row["model"] or "unknown").strip() or "unknown" for row in srows}
        effort_observations = [
            _reasoning_effort_cell(
                row["reasoning_effort"],
                row["reasoning_effort_raw"],
                row["reasoning_effort_source"],
            )
            for row in srows
        ]
        efforts = {label for label, _unsupported in effort_observations}
        if (
            len(models) != 1
            or len(efforts) != 1
            or any(unsupported for _label, unsupported in effort_observations)
        ):
            # Preserve the stable session identity in the ledger, but do not
            # assign mixed timing observations to the final row's cell.
            continue
        durations = tuple(
            d for d in (_row_duration_ms(r) for r in srows) if d is not None and d > 0
        )
        if len(durations) < MIN_TURNS:
            continue
        stream_mode = _map_stream_mode(srows[-1]["stream_mode"])
        model = next(iter(models))
        effort = next(iter(efforts))
        sessions.append(
            TimeHistorySession(
                model=model,
                effort=effort,
                stream_mode=stream_mode,
                ended_at=last_ts,
                turn_durations_ms=durations,
            )
        )
    sessions.sort(key=lambda s: s.ended_at)
    return sessions[-(MAX_SESSIONS * 3) :]


def read_time_history(
    monitor_db_path: str | None,
    *,
    now: datetime,
    exclude_session: str = "",
) -> list[TimeHistorySession]:
    """Inactive-session duration corpus from the same ledger table the token
    forecast's calibration reader reads.

    Read-only and deterministic given the database contents and ``now``: a
    session enters the corpus when its last completed row has been inactive
    for six hours. This is a session-inactivity boundary, not verified task
    completion. The active session is excluded because its observed duration
    can still grow. Any read failure (missing file, missing table, corrupt
    schema) fails open to an empty corpus — the caller degrades honestly rather
    than raising.

    This is the offline evidence-publishing job's measurement tool, not
    something the live forecast path calls — see the module docstring.
    """
    if not monitor_db_path:
        return []
    try:
        conn = _connect_ro(monitor_db_path)
        conn.row_factory = sqlite3.Row
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
            ).fetchone()
            if table is None:
                return []
            sessions = _parse_time_corpus(conn, exclude_session=exclude_session)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("time-forecast calibration history read failed: %s", exc)
        return []
    horizon = now - timedelta(seconds=COMPLETION_IDLE_SECONDS)
    finished = [s for s in sessions if s.ended_at <= horizon]
    return finished[-MAX_SESSIONS:]


def _session_kys(s: TimeHistorySession) -> tuple[tuple[int, float], ...]:
    """One session's (turn-index, y) samples; y = log remaining duration multiplier."""
    out: list[tuple[int, float]] = []
    total = s.total
    spent = 0.0
    for k, duration in enumerate(s.turn_durations_ms, start=1):
        spent += duration
        if k >= s.turns:
            break  # at the final turn nothing remains — degenerate sample
        if k > KMAX or spent <= 0:
            continue
        out.append((k, math.log(max(total / spent, 1.0))))
    return tuple(out)


def _samples(sessions: Sequence[TimeHistorySession]) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for s in sessions:
        out.extend(_session_kys(s))
    return out


def _near(samples: Sequence[tuple[int, float]], k: int) -> list[float]:
    kk = min(k, KMAX)
    lo, hi = kk - K_WINDOW, kk + K_WINDOW
    return [y for sk, y in samples if lo <= sk <= hi]


def _pool_near_table_time(pool_sessions: Sequence[TimeHistorySession]) -> list[list[float]]:
    """Per-turn-index pool sample lists — see the token engine's analog for
    why order matters here (``_pooled`` borrows an order-sensitive prefix)."""
    near: list[list[float]] = [[] for _ in range(KMAX + 1)]
    for s in pool_sessions:
        for sk, y in _session_kys(s):
            bucket = min(sk, KMAX)
            for k in range(max(1, bucket - K_WINDOW), min(KMAX, bucket + K_WINDOW) + 1):
                near[k].append(y)
    return near


class _TimeStepBands:
    """Per-walk-forward-step band table over precomputed prefix samples.

    Duration-domain analog of ``session_forecast_calibration._StepBands`` —
    see that class's docstring for why this restructuring keeps the replay
    linear in samples.
    """

    def __init__(
        self,
        prefix: Sequence[TimeHistorySession],
        pool_near_by_k: Sequence[list[float]],
    ) -> None:
        train_s, calib_s = _split(list(prefix))
        self._train_by_k: list[list[float]] = [[] for _ in range(KMAX + 1)]
        self._calib_by_k: list[list[float]] = [[] for _ in range(KMAX + 1)]
        for s in train_s:
            for sk, y in _session_kys(s):
                self._train_by_k[min(sk, KMAX)].append(y)
        for s in calib_s:
            for sk, y in _session_kys(s):
                self._calib_by_k[min(sk, KMAX)].append(y)
        self._pool_near_by_k = pool_near_by_k
        self._memo: dict[tuple[int, float, bool], tuple[float, float] | None] = {}

    def _near(self, by_k: Sequence[list[float]], k: int) -> list[float]:
        kk = min(k, KMAX)
        out: list[float] = []
        for b in range(max(1, kk - K_WINDOW), min(KMAX, kk + K_WINDOW) + 1):
            out.extend(by_k[b])
        return out

    def band(self, k: int, target: float, *, one_sided: bool = False) -> tuple[float, float] | None:
        key = (min(k, KMAX), target, one_sided)
        if key not in self._memo:
            cell_train = self._near(self._train_by_k, k)
            cell_calib = self._near(self._calib_by_k, k)
            base = _pooled(cell_train, self._pool_near_by_k[min(k, KMAX)], [])
            if len(base) < 6:
                self._memo[key] = None
            else:
                band = _conformal_band(base, cell_calib, target, one_sided=one_sided)
                self._memo[key] = (band.lo_y, band.hi_y)
        return self._memo[key]


@dataclass(frozen=True)
class _ReplayMeasurement:
    cov50: float | None
    pts50: int
    cov90: float | None
    pts90: int
    cov50_tail: float | None
    pts50_tail: int


def _replay_time(
    cell_sessions: Sequence[TimeHistorySession],
    pool_sessions: Sequence[TimeHistorySession],
) -> _ReplayMeasurement:
    """One-pass walk-forward replay measuring all three coverage figures.

    Fit strictly on the past, score strictly on the future — identical
    discipline to the token engine's ``_replay``, adapted to durations.
    """
    pool_near = _pool_near_table_time(pool_sessions)
    first_tail = max(0, len(cell_sessions) - RECENT_WINDOW)
    counters = {"50": [0, 0], "90": [0, 0], "tail": [0, 0]}
    for i in range(max(MIN_CELL_SESSIONS // 2, 6), len(cell_sessions), WF_BLOCK):
        step = _TimeStepBands(cell_sessions[:i], pool_near)
        for j, s in enumerate(cell_sessions[i : i + WF_BLOCK], start=i):
            total = s.total
            spent = 0.0
            for k, duration in enumerate(s.turn_durations_ms, start=1):
                spent += duration
                if k >= s.turns or k > KMAX or spent <= 0:
                    continue
                b50 = step.band(k, TARGET_50)
                if b50 is not None:
                    lo_y, hi_y = b50
                    counters["50"][1] += 1
                    if spent * math.exp(lo_y) <= total <= spent * math.exp(hi_y):
                        counters["50"][0] += 1
                    if j >= first_tail:
                        counters["tail"][1] += 1
                        if spent * math.exp(lo_y) <= total <= spent * math.exp(hi_y):
                            counters["tail"][0] += 1
                b90 = step.band(k, TARGET_90, one_sided=True)
                if b90 is not None:
                    _, hi_y90 = b90
                    counters["90"][1] += 1
                    if total <= spent * math.exp(hi_y90):
                        counters["90"][0] += 1

    def pct(name: str) -> tuple[float | None, int]:
        inside, scored = counters[name]
        return (None, 0) if scored == 0 else (100.0 * inside / scored, scored)

    c50, p50 = pct("50")
    c90, p90 = pct("90")
    ct, pt = pct("tail")
    return _ReplayMeasurement(c50, p50, c90, p90, ct, pt)


def walk_forward_coverage_time(
    cell_sessions: Sequence[TimeHistorySession],
    pool_sessions: Sequence[TimeHistorySession],
    target: float,
    *,
    one_sided: bool = False,
    score_tail: int | None = None,
) -> tuple[float | None, int]:
    """Measured coverage of the deployed procedure (see :func:`_replay_time`).

    Same validation discipline as the token engine's ``walk_forward_coverage``
    — ``target``/``score_tail`` are checked, not decorative.
    """
    measurement = _replay_time(cell_sessions, pool_sessions)
    if score_tail is not None:
        if score_tail != RECENT_WINDOW:
            raise ValueError(
                f"score_tail must be RECENT_WINDOW ({RECENT_WINDOW}) or None, got {score_tail!r}"
            )
        if target != TARGET_50:
            raise ValueError(f"score_tail measures TARGET_50 coverage, got target={target!r}")
        return measurement.cov50_tail, measurement.pts50_tail
    if one_sided:
        if target != TARGET_90:
            raise ValueError(f"one_sided measures TARGET_90 coverage, got target={target!r}")
        return measurement.cov90, measurement.pts90
    if target != TARGET_50:
        raise ValueError(f"the central-50% path measures TARGET_50 coverage, got target={target!r}")
    return measurement.cov50, measurement.pts50


def cell_readiness_time(
    cell_sessions: Sequence[TimeHistorySession],
    pool_sessions: Sequence[TimeHistorySession],
) -> CellReadinessTime:
    """Trust assessment for a cell: measured coverage and drift state.

    This is the offline evidence-publishing job's tool for deciding what to
    publish — it is not called by the live forecast path (see the module
    docstring).
    """
    n = len(cell_sessions)
    if n < MIN_CELL_SESSIONS:
        return CellReadinessTime(n, 0, None, None, DriftState.UNKNOWN)
    m = _replay_time(cell_sessions, pool_sessions)
    cov50, pts50 = m.cov50, m.pts50
    drift = DriftState.UNKNOWN
    if cov50 is not None and pts50 >= MIN_SCORED_POINTS:
        if n > RECENT_WINDOW:
            if m.cov50_tail is not None and m.pts50_tail >= MIN_SCORED_POINTS // 2:
                shortfall = (TARGET_50 * 100.0) - m.cov50_tail
                drift = DriftState.DRIFTING if shortfall > DRIFT_TOLERANCE else DriftState.STABLE
            else:
                drift = DriftState.STABLE
        else:
            drift = DriftState.STABLE
    return CellReadinessTime(n, pts50, cov50, m.cov90, drift)


def _nearest_bucket(table: Mapping[int, object], k: int) -> int | None:
    if not table:
        return None
    kk = min(k, KMAX)
    return min(table, key=lambda p: abs(p - kk))


def _inert(status: TimeForecastStatus, *, cell: TimeForecastCell) -> TimeForecast:
    return TimeForecast.inert(status, cell=cell)


def build_calibrated_time_forecast(
    *,
    monitor_db_path: str | None,
    now: datetime,
    session_id: str,
    model: str,
    effort: str,
    stream_mode: TimeForecastStreamMode,
    turn_index: int,
    elapsed_ms: float,
    published_prior: Mapping[tuple[str, str, str], PublishedTimeCellEvidence] | None = None,
) -> TimeForecast:
    """Contract-shaped calibrated time-remaining band, honest about every gap.

    Looks up ``_PUBLISHED_TIME_PRIOR`` for this cell — the ONLY thing that
    ever admits ``learning``/``available``, per the ratified gate.
    ``insufficient_data`` for every cell without a published entry in that
    table (currently every cell except ``claude-sonnet-5`` / unknown effort
    / streaming), and ``unknown`` whenever this session itself has no
    timing-fact signal.

    ``published_prior`` is a minimal, explicit dependency-injection seam:
    every production call site omits it and this function reads the real
    module-level ``_PUBLISHED_TIME_PRIOR`` table, unchanged. Tests pass an
    explicit table instead of reaching into the module's internals via
    ``monkeypatch.setattr`` (the #633/#634 real-path testing standard) — the
    seam exists ONLY so the evidence lookup can be exercised with real,
    caller-supplied data rather than patched module state.

    Never raises: any lookup/conversion failure degrades to ``unknown`` — a
    read/derivation failure degrades to unknown, never a stale value, never a
    silently-omitted field — rather than propagating.
    """
    cell = TimeForecastCell(
        model=model or "unknown", effort=effort or "unknown", stream_mode=stream_mode
    )
    if elapsed_ms <= 0 or turn_index < 1:
        return _inert(TimeForecastStatus.UNKNOWN, cell=cell)

    evidence_table = _PUBLISHED_TIME_PRIOR if published_prior is None else published_prior
    key = _time_cell_key(model, effort, stream_mode)
    evidence = evidence_table.get(key)
    if evidence is None:
        return _inert(TimeForecastStatus.INSUFFICIENT_DATA, cell=cell)

    try:
        b50_key = _nearest_bucket(evidence.band50_y_by_k, turn_index)
        b90_key = _nearest_bucket(evidence.band90_hi_y_by_k, turn_index)
        if b50_key is None or b90_key is None:
            return _inert(TimeForecastStatus.INSUFFICIENT_DATA, cell=cell)
        lo_y, hi_y = evidence.band50_y_by_k[b50_key]
        ceil_y = evidence.band90_hi_y_by_k[b90_key]

        lo_rem = max(0.0, elapsed_ms * (math.exp(lo_y) - 1.0))
        hi_rem = max(lo_rem, elapsed_ms * (math.exp(hi_y) - 1.0))
        ceil_rem = max(hi_rem, elapsed_ms * (math.exp(ceil_y) - 1.0))
        source = (
            f"walk-forward split-conformal empirical quantiles "
            f"({evidence.history_n} sessions, {evidence.prior_version})"
        )

        status = (
            TimeForecastStatus.AVAILABLE
            if evidence.full_confidence
            else TimeForecastStatus.LEARNING
        )
        gate = TimeForecastGate(
            sedr_014_landed=True,
            calibration_evidence_published=True,
            inputs_verified_timing_facts_only=True,
        )
        coverage = Coverage(
            method=evidence.coverage_method,
            observed=round(min(evidence.observed_coverage_50, 100.0) / 100.0, 4),
            history_n=evidence.history_n,
            drift_state=evidence.drift_state,
        )
        return TimeForecast(
            status=status,
            basis=BASIS,
            remaining_time_likely_50_ms=IntervalEstimate(
                state=ValueState.ESTIMATED,
                low=round(lo_rem),
                high=round(hi_rem),
                source=source,
                unit="ms",
            ),
            remaining_time_ceiling_90_ms=NumericValue.estimated(
                round(ceil_rem), source=source, unit="ms"
            ),
            coverage=coverage,
            gate=gate,
            cell=cell,
        )
    except Exception:
        logger.exception("calibrated time-forecast band construction failed; degrading to unknown")
        return _inert(TimeForecastStatus.UNKNOWN, cell=cell)


__all__ = [
    "BASIS",
    "COMPLETION_IDLE_SECONDS",
    "KMAX",
    "MIN_CELL_SESSIONS",
    "MIN_SCORED_POINTS",
    "CellReadinessTime",
    "PublishedTimeCellEvidence",
    "TimeHistorySession",
    "build_calibrated_time_forecast",
    "cell_readiness_time",
    "read_time_history",
    "walk_forward_coverage_time",
]
