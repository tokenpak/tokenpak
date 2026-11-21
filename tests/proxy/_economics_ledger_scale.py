"""Session economics against a production-size ledger, measured per call.

Builds a deterministic ``monitor.db`` ledger of completed Claude Code-shaped
sessions (150,000 rows by default, about 30 days of history), then runs a
child process that plays a status line: a few live sessions keep writing
rows, and after every write the status line asks for session economics for
the session that just wrote. The first call runs before any write (cold);
each of the next ``calls`` calls follows a ledger write, which is the case
that used to defeat the calibration corpus cache.

The child calls ``forecast_endpoint._build_session_economics_response`` —
the function behind ``POST /v1/messages/session-economics`` — with a frozen
evaluation time, so a run is deterministic. Per call it records:

- wall and CPU time;
- with ``trace=True``, the ``tracemalloc`` peak above the memory traced at
  the start of the call (the call's transient peak);
- ``VmRSS`` of the child, read from ``/proc/self/status``;
- a SHA-256 of the economics JSON and the forecast status, so two source
  trees can be compared call by call.

No network, no provider, no proxy server: the child imports the economics
builder directly, with ``HOME`` and ``TOKENPAK_HOME`` pointed at a throwaway
directory. ``source`` selects the tree the child imports (for example a
``git archive`` export of a base commit), so the same ledger and the same
write plan measure both sides.

Used by ``tests/proxy/test_session_economics_ledger_scale.py``;
``python -m tests.proxy._economics_ledger_scale --source <tree>`` runs it by
hand and prints the JSON summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Evaluation epoch. Ledger timestamps are naive wall-clock strings, as the
#: live writer stamps them; the child runs with ``TZ=UTC``.
T0 = datetime(2026, 10, 1, 12, 0, 0)
HISTORY_DAYS = 30
LIVE_SESSIONS = 4
#: A live session ends and a new one starts every this many steps.
LIVE_ROTATE_EVERY = 60
STEP_SECONDS = 15

_MODELS = (("claude-sonnet-4-5", 0.55), ("claude-opus-4-1", 0.30), ("claude-haiku-4-5", 0.15))
#: (reasoning_effort, reasoning_effort_raw, reasoning_effort_source), weight.
_EFFORTS = (
    (("", "", ""), 0.45),
    (("high", "high", "request_body"), 0.25),
    (("medium", "medium", "request_body"), 0.15),
    (("", "xhigh", "request_body_unrecognized"), 0.10),
    (("", "max", "request_body_unrecognized"), 0.05),
)
_INSERT_COLUMNS = (
    "timestamp",
    "model",
    "request_type",
    "status_code",
    "endpoint",
    "session_id",
    "reasoning_effort",
    "reasoning_effort_raw",
    "reasoning_effort_source",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cache_read_tokens",
    "provider_cache_creation_tokens",
    "latency_ms",
    "estimated_cost",
)
_INSERT = (
    f"INSERT INTO requests ({', '.join(_INSERT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _INSERT_COLUMNS)})"
)


@dataclass
class ScaleConfig:
    source: str = str(REPO_ROOT)
    rows: int = 150_000
    calls: int = 200
    trace: bool = False
    seed: int = 20261020
    #: Reuse a ledger built earlier (copied, never written in place).
    ledger: str = ""


@dataclass
class CallSample:
    index: int
    session_id: str
    wall_ms: float
    cpu_ms: float
    peak_mb: float | None
    rss_mb: float
    sha256: str
    forecast: str


@dataclass
class ScaleResult:
    source: str
    tokenpak_file: str
    rows: int
    trace: bool
    samples: list[CallSample] = field(default_factory=list)

    def warm(self) -> list[CallSample]:
        return self.samples[1:]

    def summary(self) -> dict[str, object]:
        warm = self.warm()
        first = self.samples[0]
        peaks = [s.peak_mb for s in warm if s.peak_mb is not None]
        forecasts: dict[str, int] = {}
        for s in self.samples:
            forecasts[s.forecast] = forecasts.get(s.forecast, 0) + 1
        return {
            "source": self.source,
            "tokenpak_file": self.tokenpak_file,
            "rows": self.rows,
            "trace": self.trace,
            "calls_after_first": len(warm),
            "first_call_ms": round(first.wall_ms, 1),
            "first_call_peak_mb": first.peak_mb,
            "median_ms_after_first": round(statistics.median(s.wall_ms for s in warm), 1),
            "median_cpu_ms_after_first": round(statistics.median(s.cpu_ms for s in warm), 1),
            "max_ms_after_first": round(max(s.wall_ms for s in warm), 1),
            "max_peak_mb_after_first": round(max(peaks), 3) if peaks else None,
            "median_peak_mb_after_first": round(statistics.median(peaks), 3) if peaks else None,
            "rss_after_first_mb": round(first.rss_mb, 1),
            "rss_end_mb": round(warm[-1].rss_mb, 1),
            "rss_growth_mb": round(self.rss_growth_mb(), 1),
            "rss_max_mb": round(max(s.rss_mb for s in self.samples), 1),
            "forecast_status_counts": forecasts,
            "result_digest": self.result_digest(),
        }

    def rss_growth_mb(self) -> float:
        return self.samples[-1].rss_mb - self.samples[0].rss_mb

    def result_digest(self) -> str:
        """One digest over every call's economics JSON, in call order."""
        h = hashlib.sha256()
        for s in self.samples:
            h.update(s.sha256.encode())
        return h.hexdigest()


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _pick(rnd: random.Random, options: tuple[tuple[object, float], ...]) -> object:
    values = [v for v, _ in options]
    weights = [w for _, w in options]
    return rnd.choices(values, weights=weights, k=1)[0]


def _turn_row(
    rnd: random.Random,
    ts: datetime,
    session_id: str,
    model: str,
    effort: tuple[str, str, str],
    *,
    provider: bool = True,
) -> tuple[object, ...]:
    roll = rnd.random()
    status = 200 if roll < 0.955 else 429 if roll < 0.97 else 500 if roll < 0.985 else 0
    inp = rnd.randrange(800, 60_000)
    out = rnd.randrange(60, 4_000)
    cache_read = rnd.randrange(0, 180_000) if rnd.random() < 0.8 else 0
    cache_creation = rnd.randrange(0, 20_000) if rnd.random() < 0.4 else 0
    cost = (inp * 3 + out * 15 + cache_read * 0.3 + cache_creation * 3.75) / 1_000_000
    return (
        ts.isoformat(),
        model,
        "messages",
        status,
        "/v1/messages",
        session_id,
        effort[0],
        effort[1],
        effort[2],
        inp,
        out,
        cache_read,
        cache_creation,
        inp if provider else None,
        out if provider else None,
        cache_read if provider else None,
        cache_creation if provider else None,
        rnd.randrange(400, 90_000),
        round(cost, 6),
    )


def _session_rows(
    rnd: random.Random,
    session_id: str,
    start: datetime,
    turns: int,
    *,
    profile: tuple[str, tuple[str, str, str]] | None = None,
) -> list[tuple[object, ...]]:
    if profile is None:
        model = str(_pick(rnd, _MODELS))
        effort: tuple[str, str, str] = _pick(rnd, _EFFORTS)  # type: ignore[assignment]
        # A few sessions switch model part-way; they never train a cell.
        switch_at = rnd.randrange(1, turns) if turns > 2 and rnd.random() < 0.03 else None
        # Older writers recorded no provider-normalized counts.
        provider = rnd.random() < 0.9
    else:
        model, effort = profile
        switch_at, provider = None, True
    rows = []
    ts = start
    for t in range(turns):
        if switch_at is not None and t == switch_at:
            model = "claude-haiku-4-5" if model != "claude-haiku-4-5" else "claude-sonnet-4-5"
        rows.append(_turn_row(rnd, ts, session_id, model, effort, provider=provider))
        ts += timedelta(seconds=rnd.uniform(4.0, 180.0))
    return rows


def _live_session_ids() -> list[str]:
    return [f"live-{i:03d}" for i in range(LIVE_SESSIONS)]


def _live_profile(session_id: str) -> tuple[str, tuple[str, str, str]]:
    """Model and effort of a live session; every row it writes keeps them."""
    efforts = [e for e, _ in _EFFORTS[:3]]
    return "claude-sonnet-4-5", efforts[int(session_id.rsplit("-", 1)[1]) % len(efforts)]


def build_ledger(path: Path, *, rows: int, seed: int) -> int:
    """Write a ``monitor.db`` ledger with about ``rows`` completed rows."""
    from tokenpak.proxy.monitor import Monitor

    rnd = random.Random(seed)
    all_rows: list[tuple[object, ...]] = []
    n = 0
    history_start = T0 - timedelta(days=HISTORY_DAYS)
    span = (HISTORY_DAYS * 86_400) - 2 * 3_600
    # History stops short of the live sessions' writes and the evaluation time.
    cutoff = (T0 - timedelta(minutes=10)).isoformat()
    while len(all_rows) < rows:
        turns = max(1, min(600, int(rnd.lognormvariate(3.2, 0.9))))
        start = history_start + timedelta(seconds=rnd.uniform(0, span))
        session = _session_rows(rnd, f"hist-{n:05d}", start, turns)
        all_rows.extend(r for r in session if str(r[0]) < cutoff)
        n += 1
    del all_rows[rows:]
    # The live sessions are mid-conversation at T0.
    for sid in _live_session_ids():
        all_rows.extend(
            _session_rows(rnd, sid, T0 - timedelta(minutes=40), 12, profile=_live_profile(sid))
        )
    # Live rows are stamped at completion and appended in that order.
    all_rows.sort(key=lambda r: str(r[0]))

    Monitor(str(path))
    conn = sqlite3.connect(str(path))
    try:
        conn.executemany(_INSERT, all_rows)
        conn.commit()
        count = int(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
    finally:
        conn.close()
    return count


def write_plan(seed: int, calls: int) -> list[tuple[str, list[tuple[object, ...]], datetime]]:
    """Per step: (session asked about, rows written first, evaluation time)."""
    rnd = random.Random(seed + 1)
    live = _live_session_ids()
    plan: list[tuple[str, list[tuple[object, ...]], datetime]] = [
        (live[0], [], T0 + timedelta(seconds=1))
    ]
    fresh = 0
    for step in range(1, calls + 1):
        ts = T0 + timedelta(seconds=step * STEP_SECONDS)
        if step % LIVE_ROTATE_EVERY == 0:
            slot = (step // LIVE_ROTATE_EVERY) % LIVE_SESSIONS
            live[slot] = f"live-new-{fresh:03d}"
            fresh += 1
        sid = live[(step - 1) % LIVE_SESSIONS]
        model, effort = _live_profile(sid)
        written = [_turn_row(rnd, ts, sid, model, effort)]
        if step % 7 == 0:
            # A retried turn: two completions a moment apart.
            written.append(_turn_row(rnd, ts + timedelta(milliseconds=300), sid, model, effort))
        plan.append((sid, written, ts + timedelta(seconds=1)))
    return plan


# ---------------------------------------------------------------------------
# Child
# ---------------------------------------------------------------------------


def _rss_mb() -> float:
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def _child(args: dict[str, object]) -> dict[str, object]:
    import time
    import tracemalloc

    source = os.path.realpath(str(args["source"]))
    import tokenpak

    tokenpak_file = os.path.realpath(tokenpak.__file__)
    if not tokenpak_file.startswith(source + os.sep):
        raise SystemExit(f"imported {tokenpak_file}, expected a module under {source}")
    from tokenpak.proxy.forecast_endpoint import _build_session_economics_response

    db = str(args["db"])
    trace = bool(args["trace"])
    plan = write_plan(int(args["seed"]), int(args["calls"]))
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    if trace:
        tracemalloc.start()
    samples = []
    try:
        for index, (sid, written, now) in enumerate(plan):
            if written:
                writer.executemany(_INSERT, written)
                writer.commit()
            peak_mb = None
            if trace:
                tracemalloc.reset_peak()
                start_traced = tracemalloc.get_traced_memory()[0]
            w0, c0 = time.perf_counter(), time.process_time()
            economics = _build_session_economics_response(sid, db, now=now.astimezone())
            body = economics.to_json()
            w1, c1 = time.perf_counter(), time.process_time()
            if trace:
                peak_mb = (tracemalloc.get_traced_memory()[1] - start_traced) / (1 << 20)
            samples.append(
                {
                    "index": index,
                    "session_id": sid,
                    "wall_ms": (w1 - w0) * 1000.0,
                    "cpu_ms": (c1 - c0) * 1000.0,
                    "peak_mb": peak_mb,
                    "rss_mb": _rss_mb(),
                    "sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "forecast": economics.forecast.status.value,
                }
            )
            del economics, body
    finally:
        writer.close()
        if trace:
            tracemalloc.stop()
    return {"tokenpak_file": tokenpak_file, "samples": samples}


# ---------------------------------------------------------------------------
# Parent
# ---------------------------------------------------------------------------


def run_scale(cfg: ScaleConfig) -> ScaleResult:
    with tempfile.TemporaryDirectory(prefix="tp-econ-scale-") as tmp_name:
        tmp = Path(tmp_name)
        home = tmp / "home"
        (home / ".tokenpak").mkdir(parents=True)
        db = tmp / "monitor.db"
        if cfg.ledger:
            shutil.copyfile(cfg.ledger, db)
        else:
            build_ledger(db, rows=cfg.rows, seed=cfg.seed)
        rows = _row_count(db)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "TOKENPAK_HOME": str(home / ".tokenpak"),
            "TOKENPAK_DB": str(db),
            "PYTHONPATH": os.path.realpath(cfg.source),
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
            "LANG": "C.UTF-8",
        }
        # HOME moves to the throwaway home; keep this interpreter's user site.
        import site

        env["PYTHONUSERBASE"] = os.environ.get("PYTHONUSERBASE") or site.getuserbase()
        args = {
            "source": cfg.source,
            "db": str(db),
            "trace": cfg.trace,
            "seed": cfg.seed,
            "calls": cfg.calls,
        }
        out = tmp / "child.json"
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child", json.dumps(args), str(out)],
            cwd=str(tmp),
            env=env,
            capture_output=True,
            text=True,
            timeout=6 * 3600,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"child failed ({proc.returncode}):\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
            )
        data = json.loads(out.read_text(encoding="utf-8"))
    return ScaleResult(
        source=cfg.source,
        tokenpak_file=str(data["tokenpak_file"]),
        rows=rows,
        trace=cfg.trace,
        samples=[CallSample(**s) for s in data["samples"]],
    )


def _row_count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=str(REPO_ROOT))
    parser.add_argument("--rows", type=int, default=ScaleConfig.rows)
    parser.add_argument("--calls", type=int, default=ScaleConfig.calls)
    parser.add_argument("--seed", type=int, default=ScaleConfig.seed)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--ledger", default="", help="copy this ledger instead of building one")
    parser.add_argument("--build-ledger", default="", help="only build a ledger at this path")
    parser.add_argument("--samples", default="", help="write every call's sample here as JSON")
    ns = parser.parse_args(argv)
    if ns.build_ledger:
        print(build_ledger(Path(ns.build_ledger), rows=ns.rows, seed=ns.seed))
        return 0
    result = run_scale(
        ScaleConfig(
            source=ns.source,
            rows=ns.rows,
            calls=ns.calls,
            trace=ns.trace,
            seed=ns.seed,
            ledger=ns.ledger,
        )
    )
    if ns.samples:
        Path(ns.samples).write_text(
            json.dumps([asdict(s) for s in result.samples]), encoding="utf-8"
        )
    print(json.dumps(result.summary(), indent=1))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        # Run from the selected tree only: drop this file's directory, which
        # precedes PYTHONPATH on sys.path.
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
        child_args = json.loads(sys.argv[2])
        Path(sys.argv[3]).write_text(json.dumps(_child(child_args)), encoding="utf-8")
        sys.exit(0)
    sys.exit(main())
