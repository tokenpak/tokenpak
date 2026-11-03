"""Proxy memory replay: large Claude Code-shaped bodies, measured from /proc.

Replays a deterministic stream of ``POST /v1/messages`` requests through a
real ``ProxyServer`` subprocess (``_memory_replay_launcher.py``) and a local
fake upstream. Nothing leaves loopback: the launcher's audit hook refuses any
other connection or name lookup.

The traffic models long agent conversations. Each session sends a run of
turns whose body grows from ``min_body`` to ``max_body`` by appending
messages, so turn ``t`` repeats turn ``t-1``'s prefix as a real client does.
About ``active`` sessions are open at any moment; when one ends the next one
starts, so the replay touches ``sessions`` distinct session ids. Some sessions
change their tool list part-way through (a cache invalidator). Interleaved
with the model traffic, a status line asks for session economics every
``econ_every`` requests and a dashboard tab reads its endpoints every
``dashboard_every`` requests, so the SQLite read paths those endpoints use
run too. The ledger is seeded with completed history.

Measured on the proxy process only:

- RSS (``VmRSS``), threads and descriptors from ``/proc/<pid>``; a sampler
  thread reads the descriptors every ``fd_poll_s`` seconds, so the reported
  ``monitor.db`` peak covers every moment of the run, not only checkpoints;
- traced memory from ``tracemalloc`` inside the proxy (``trace=True``), with
  the top allocation-site differences between the end of the warm-up and the
  end of the run;
- after the measured phase, RSS before and after glibc ``malloc_trim(0)``:
  memory that ``malloc_trim`` returns was free inside the allocator, not
  live.

``classify`` labels post-warm-up growth as retention (traced memory grows
with RSS), fragmentation (traced memory flat while RSS grows), mixed, or
flat.

The outbound DLP scan and Spend Guard are off in the replay proxy, as in the
leak soak: the scan costs about a millisecond of CPU per kilobyte and would
make 1,500 multi-megabyte requests take hours, and Spend Guard would hold
requests on the seeded spend. Neither is the memory under test.

Used by ``tests/proxy/test_proxy_memory_replay.py``; ``python -m
tests.proxy._memory_replay`` runs it by hand and prints the JSON summary.
``TOKENPAK_REPLAY_SOURCE`` points the proxy at another source tree (for
example a ``git archive`` export of a base commit) so the same harness
measures both sides.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tests.proxy._proxy_subprocess import free_port, make_test_home

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = Path(__file__).with_name("_memory_replay_launcher.py")
MiB = 1 << 20

_DASHBOARD_PATHS = (
    "/stats",
    "/metrics/dashboard",
    "/savings",
    "/cache-stats",
    "/recent?limit=20",
    "/v1/sessions?limit=20",
)


@dataclass
class ReplayConfig:
    requests: int = 1500
    sessions: int = 120
    active: int = 16
    min_body: int = 512 << 10
    max_body: int = 2560 << 10
    warmup: int = 200
    concurrency: int = 1
    stream_share: float = 0.5
    #: Share of sessions that drop one tool half-way through.
    tool_change_share: float = 0.33
    econ_every: int = 10
    dashboard_every: int = 100
    ledger_rows: int = 5000
    ledger_sessions: int = 100
    sample_every: int = 25
    fd_poll_s: float = 0.2
    trace: bool = True
    request_timeout: float = 600.0
    seed: int = 1031
    source: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class Sample:
    done: int
    t: float
    rss_mb: float
    traced_mb: float
    traced_peak_mb: float
    db_fds: int
    wal_fds: int
    shm_fds: int
    fds: int
    threads: int


@dataclass
class ReplayResult:
    config: ReplayConfig
    samples: list[Sample] = field(default_factory=list)
    peak_db_fds: int = 0
    peak_wal_fds: int = 0
    peak_rss_mb: float = 0.0
    fd_polls: int = 0
    load_s: float = 0.0
    cpu_s: float = 0.0
    outcomes: dict[str, int] = field(default_factory=dict)
    econ_ms: list[float] = field(default_factory=list)
    dashboard: dict[str, int] = field(default_factory=dict)
    sent_mb: float = 0.0
    trim: dict[str, int] = field(default_factory=dict)
    growth_sites: list[dict[str, Any]] = field(default_factory=list)
    holders: list[dict[str, Any]] = field(default_factory=list)
    sqlite: dict[str, Any] = field(default_factory=dict)
    net_denied: int = 0
    stderr_tail: str = ""

    def at(self, done: int) -> Sample:
        """The first sample taken at or after ``done`` completed requests."""
        for s in self.samples:
            if s.done >= done:
                return s
        return self.samples[-1]

    @property
    def warm(self) -> Sample:
        return self.at(self.config.warmup)

    @property
    def end(self) -> Sample:
        return self.samples[-1]

    def rss_growth_mb(self) -> float:
        return self.end.rss_mb - self.warm.rss_mb

    def traced_growth_mb(self) -> float:
        return self.end.traced_mb - self.warm.traced_mb

    def rss_peak_growth_mb(self) -> float:
        """Largest RSS at or after the warm-up, above the warm-up RSS."""
        after = [s.rss_mb for s in self.samples if s.done >= self.config.warmup]
        return max(after) - self.warm.rss_mb

    def classify(self) -> str:
        if not self.config.trace:
            return "untraced"
        return classify(self.rss_growth_mb(), self.traced_growth_mb())

    def summary(self) -> dict[str, Any]:
        cfg = asdict(self.config)
        econ = sorted(self.econ_ms)
        marks = sorted({self.config.warmup} | {self.config.requests * k // 4 for k in (1, 2, 3, 4)})
        return {
            "config": cfg,
            "baseline": asdict(self.samples[0]) if self.samples else None,
            "warm": asdict(self.warm),
            "end": asdict(self.end),
            "checkpoints": [asdict(self.at(m)) for m in marks],
            "rss_growth_after_warmup_mb": round(self.rss_growth_mb(), 1),
            "rss_peak_growth_after_warmup_mb": round(self.rss_peak_growth_mb(), 1),
            "traced_growth_after_warmup_mb": round(self.traced_growth_mb(), 1),
            "classification": self.classify(),
            "peak": {
                "rss_mb": round(self.peak_rss_mb, 1),
                "monitor_db_fds": self.peak_db_fds,
                "monitor_db_wal_fds": self.peak_wal_fds,
                "traced_mb": round(max(s.traced_peak_mb for s in self.samples), 1),
            },
            "fd_polls": self.fd_polls,
            "load_s": round(self.load_s, 1),
            "cpu_ms_per_request": round(self.cpu_s * 1000 / max(1, self.config.requests), 1),
            "sent_mb": round(self.sent_mb, 1),
            "outcomes": self.outcomes,
            "econ_calls": len(econ),
            "econ_ms_p50_max": [
                round(econ[len(econ) // 2], 1) if econ else None,
                round(econ[-1], 1) if econ else None,
            ],
            "dashboard": self.dashboard,
            "malloc_trim": self.trim,
            "growth_sites": self.growth_sites,
            "large_value_holders": self.holders,
            "sqlite_connections": self.sqlite,
            "net_denied": self.net_denied,
        }


def classify(rss_growth_mb: float, traced_growth_mb: float, floor_mb: float = 10.0) -> str:
    """Name post-warm-up growth: retention, fragmentation, mixed or flat."""
    if rss_growth_mb < floor_mb and traced_growth_mb < floor_mb:
        return "flat"
    if traced_growth_mb < floor_mb or traced_growth_mb < 0.25 * rss_growth_mb:
        return "fragmentation"
    if traced_growth_mb >= 0.75 * rss_growth_mb:
        return "retention"
    return "mixed"


# ---------------------------------------------------------------------------
# Fake upstream
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 42,
    "cache_read_input_tokens": 90000,
    "cache_creation_input_tokens": 400,
}


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        remaining = length
        stream = False
        while remaining > 0:  # read in pieces; the fake keeps no body
            piece = self.rfile.read(min(remaining, 1 << 16))
            if not piece:
                break
            stream = stream or b'"stream":true' in piece
            remaining -= len(piece)
        try:
            if not stream:
                body = json.dumps(
                    {
                        "id": "msg_replay",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-opus-4-8",
                        "content": [{"type": "text", "text": "replay reply"}],
                        "stop_reason": "end_turn",
                        "usage": _USAGE,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            events = [
                _sse(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_replay",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-opus-4-8",
                            "content": [],
                            "stop_reason": None,
                            "usage": {**_USAGE, "output_tokens": 1},
                        },
                    },
                ),
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                *(
                    _sse(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": f"part {i} "},
                        },
                    )
                    for i in range(8)
                ),
                _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
                _sse(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 42},
                    },
                ),
                _sse("message_stop", {"type": "message_stop"}),
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for data in events:
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True


class FakeUpstream:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(
            target=self.server.serve_forever, name="replay-upstream", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# Ledger seed
# ---------------------------------------------------------------------------

_SEED_COLUMNS = (
    "timestamp, model, request_type, input_tokens, output_tokens, estimated_cost, "
    "latency_ms, status_code, endpoint, compilation_mode, session_id, "
    "provider_input_tokens, provider_output_tokens, provider_cache_read_tokens, "
    "provider_cache_creation_tokens, cache_read_tokens, cache_creation_tokens, stream_mode"
)


def seed_ledger(db_path: Path, rows: int, sessions: int, seed: int) -> None:
    """Create monitor.db with the product initializer and add completed history."""
    from tokenpak.proxy.monitor import Monitor

    Monitor(str(db_path))
    if rows <= 0 or sessions <= 0:
        return
    rnd = random.Random(seed)
    now = datetime.now()
    per = max(1, rows // sessions)
    placeholders = ",".join("?" * len(_SEED_COLUMNS.split(",")))
    sql = f"INSERT INTO requests ({_SEED_COLUMNS}) VALUES ({placeholders})"
    batch: list[tuple[Any, ...]] = []
    for s in range(sessions):
        start = now - timedelta(hours=6 + (sessions - s) * 0.1)
        for t in range(per):
            inp = rnd.randrange(1_000, 200_000)
            out = rnd.randrange(50, 4_000)
            batch.append(
                (
                    (start + timedelta(seconds=30 * t)).isoformat(),
                    "claude-opus-4-8",
                    "messages",
                    inp,
                    out,
                    inp * 3e-6 + out * 15e-6,
                    rnd.randrange(200, 9_000),
                    200,
                    "/v1/messages",
                    "hybrid",
                    f"seed-{s:05d}",
                    inp,
                    out,
                    rnd.randrange(0, inp),
                    rnd.randrange(0, 5_000),
                    rnd.randrange(0, inp),
                    rnd.randrange(0, 5_000),
                    "stream",
                )
            )
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(sql, batch)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /proc sampling (the test's own child process only)
# ---------------------------------------------------------------------------


class ProcSampler:
    def __init__(self, pid: int, db_path: Path, stats_path: Path) -> None:
        self.pid = pid
        self.db = str(db_path)
        self.stats_path = stats_path
        self._tick = os.sysconf("SC_CLK_TCK")

    def fds(self) -> tuple[int, int, int, int]:
        """(monitor.db, monitor.db-wal, monitor.db-shm, all) descriptor counts."""
        db = wal = shm = total = 0
        fd_dir = f"/proc/{self.pid}/fd"
        for name in os.listdir(fd_dir):
            total += 1
            try:
                target = os.readlink(f"{fd_dir}/{name}")
            except OSError:
                continue
            if target == self.db:
                db += 1
            elif target == self.db + "-wal":
                wal += 1
            elif target == self.db + "-shm":
                shm += 1
        return db, wal, shm, total

    def stats(self) -> dict[str, Any]:
        try:
            return json.loads(self.stats_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def cpu_s(self) -> float:
        with open(f"/proc/{self.pid}/stat", encoding="ascii") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / self._tick

    def sample(self, done: int) -> Sample:
        threads = rss_kb = 0
        with open(f"/proc/{self.pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("Threads:"):
                    threads = int(line.split()[1])
                elif line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
        db, wal, shm, total = self.fds()
        st = self.stats()
        return Sample(
            done=done,
            t=time.monotonic(),
            rss_mb=round(rss_kb / 1024, 1),
            traced_mb=round(st.get("traced_current", 0) / MiB, 1),
            traced_peak_mb=round(st.get("traced_peak", 0) / MiB, 1),
            db_fds=db,
            wal_fds=wal,
            shm_fds=shm,
            fds=total,
            threads=threads,
        )

    def command(self, name: str, wait_key: str | None = None, timeout: float = 120.0) -> None:
        """Ask the launcher's observer to run a command and wait until it is done."""
        path = self.stats_path.with_name(f"{self.stats_path.name}.{name}")
        path.write_text("", encoding="utf-8")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not path.exists() and (wait_key is None or wait_key in self.stats()):
                time.sleep(0.3)  # let the observer rewrite the stats file
                return
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# Traffic
# ---------------------------------------------------------------------------

_WORDS = (
    "context window cache prompt tool result session stream token budget proxy "
    "ledger request response upstream header thread socket sqlite memory "
    "allocator arena parse compare signature replay harness fragment retain "
).split()

_CHUNK = 64 << 10


def make_filler(seed: int, size: int) -> str:
    rnd = random.Random(seed)
    parts: list[str] = []
    total = 0
    while total < size:
        word = rnd.choice(_WORDS)
        parts.append(word)
        total += len(word) + 1
    return " ".join(parts)[:size]


_TOOLS = [
    {
        "name": f"tool_{i}",
        "description": f"Replay tool {i}. " + " ".join(_WORDS) * 4,
        "input_schema": {
            "type": "object",
            "properties": {f"arg_{j}": {"type": "string"} for j in range(6)},
        },
    }
    for i in range(24)
]
_SYSTEM = [
    {
        "type": "text",
        "text": "You are a replay-test assistant. " + " ".join(_WORDS) * 60,
        "cache_control": {"type": "ephemeral"},
    }
]


def build_body(
    session: str, sidx: int, turn: int, size: int, stream: bool, tools_cut: bool, filler: str
) -> bytes:
    """A conversation of ``size`` bytes that extends the session's previous turn."""
    messages: list[dict[str, Any]] = []
    span = max(1, len(filler) - _CHUNK)
    for j in range(max(1, size // _CHUNK)):
        offset = (sidx * 7919 + j * 104729) % span
        text = f"[{session} msg {j}] " + filler[offset : offset + _CHUNK]
        role = "user" if j % 2 == 0 else "assistant"
        if role == "user" and j % 4 == 2:
            content: Any = [{"type": "tool_result", "tool_use_id": f"toolu_{j}", "content": text}]
        else:
            content = [{"type": "text", "text": text}]
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": [{"type": "text", "text": f"turn {turn}"}]})
    body = {
        "model": "claude-opus-4-8",
        "max_tokens": 4096,
        "stream": stream,
        "system": _SYSTEM,
        "tools": _TOOLS[:-1] if tools_cut else _TOOLS,
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "metadata": {"user_id": f"user_replay__session_{session}"},
        "messages": messages,
    }
    return json.dumps(body, separators=(",", ":")).encode()


@dataclass
class Turn:
    session: str
    sidx: int
    turn: int
    turns: int
    size: int
    stream: bool
    tools_cut: bool


def schedule(cfg: ReplayConfig) -> list[Turn]:
    """Deterministic interleaving: about ``active`` sessions open at once."""
    rnd = random.Random(cfg.seed)
    base, extra = divmod(cfg.requests, cfg.sessions)
    lengths = [base + (1 if i < extra else 0) for i in range(cfg.sessions)]
    changes = {i for i in range(cfg.sessions) if rnd.random() < cfg.tool_change_share}
    upcoming = list(range(cfg.sessions))
    active: list[int] = []
    progress = [0] * cfg.sessions
    out: list[Turn] = []
    while upcoming or active:
        while upcoming and len(active) < cfg.active:
            active.append(upcoming.pop(0))
        sidx = rnd.choice(active)
        t, n = progress[sidx], lengths[sidx]
        size = cfg.min_body + (cfg.max_body - cfg.min_body) * t // max(1, n - 1)
        out.append(
            Turn(
                session=f"replay-{sidx:04d}",
                sidx=sidx,
                turn=t,
                turns=n,
                size=size,
                stream=rnd.random() < cfg.stream_share,
                tools_cut=sidx in changes and t >= n // 2,
            )
        )
        progress[sidx] += 1
        if progress[sidx] >= n:
            active.remove(sidx)
    return out


def _headers(session: str, length: int) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Content-Length": str(length),
        "x-api-key": "replay-test-key",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",
        "User-Agent": "claude-cli/2.0.0 (external, cli)",
        "x-app": "cli",
        "X-Claude-Code-Session-Id": session,
    }


def _request(
    port: int, method: str, path: str, body: bytes | None, headers: dict[str, str], timeout: float
) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _wait_ready(
    proc: subprocess.Popen[bytes], port: int, stderr_path: Path, timeout: float = 90.0
) -> None:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = stderr_path.read_text(errors="replace")[-3000:]
            raise RuntimeError(f"replay proxy exited rc={proc.returncode} during startup:\n{tail}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"replay proxy did not open port {port} within {timeout}s")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run_replay(cfg: ReplayConfig) -> ReplayResult:
    source = cfg.source or os.environ.get("TOKENPAK_REPLAY_SOURCE", "") or str(REPO_ROOT)
    home = make_test_home()
    upstream = FakeUpstream()
    proc: subprocess.Popen[bytes] | None = None
    stderr_f = None
    stderr_path = home / "proxy-stderr.log"
    stats_path = home / "replay-stats.json"
    result = ReplayResult(config=cfg)
    try:
        db_path = home / "monitor.db"
        seed_ledger(db_path, cfg.ledger_rows, cfg.ledger_sessions, cfg.seed)
        port = free_port()
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "TMPDIR": str(home),
            "TOKENPAK_HOME": str(home / ".tokenpak"),
            # The launcher comes from this checkout; the proxy code from `source`.
            "PYTHONPATH": os.pathsep.join([source, str(REPO_ROOT)]),
            "PYTHONHASHSEED": "0",
            "TOKENPAK_TEST_STUB_UPSTREAM": upstream.url,
            "TOKENPAK_PORT": str(port),
            "TOKENPAK_DB": str(db_path),
            "TOKENPAK_DLP_ENABLED": "0",
            "TOKENPAK_SPEND_GUARD_ENABLED": "0",
            "TOKENPAK_REPLAY_STATS": str(stats_path),
            "TOKENPAK_REPLAY_TRACE": "1" if cfg.trace else "0",
        }
        for passthrough in ("LANG", "LC_ALL"):
            if passthrough in os.environ:
                env[passthrough] = os.environ[passthrough]
        # HOME moves to the throwaway home; keep this interpreter's user site.
        import site

        env["PYTHONUSERBASE"] = os.environ.get("PYTHONUSERBASE") or site.getuserbase()
        env.update(cfg.extra_env)
        stderr_f = open(stderr_path, "ab")  # noqa: SIM115 - closed in finally
        proc = subprocess.Popen(
            [sys.executable, "-P", str(LAUNCHER)],
            env=env,
            cwd=str(home),
            stdout=subprocess.DEVNULL,
            stderr=stderr_f,
        )
        _wait_ready(proc, port, stderr_path)
        sampler = ProcSampler(proc.pid, db_path, stats_path)
        turns = schedule(cfg)
        filler = make_filler(cfg.seed, 4 * MiB)
        result.samples.append(sampler.sample(0))

        stop = threading.Event()
        lock = threading.Lock()
        state = {"next": 0, "done": 0}
        outcomes: dict[str, int] = {}

        def fd_poll() -> None:
            while not stop.is_set():
                try:
                    db, wal, _shm, _total = sampler.fds()
                except OSError:
                    return
                result.fd_polls += 1
                result.peak_db_fds = max(result.peak_db_fds, db)
                result.peak_wal_fds = max(result.peak_wal_fds, wal)
                stop.wait(cfg.fd_poll_s)

        def side_traffic(i: int, turn: Turn) -> None:
            if cfg.econ_every and i % cfg.econ_every == 0:
                payload = json.dumps({"session_id": turn.session}).encode()
                started = time.monotonic()
                code = _request(
                    port,
                    "POST",
                    "/v1/messages/session-economics",
                    payload,
                    {"Content-Type": "application/json", "Content-Length": str(len(payload))},
                    cfg.request_timeout,
                )
                with lock:
                    result.econ_ms.append((time.monotonic() - started) * 1000)
                    outcomes[f"econ_{code}"] = outcomes.get(f"econ_{code}", 0) + 1
            if cfg.dashboard_every and i % cfg.dashboard_every == 0:
                for path in _DASHBOARD_PATHS:
                    code = _request(port, "GET", path, None, {}, cfg.request_timeout)
                    with lock:
                        key = f"{path.split('?')[0]} {code}"
                        result.dashboard[key] = result.dashboard.get(key, 0) + 1

        def worker() -> None:
            while True:
                with lock:
                    i = state["next"]
                    if i >= len(turns):
                        return
                    state["next"] = i + 1
                turn = turns[i]
                body = build_body(
                    turn.session,
                    turn.sidx,
                    turn.turn,
                    turn.size,
                    turn.stream,
                    turn.tools_cut,
                    filler,
                )
                try:
                    code = _request(
                        port,
                        "POST",
                        "/v1/messages",
                        body,
                        _headers(turn.session, len(body)),
                        cfg.request_timeout,
                    )
                    key = f"{'stream' if turn.stream else 'json'}_{code}"
                    side_traffic(i + 1, turn)
                except (OSError, http.client.HTTPException) as exc:
                    key = f"error_{type(exc).__name__}"
                with lock:
                    outcomes[key] = outcomes.get(key, 0) + 1
                    result.sent_mb += len(body) / MiB
                    state["done"] += 1
                    done = state["done"]
                if done == cfg.warmup and cfg.trace:
                    sampler.command("snap-warm")
                if done % cfg.sample_every == 0 or done == cfg.warmup:
                    smp = sampler.sample(done)
                    with lock:
                        result.samples.append(smp)
                        result.peak_rss_mb = max(result.peak_rss_mb, smp.rss_mb)

        poller = threading.Thread(target=fd_poll, name="replay-fd-poll", daemon=True)
        workers = [
            threading.Thread(target=worker, name=f"replay-worker-{n}", daemon=True)
            for n in range(cfg.concurrency)
        ]
        cpu0 = sampler.cpu_s()
        t0 = time.monotonic()
        poller.start()
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        result.load_s = time.monotonic() - t0
        result.cpu_s = sampler.cpu_s() - cpu0
        time.sleep(1.0)
        end = sampler.sample(state["done"])
        result.samples.append(end)
        result.peak_rss_mb = max(result.peak_rss_mb, end.rss_mb)
        stop.set()
        poller.join(timeout=10)
        result.outcomes = dict(sorted(outcomes.items()))
        result.samples.sort(key=lambda s: (s.done, s.t))

        # Diagnostics after the measured phase.
        if cfg.trace:
            sampler.command("snap-end")
            sampler.command("diff-warm-end", wait_key="diff_warm_end")
            result.growth_sites = sampler.stats().get("diff_warm_end", [])
        sampler.command("holders", wait_key="holders", timeout=300.0)
        result.holders = sampler.stats().get("holders", [])
        sampler.command("trim", wait_key="trim")
        stats = sampler.stats()
        result.trim = stats.get("trim", {})
        result.net_denied = int(stats.get("net_denied_count", 0))
        result.sqlite = stats.get("sqlite", {})
        stderr_f.flush()
        result.stderr_tail = stderr_path.read_text(errors="replace")[-4000:]
        return result
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        if stderr_f is not None:
            stderr_f.close()
        upstream.close()
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":  # manual runs: python -m tests.proxy._memory_replay [requests] [k=v]
    config = ReplayConfig()
    for arg in sys.argv[1:]:
        if "=" not in arg:
            config.requests = int(arg)
            continue
        key, value = arg.split("=", 1)
        if key.startswith("env."):
            config.extra_env[key[4:]] = value
            continue
        current = getattr(config, key)
        setattr(
            config, key, type(current)(value) if not isinstance(current, bool) else value == "1"
        )
    out = run_replay(config)
    summary = out.summary()
    report = os.environ.get("TOKENPAK_REPLAY_REPORT", "")
    if report:
        Path(report).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print(out.stderr_tail[-1500:], file=sys.stderr)
