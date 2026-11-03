"""The incremental calibration corpus equals the full-rescan corpus it replaced.

``session_forecast_calibration._corpus_for`` used to re-read and re-group the
whole ``requests`` ledger on every session-economics call. It now folds in
only the rows appended since its last read. ``test_leak_memory_bounds.py``
checks the incremental result against the module's own full parse, which was
rewritten in the same change. This file checks both against an oracle: the
previous implementation (a ``fetchall`` of the full ledger, grouped per
session), kept below with only its names and its entry type changed, so a
change to either new path that drifts from the old semantics fails here.

The oracle is a characterization of the old behavior, not a second
implementation to maintain: if the corpus semantics are changed on purpose,
change the oracle with them and say so in the change.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tokenpak.proxy.session_forecast_calibration as cal
from tokenpak.proxy.spend_guard.session_state import _reasoning_effort_cell

# The oracle re-reads the ledger after every append, by design.
pytestmark = pytest.mark.timeout(120)

# ---------------------------------------------------------------------------
# Oracle: the full-rescan corpus parse, as it was before the incremental one
# ---------------------------------------------------------------------------

_ORACLE_SELECT_TAIL = (
    "timestamp, "
    "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
    "provider_input_tokens, provider_output_tokens, "
    "provider_cache_read_tokens, provider_cache_creation_tokens "
    "FROM requests "
    "WHERE session_id IS NOT NULL AND TRIM(session_id) != '' "
    "AND status_code BETWEEN 200 AND 599 "
    "ORDER BY timestamp ASC, id ASC"
)
_ORACLE_QUERY: dict[tuple[bool, bool], str] = {
    (False, False): (
        "SELECT session_id, model, reasoning_effort, "
        "'' AS reasoning_effort_source, '' AS reasoning_effort_raw, " + _ORACLE_SELECT_TAIL
    ),
    (False, True): (
        "SELECT session_id, model, reasoning_effort, "
        "'' AS reasoning_effort_source, reasoning_effort_raw, " + _ORACLE_SELECT_TAIL
    ),
    (True, False): (
        "SELECT session_id, model, reasoning_effort, "
        "reasoning_effort_source, '' AS reasoning_effort_raw, " + _ORACLE_SELECT_TAIL
    ),
    (True, True): (
        "SELECT session_id, model, reasoning_effort, "
        "reasoning_effort_source, reasoning_effort_raw, " + _ORACLE_SELECT_TAIL
    ),
}


def _oracle_row_total(row: sqlite3.Row) -> float:
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


def _oracle_parse_ts_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


_Entry = tuple[str, str, str, datetime, tuple[float, ...]]


def _oracle_parse_corpus(conn: sqlite3.Connection) -> tuple[_Entry, ...]:
    available = {str(row[1]) for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
    query = _ORACLE_QUERY[
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
    entries: list[_Entry] = []
    for sid, srows in grouped.items():
        last_ts = _oracle_parse_ts_utc(srows[-1]["timestamp"])
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
            continue
        costs = tuple(c for c in (_oracle_row_total(r) for r in srows) if c > 0)
        if len(costs) < cal.MIN_TURNS:
            continue
        entries.append((sid, next(iter(models)), next(iter(efforts)), last_ts, costs))
    entries.sort(key=lambda e: e[3])
    return tuple(entries[-(cal.MAX_SESSIONS * 3) :])


def _oracle(db: Path) -> tuple[_Entry, ...]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return _oracle_parse_corpus(conn)
    finally:
        conn.close()


def _flat(corpus: tuple[cal._CorpusEntry, ...]) -> tuple[_Entry, ...]:
    return tuple(
        (e.session_id, e.session.model, e.session.effort, e.session.ended_at, e.session.turn_costs)
        for e in corpus
    )


_PARSE_CORPUS = cal._parse_corpus


def _full_parse(db: Path) -> tuple[_Entry, ...]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return _flat(tuple(_PARSE_CORPUS(conn)))
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cal, "_CORPUS_CACHE", {})
    monkeypatch.setattr(cal, "_READINESS_CACHE", {})


def _count_full_parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    real = cal._parse_corpus

    def counting(conn: sqlite3.Connection) -> tuple[cal._CorpusEntry, ...]:
        calls[0] += 1
        return real(conn)

    monkeypatch.setattr(cal, "_parse_corpus", counting)
    return calls


# ---------------------------------------------------------------------------
# Ledgers
# ---------------------------------------------------------------------------

_MODERN = (
    "session_id, model, reasoning_effort, reasoning_effort_raw, reasoning_effort_source, "
    "timestamp, status_code, input_tokens, output_tokens, cache_read_tokens, "
    "cache_creation_tokens, provider_input_tokens, provider_output_tokens, "
    "provider_cache_read_tokens, provider_cache_creation_tokens"
)
_EFFORTS = (
    ("", "", ""),
    ("high", "high", "request_body"),
    ("medium", "medium", "request_body"),
    ("", "xhigh", "request_body_unrecognized"),
    ("", "max", "request_body_unrecognized"),
    ("unknown", "", ""),
)
_BASE = datetime(2026, 9, 1, 8, 0, 0)


def _modern_ledger(path: Path) -> None:
    from tokenpak.proxy.monitor import Monitor

    Monitor(str(path))


def _append(path: Path, columns: str, rows: list[tuple[object, ...]]) -> None:
    marks = ",".join("?" for _ in columns.split(","))
    conn = sqlite3.connect(str(path))
    try:
        # Durability is not under test; an fsync per batch only slows it.
        conn.execute("PRAGMA synchronous=OFF")
        conn.executemany(f"INSERT INTO requests ({columns}) VALUES ({marks})", rows)
        conn.commit()
    finally:
        conn.close()


def _stamp(rnd: random.Random, ts: datetime, *, irregular: bool) -> str:
    """The live writer's spelling, or the others found in imported ledgers."""
    if not irregular:
        return ts.isoformat()
    roll = rnd.random()
    if roll < 0.80:
        return ts.isoformat()
    if roll < 0.90:
        return ts.replace(microsecond=0).isoformat() + "Z"
    if roll < 0.97:
        return ts.isoformat() + "+00:00"
    return ts.isoformat(sep=" ")


class _Writer:
    """Session-shaped traffic with the irregularities the corpus must absorb."""

    def __init__(self, seed: int, sessions: int) -> None:
        self.rnd = random.Random(seed)
        self.profiles = {
            f"s{i:04d}": (
                self.rnd.choice(("model-a", "model-a", "model-b")),
                self.rnd.choice(_EFFORTS),
            )
            for i in range(sessions)
        }
        self.clock = _BASE

    def rows(
        self, n: int, *, disorder: float = 0.0, irregular: bool = False
    ) -> list[tuple[object, ...]]:
        rnd = self.rnd
        out = []
        for _ in range(n):
            self.clock += timedelta(seconds=rnd.uniform(0.0, 90.0))
            ts = self.clock
            if rnd.random() < disorder:
                ts -= timedelta(minutes=rnd.uniform(1.0, 600.0))
            sid = rnd.choice(sorted(self.profiles))
            model, effort = self.profiles[sid]
            if rnd.random() < 0.02:
                model = "model-c"  # the session now spans two models
            if rnd.random() < 0.02:
                effort = rnd.choice(_EFFORTS)
            roll = rnd.random()
            if roll < 0.04:
                sid = f" {sid} "
            elif roll < 0.05:
                sid = "   "
            elif roll < 0.06:
                sid = None  # type: ignore[assignment]
            status = rnd.choice((200,) * 30 + (404, 429, 500, 199, 600, 999))
            tokens = [rnd.randrange(0, 50_000) for _ in range(4)]
            if rnd.random() < 0.05:
                tokens = [0, 0, 0, 0]
            if rnd.random() < 0.03:
                tokens[1] = -5
            provider: list[object] = list(tokens) if rnd.random() < 0.7 else [None] * 4
            if rnd.random() < 0.05:
                provider[2] = float(rnd.randrange(0, 9_000)) + 0.5
            out.append(
                (sid, model, *effort, _stamp(rnd, ts, irregular=irregular), status)
                + (*tokens, *provider)
            )
        return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_incremental_corpus_matches_the_old_full_rescan(tmp_path, monkeypatch) -> None:
    db = tmp_path / "monitor.db"
    _modern_ledger(db)
    writer = _Writer(seed=20261020, sessions=50)
    _append(db, _MODERN, writer.rows(600, irregular=True))
    full_parses = _count_full_parses(monkeypatch)
    steps = 30
    for step in range(steps):
        # Mostly in-order appends, as the live writer produces. Every tenth
        # batch carries a row stamped earlier than rows already read, and
        # another uses the spellings of imported ledgers.
        rows = writer.rows(
            writer.rnd.randrange(1, 12),
            disorder=0.3 if step % 10 == 9 else 0.0,
            irregular=step % 10 == 4,
        )
        _append(db, _MODERN, rows)
        expected = _oracle(db)
        assert _flat(cal._corpus_for(str(db))) == expected, f"diverged at step {step}"
        assert _full_parse(db) == expected, f"full parse diverged at step {step}"
    # The incremental path, not a re-parse, produced most of these results.
    assert full_parses[0] <= steps // 4, full_parses[0]


def test_window_cut_and_end_time_ties_match_the_old_full_rescan(tmp_path, monkeypatch) -> None:
    """More sessions than the window keeps, many ending at the same instant."""
    # A small window keeps the ledger small; both sides read the constant.
    monkeypatch.setattr(cal, "MAX_SESSIONS", 20)
    db = tmp_path / "monitor.db"
    _modern_ledger(db)
    rnd = random.Random(7)
    window = cal.MAX_SESSIONS * 3
    rows = []
    extra = 40
    for i in range(window + extra):
        # Groups of sessions share their last timestamp exactly.
        end = _BASE + timedelta(minutes=(i // 5))
        spelled = end.isoformat() if i % 2 else end.isoformat() + "+00:00"
        for t in range(cal.MIN_TURNS - 1 + rnd.randrange(0, 3)):
            ts = end - timedelta(seconds=30 * (cal.MIN_TURNS + 3 - t))
            rows.append((f"w{i:05d}", "model-a", "", "", "", ts.isoformat(), 200, 100, 10, 0, 0))
        rows.append((f"w{i:05d}", "model-a", "", "", "", spelled, 200, 100, 10, 0, 0))
    rows.sort(key=lambda r: str(r[5]))
    columns = (
        "session_id, model, reasoning_effort, reasoning_effort_raw, reasoning_effort_source, "
        "timestamp, status_code, input_tokens, output_tokens, cache_read_tokens, "
        "cache_creation_tokens"
    )
    _append(db, columns, rows)
    assert _flat(cal._corpus_for(str(db))) == _oracle(db)
    full_parses = _count_full_parses(monkeypatch)
    later = _BASE + timedelta(minutes=(window + extra) // 5 + 1)
    for step in range(40):
        # Old sessions resume and new ones start; both move the window.
        sid = f"w{rnd.randrange(0, window + extra + 20):05d}"
        ts = (later + timedelta(seconds=step)).isoformat()
        _append(db, columns, [(sid, "model-a", "", "", "", ts, 200, 50, 5, 0, 0)])
        assert _flat(cal._corpus_for(str(db))) == _oracle(db), f"diverged at step {step}"
    assert full_parses[0] < 40, full_parses[0]


def test_legacy_schema_and_a_schema_change_match_the_old_full_rescan(tmp_path) -> None:
    """No effort-provenance columns, odd timestamps, then an ALTER.

    NULL, numeric and unparsable timestamps sort apart from ISO text in
    SQLite (an unparsable one after every date), so a session holding one
    keeps forcing a full parse; the result must still match.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp, "
            "model TEXT, session_id TEXT, reasoning_effort TEXT, status_code INTEGER, "
            "input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
            "cache_creation_tokens INTEGER, provider_input_tokens INTEGER, "
            "provider_output_tokens INTEGER, provider_cache_read_tokens INTEGER, "
            "provider_cache_creation_tokens INTEGER)"
        )
        conn.commit()
    finally:
        conn.close()
    columns = (
        "session_id, model, reasoning_effort, timestamp, status_code, input_tokens, "
        "output_tokens, cache_read_tokens, cache_creation_tokens"
    )
    rnd = random.Random(3)
    clock = _BASE
    for step in range(30):
        batch = []
        for _ in range(rnd.randrange(5, 25)):
            clock += timedelta(seconds=rnd.uniform(1, 60))
            roll = rnd.random()
            ts: object = clock.isoformat()
            if roll < 0.03:
                ts = None
            elif roll < 0.06:
                ts = clock.timestamp()
            elif roll < 0.08:
                ts = "not-a-time"
            sid = f"l{rnd.randrange(0, 25)}"
            model = None if rnd.random() < 0.02 else "model-a"
            effort = rnd.choice(("", "high", "unknown"))
            batch.append((sid, model, effort, ts, 200, rnd.randrange(1, 900), 3, 0, 0))
        _append(db, columns, batch)
        if step == 20:
            conn = sqlite3.connect(str(db))
            try:
                conn.execute("ALTER TABLE requests ADD COLUMN reasoning_effort_raw TEXT")
                conn.commit()
            finally:
                conn.close()
        assert _flat(cal._corpus_for(str(db))) == _oracle(db), f"diverged at step {step}"


def test_session_economics_is_identical_with_the_old_corpus(tmp_path, monkeypatch) -> None:
    """End to end: the economics payload does not change with the corpus path."""
    from tokenpak.proxy.forecast_endpoint import _build_session_economics_response

    rnd = random.Random(11)
    history = []
    for i in range(70):
        start = _BASE + timedelta(hours=i)
        for t in range(rnd.randrange(4, 14)):
            ts = (start + timedelta(minutes=2 * t)).isoformat()
            inp, out = rnd.randrange(1_000, 40_000), rnd.randrange(50, 3_000)
            history.append(
                (f"h{i:03d}", "model-a", "high", "high", "request_body", ts, 200, inp, out, 0, 0)
                + (inp, out, 0, 0)
            )
    live_start = _BASE + timedelta(days=5)
    plan = []
    for step in range(24):
        sid = f"live{step % 3}"
        ts = live_start + timedelta(seconds=20 * step)
        inp, out = rnd.randrange(1_000, 40_000), rnd.randrange(50, 3_000)
        row = (sid, "model-a", "high", "high", "request_body", ts.isoformat(), 200)
        plan.append((sid, row + (inp, out, 0, 0, inp, out, 0, 0), ts + timedelta(seconds=1)))

    def run(db: Path, corpus_for) -> list[str]:  # type: ignore[no-untyped-def]
        _modern_ledger(db)
        _append(db, _MODERN, history)
        monkeypatch.setattr(cal, "_CORPUS_CACHE", {})
        monkeypatch.setattr(cal, "_READINESS_CACHE", {})
        monkeypatch.setattr(cal, "_corpus_for", corpus_for)
        out = []
        for sid, row, now in plan:
            _append(db, _MODERN, [row])
            # Ledger stamps are naive local wall-clock time, as live.
            economics = _build_session_economics_response(sid, str(db), now=now.astimezone())
            out.append(economics.to_json())
        return out

    def old_corpus_for(path: str) -> tuple[cal._CorpusEntry, ...]:
        return tuple(
            cal._CorpusEntry(
                session_id=sid,
                session=cal.HistorySession(
                    model=model, effort=effort, ended_at=ended, turn_costs=costs
                ),
            )
            for sid, model, effort, ended, costs in _oracle(Path(path))
        )

    real_corpus_for = cal._corpus_for
    old = run(tmp_path / "old.db", old_corpus_for)
    new = run(tmp_path / "new.db", real_corpus_for)
    assert new == old
    # The comparison exercised calibrated forecasts, not only fallbacks.
    assert sum('"status":"available"' in body for body in new) >= len(plan) // 2
