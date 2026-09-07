# SPDX-License-Identifier: Apache-2.0
"""Calibrated remaining-token forecast: bucketed quantiles + split conformal.

Re-derivation of the archived offline research harness for production use,
with the model class deliberately simplified to dependency-free empirical
quantiles so the proxy carries no ML runtime. What is kept from the research
is the part that made its numbers honest:

- the target is the LOG REMAINING MULTIPLIER ``y = log(max(total/spent, 1))``
  of session histories inactive for at least six hours, never a point blend
  with already-spent tokens; inactivity is not verified task completion;
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
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence, TypeVar

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
from tokenpak.proxy.spend_guard.session_state import _reasoning_effort_cell

logger = logging.getLogger(__name__)

#: Version label for the built-in cold-start prior. The prior's constants are
#: conservative shape parameters, not published claims; the version string is
#: surfaced in the learning reason so a borrowed prior is always attributable.
PRIOR_VERSION = "tokenpak-builtin-prior/1"

#: Trust floor: a cell forecasts only after this many of its own inactive
#: session histories have been measured by the walk-forward replay.
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
        "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
        "provider_input_tokens, provider_output_tokens, "
        "provider_cache_read_tokens, provider_cache_creation_tokens "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
    (False, True): (
        "SELECT session_id, model, reasoning_effort, "
        "'' AS reasoning_effort_source, reasoning_effort_raw, timestamp, "
        "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
        "provider_input_tokens, provider_output_tokens, "
        "provider_cache_read_tokens, provider_cache_creation_tokens "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
    (True, False): (
        "SELECT session_id, model, reasoning_effort, "
        "reasoning_effort_source, '' AS reasoning_effort_raw, timestamp, "
        "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
        "provider_input_tokens, provider_output_tokens, "
        "provider_cache_read_tokens, provider_cache_creation_tokens "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
    (True, True): (
        "SELECT session_id, model, reasoning_effort, "
        "reasoning_effort_source, reasoning_effort_raw, timestamp, "
        "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
        "provider_input_tokens, provider_output_tokens, "
        "provider_cache_read_tokens, provider_cache_creation_tokens "
        "FROM requests "
        "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
        "AND status_code BETWEEN 200 AND 599 "
        "ORDER BY timestamp ASC, id ASC"
    ),
}

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
    """One inactive session history's per-turn total-token sequence."""

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


@dataclass(frozen=True)
class _CorpusEntry:
    """One parsed session (finished or not) keyed for the fingerprint cache."""

    session_id: str
    session: HistorySession


_CACHE_LOCK = threading.Lock()
#: db_path -> (fingerprint, corpus entries). Bounded small; process-local.
_CORPUS_CACHE: dict[str, tuple[tuple[int, int], tuple[_CorpusEntry, ...]]] = {}
#: One participant's (model, effort, ended_at, turns, total, turn_costs) tuple.
_ParticipantSignature = tuple[tuple[str, str, str, int, float, tuple[float, ...]], ...]
#: (db_path, cell key, cell signature, pool signature) -> readiness.
_ReadinessCacheKey = tuple[
    str | None, tuple[str, str], _ParticipantSignature, _ParticipantSignature
]
_READINESS_CACHE: dict[_ReadinessCacheKey, CellReadiness] = {}
_CORPUS_CACHE_MAX = 4
_READINESS_CACHE_MAX = 32


def _ledger_fingerprint(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute("SELECT COALESCE(MAX(id), 0), COUNT(*) FROM requests").fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _connect_ro(path: str) -> sqlite3.Connection:
    """Read-only open: an absent file errors instead of being created."""
    from urllib.parse import quote

    return sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)


def _parse_corpus(conn: sqlite3.Connection) -> tuple[_CorpusEntry, ...]:
    available = {str(row[1]) for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
    query = _CORPUS_QUERY_BY_EFFORT_PROVENANCE[
        (
            "reasoning_effort_source" in available,
            "reasoning_effort_raw" in available,
        )
    ]
    rows = conn.execute(query).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        sid = str(row["session_id"]).strip()
        if sid:
            grouped.setdefault(sid, []).append(row)
    entries: list[_CorpusEntry] = []
    for sid, srows in grouped.items():
        last_ts = _parse_ts_utc(srows[-1]["timestamp"])
        if last_ts is None:
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
            # A stable session identity can legitimately cross model or
            # effort boundaries. Its spend remains observable elsewhere, but
            # its total cannot train either homogeneous calibration cell.
            continue
        costs = tuple(c for c in (_row_total(r) for r in srows) if c > 0)
        if len(costs) < MIN_TURNS:
            continue
        model = next(iter(models))
        effort = next(iter(efforts))
        entries.append(
            _CorpusEntry(
                session_id=sid,
                session=HistorySession(
                    model=model, effort=effort, ended_at=last_ts, turn_costs=costs
                ),
            )
        )
    entries.sort(key=lambda e: e.session.ended_at)
    # Bound memory ahead of the per-call finished filter; newest win. This is
    # a semantics choice, not a cache artifact: cached and fresh parses share
    # this exact truncation, so they can never diverge from each other.
    return tuple(entries[-(MAX_SESSIONS * 3) :])


def _corpus_for(monitor_db_path: str) -> tuple[_CorpusEntry, ...]:
    """Fingerprint-cached parsed corpus; identical to an uncached parse.

    The fingerprint (max id, row count) changes on any ledger append, so a
    hit can only serve data equal to what a fresh parse would produce —
    determinism and the non-self-metering claims are unaffected.
    """
    try:
        conn = _connect_ro(monitor_db_path)
        conn.row_factory = sqlite3.Row
        try:
            # The fingerprint read and the corpus parse must observe ONE
            # consistent snapshot: an explicit transaction holds a read
            # lock across both statements, so a concurrent writer cannot
            # commit in the gap between them and leave the cached
            # fingerprint describing a row count the cached corpus
            # disagrees with.
            conn.execute("BEGIN")
            try:
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
                ).fetchone()
                if table is None:
                    return ()
                fingerprint = _ledger_fingerprint(conn)
                with _CACHE_LOCK:
                    cached = _CORPUS_CACHE.get(monitor_db_path)
                    if cached is not None and cached[0] == fingerprint:
                        return cached[1]
                corpus = _parse_corpus(conn)
            finally:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("calibration history read failed: %s", exc)
        return ()
    with _CACHE_LOCK:
        if len(_CORPUS_CACHE) >= _CORPUS_CACHE_MAX and monitor_db_path not in _CORPUS_CACHE:
            _CORPUS_CACHE.pop(next(iter(_CORPUS_CACHE)))
        _CORPUS_CACHE[monitor_db_path] = (fingerprint, corpus)
    return corpus


def read_history(
    monitor_db_path: str | None,
    *,
    now: datetime,
    exclude_session: str = "",
) -> list[HistorySession]:
    """Inactive-session corpus from the same ledger table the engine reads.

    Read-only and deterministic given the database contents and ``now``:
    a session enters the corpus when its last completed row has been inactive
    for six hours. This is a session-inactivity boundary, not verified task
    completion. The active session is excluded because its observed total can
    still grow.
    """
    if not monitor_db_path:
        return []
    horizon = now - timedelta(seconds=COMPLETION_IDLE_SECONDS)
    sessions = [
        e.session
        for e in _corpus_for(monitor_db_path)
        if e.session_id != exclude_session and e.session.ended_at <= horizon
    ]
    return sessions[-MAX_SESSIONS:]


def _cell_key(model: str, effort: str) -> tuple[str, str]:
    return (model.strip() or "unknown", effort.strip() or "unknown")


def _session_kys(s: HistorySession) -> tuple[tuple[int, float], ...]:
    """One session's (turn-index, y) samples; y = log remaining multiplier."""
    out: list[tuple[int, float]] = []
    total = s.total
    spent = 0.0
    for k, cost in enumerate(s.turn_costs, start=1):
        spent += cost
        if k >= s.turns:
            break  # at the final turn nothing remains — degenerate sample
        if k > KMAX or spent <= 0:
            continue
        out.append((k, math.log(max(total / spent, 1.0))))
    return tuple(out)


def _samples(sessions: Sequence[HistorySession]) -> list[tuple[int, float]]:
    """(turn-index, y) samples across sessions (order-preserving)."""
    out: list[tuple[int, float]] = []
    for s in sessions:
        out.extend(_session_kys(s))
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


_HistoryT = TypeVar("_HistoryT")


def _split(sessions: Sequence[_HistoryT]) -> tuple[list[_HistoryT], list[_HistoryT]]:
    """Chronological train/calibration split (most recent quarter calibrates).

    Generic over the session record type: the split is pure sequence slicing
    with no dependency on ``HistorySession``'s fields, so both the token
    forecast and the duration forecast (``time_forecast_calibration``) share
    this exact implementation on their own record types.
    """
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


class _StepBands:
    """Per-walk-forward-step band table over precomputed prefix samples.

    Assembles the train/calibration pools for a step ONCE (per turn index,
    from per-session sample tuples) and answers band queries from them —
    identical mathematics to :func:`_band_for`, restructured so the replay
    is linear in samples instead of rescanning the prefix for every scored
    point. This is what keeps the measurement affordable at the corpus cap.
    """

    def __init__(
        self,
        prefix: Sequence[HistorySession],
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
        self._memo: dict[tuple[int, float, bool], _Band | None] = {}

    def _near(self, by_k: Sequence[list[float]], k: int) -> list[float]:
        kk = min(k, KMAX)
        out: list[float] = []
        for b in range(max(1, kk - K_WINDOW), min(KMAX, kk + K_WINDOW) + 1):
            out.extend(by_k[b])
        return out

    def band(self, k: int, target: float, *, one_sided: bool = False) -> _Band | None:
        key = (min(k, KMAX), target, one_sided)
        if key not in self._memo:
            cell_train = self._near(self._train_by_k, k)
            cell_calib = self._near(self._calib_by_k, k)
            base = _pooled(cell_train, self._pool_near_by_k[min(k, KMAX)], _prior_near(k))
            self._memo[key] = (
                None
                if len(base) < 6
                else _conformal_band(base, cell_calib, target, one_sided=one_sided)
            )
        return self._memo[key]


@dataclass(frozen=True)
class _ReplayMeasurement:
    cov50: float | None
    pts50: int
    cov90: float | None
    pts90: int
    cov50_tail: float | None
    pts50_tail: int


def _pool_near_table(pool_sessions: Sequence[HistorySession]) -> list[list[float]]:
    """Per-turn-index pool sample lists in DEPLOYED (session-major) order.

    Order matters here and nowhere else in the replay: ``_pooled`` borrows an
    order-sensitive prefix of the pool list, so the replay must assemble pool
    samples in exactly the order the live ``_band_for`` path produces —
    session-major, turn-order within a session — or it silently measures a
    different estimator than the one deployed. (Train/calibration lists only
    feed order-insensitive quantiles.) The equivalence regression test pins
    band-level equality against the live path on a pooled corpus.
    """
    near: list[list[float]] = [[] for _ in range(KMAX + 1)]
    for s in pool_sessions:
        for sk, y in _session_kys(s):
            bucket = min(sk, KMAX)
            for k in range(max(1, bucket - K_WINDOW), min(KMAX, bucket + K_WINDOW) + 1):
                near[k].append(y)
    return near


def _replay(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
) -> _ReplayMeasurement:
    """One-pass walk-forward replay measuring all three coverage figures.

    Fit strictly on the past, score strictly on the future; the tail figure
    restricts SCORING to the most recent ``RECENT_WINDOW`` sessions while
    still fitting on all prior history — the drift instrument.
    """
    pool_near = _pool_near_table(pool_sessions)
    first_tail = max(0, len(cell_sessions) - RECENT_WINDOW)
    counters = {"50": [0, 0], "90": [0, 0], "tail": [0, 0]}
    for i in range(max(MIN_CELL_SESSIONS // 2, 6), len(cell_sessions), WF_BLOCK):
        step = _StepBands(cell_sessions[:i], pool_near)
        for j, s in enumerate(cell_sessions[i : i + WF_BLOCK], start=i):
            total = s.total
            spent = 0.0
            for k, cost in enumerate(s.turn_costs, start=1):
                spent += cost
                if k >= s.turns or k > KMAX or spent <= 0:
                    continue
                b50 = step.band(k, TARGET_50)
                if b50 is not None:
                    counters["50"][1] += 1
                    if spent * math.exp(b50.lo_y) <= total <= spent * math.exp(b50.hi_y):
                        counters["50"][0] += 1
                    if j >= first_tail:
                        counters["tail"][1] += 1
                        if spent * math.exp(b50.lo_y) <= total <= spent * math.exp(b50.hi_y):
                            counters["tail"][0] += 1
                b90 = step.band(k, TARGET_90, one_sided=True)
                if b90 is not None:
                    counters["90"][1] += 1
                    if total <= spent * math.exp(b90.hi_y):
                        counters["90"][0] += 1

    def pct(name: str) -> tuple[float | None, int]:
        inside, scored = counters[name]
        return (None, 0) if scored == 0 else (100.0 * inside / scored, scored)

    c50, p50 = pct("50")
    c90, p90 = pct("90")
    ct, pt = pct("tail")
    return _ReplayMeasurement(c50, p50, c90, p90, ct, pt)


def walk_forward_coverage(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
    target: float,
    *,
    one_sided: bool = False,
    score_tail: int | None = None,
) -> tuple[float | None, int]:
    """Measured coverage of the deployed procedure (see :func:`_replay`).

    ``target`` names the coverage level actually being measured — it is
    validated, not decorative: the tail-scored and central-50% paths only
    ever measure ``TARGET_50``, and the one-sided path only ever measures
    ``TARGET_90``. ``score_tail``, when given, must be exactly
    ``RECENT_WINDOW`` — the only tail length ``_replay`` computes — never
    silently coerced from an arbitrary int.
    """
    measurement = _replay(cell_sessions, pool_sessions)
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


def cell_readiness(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
) -> CellReadiness:
    """Trust assessment for a cell: measured coverage and drift state."""
    n = len(cell_sessions)
    if n < MIN_CELL_SESSIONS:
        return CellReadiness(n, 0, None, None, DriftState.UNKNOWN)
    m = _replay(cell_sessions, pool_sessions)
    cov50, pts50 = m.cov50, m.pts50
    drift = DriftState.UNKNOWN
    if cov50 is not None and pts50 >= MIN_SCORED_POINTS:
        if n > RECENT_WINDOW:
            # Score the full-history fit on the recent tail only: if the
            # accumulated model no longer covers recent sessions, forget.
            if m.cov50_tail is not None and m.pts50_tail >= MIN_SCORED_POINTS // 2:
                shortfall = (TARGET_50 * 100.0) - m.cov50_tail
                drift = DriftState.DRIFTING if shortfall > DRIFT_TOLERANCE else DriftState.STABLE
            else:
                drift = DriftState.STABLE
        else:
            drift = DriftState.STABLE
    return CellReadiness(n, pts50, cov50, m.cov90, drift)


def _expected_turns(
    cell_sessions: Sequence[HistorySession],
    pool_sessions: Sequence[HistorySession],
    k: int,
) -> tuple[int, int] | None:
    """Central-50% remaining-turn interval from inactive-session lengths."""
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


def _participants_signature(sessions: Sequence[HistorySession]) -> _ParticipantSignature:
    return tuple(
        (
            s.model,
            s.effort,
            s.ended_at.isoformat(),
            s.turns,
            round(s.total, 3),
            tuple(round(c, 3) for c in s.turn_costs),
        )
        for s in sessions
    )


def _cached_readiness(
    monitor_db_path: str | None,
    key: tuple[str, str],
    cell: Sequence[HistorySession],
    pool: Sequence[HistorySession],
) -> CellReadiness:
    """Memoized trust assessment — the replay is the expensive step.

    The key pins the exact participant sets, so a hit can only return what
    a fresh replay over the same inputs would compute; results stay a pure
    function of (ledger, now). Process-local and bounded.
    """
    cache_key: _ReadinessCacheKey = (
        monitor_db_path,
        key,
        _participants_signature(cell),
        _participants_signature(pool),
    )
    with _CACHE_LOCK:
        hit = _READINESS_CACHE.get(cache_key)
    if hit is not None:
        return hit
    readiness = cell_readiness(cell, pool)
    with _CACHE_LOCK:
        if len(_READINESS_CACHE) >= _READINESS_CACHE_MAX and cache_key not in _READINESS_CACHE:
            _READINESS_CACHE.pop(next(iter(_READINESS_CACHE)))
        _READINESS_CACHE[cache_key] = readiness
    return readiness


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
    if spent_tokens <= 0 or turn_index < 1:
        return _status_forecast(ForecastStatus.UNAVAILABLE, "no completed spend to forecast from")
    history = read_history(monitor_db_path, now=now, exclude_session=session_id)
    key = _cell_key(model, effort)
    cell = [s for s in history if _cell_key(s.model, s.effort) == key]
    pool = [s for s in history if _cell_key(s.model, s.effort) != key]

    readiness = _cached_readiness(monitor_db_path, key, cell, pool)
    if readiness.sessions < MIN_CELL_SESSIONS or readiness.scored_points < MIN_SCORED_POINTS:
        return _status_forecast(
            ForecastStatus.LEARNING,
            (
                f"learning: {readiness.sessions} session histories inactive for at least "
                f"six hours for this model/effort (inactivity is not verified task "
                f"completion; needs {MIN_CELL_SESSIONS}); borrowing "
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
