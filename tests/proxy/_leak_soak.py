"""Proxy leak soak: a real proxy process, a local fake upstream, /proc numbers.

Drives a Claude Code-shaped mix through a real ``ProxyServer`` subprocess and
measures that process from ``/proc``: live threads, RSS, CPU time and the
open file descriptors on its ``monitor.db``. Nothing leaves loopback.

The mix mirrors a busy host:

- model requests from several concurrent sessions that carry a stable
  session header, mostly streaming, some non-streaming, and some whose client
  disconnects mid-stream; a share of them carries 1-3 MB bodies (long
  conversations), the rest 16-96 KB;
- one status-line poller per session, posting
  ``/v1/messages/session-economics`` every two seconds with a 0.5 s client
  timeout (the companion status writer's cadence);
- one open dashboard tab polling its endpoints every five seconds.

The ledger is seeded with completed history so that session economics has a
realistic corpus to read. The outbound DLP scan is switched off in the soak
proxy: it costs about a millisecond of CPU per kilobyte of every request
body and is unrelated to the lifecycle under test, and with it on the 2,000
requests no longer fit in a bounded run on one core.

Used by ``tests/proxy/test_proxy_leak_soak.py``. Not a test module.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tests.proxy._proxy_subprocess import free_port, make_test_home

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = Path(__file__).with_name("_leak_soak_launcher.py")

_DASHBOARD_PATHS = (
    "/stats",
    "/metrics/dashboard",
    "/health",
    "/status",
    "/savings",
    "/recent?limit=20",
    "/cache-stats",
    "/v1/sessions?limit=20",
)


@dataclass
class SoakConfig:
    requests: int = 2000
    concurrency: int = 16
    sessions: int = 8
    min_body: int = 1 << 20
    max_body: int = 3 << 20
    #: Share of requests with min_body..max_body bodies; the rest carry
    #: small_min..small_max. Each 1-3 MB request costs the proxy on the order
    #: of a second of single-threaded CPU, so an all-large mix measures CPU
    #: saturation rather than leaks.
    large_share: float = 0.05
    small_min: int = 16 << 10
    small_max: int = 96 << 10
    disconnect_share: float = 0.10
    nonstream_share: float = 0.15
    status_pollers: int = 4
    status_interval: float = 2.0
    status_timeout: float = 0.5
    dashboard_interval: float = 5.0
    ledger_rows: int = 20_000
    ledger_sessions: int = 400
    drain_timeout: float = 90.0
    thread_slack: int = 4
    #: Long enough that a saturated proxy is waited for, not abandoned.
    request_timeout: float = 600.0
    #: The load phase stops issuing requests after this long; a run that hits
    #: it reports ``load_timed_out`` (the leaking proxy slows to a crawl).
    load_deadline_s: float = 1200.0
    diagnostics: bool = True


@dataclass
class Sample:
    t: float
    done: int
    threads: int
    rss_mb: float
    db_fds: int
    fds: int
    cpu_s: float


@dataclass
class SoakResult:
    config: SoakConfig
    baseline: Sample
    checkpoints: list[Sample] = field(default_factory=list)
    peak_threads: int = 0
    peak_db_fds: int = 0
    peak_rss_mb: float = 0.0
    end_of_load: Sample | None = None
    final: Sample | None = None
    drain_s: float = 0.0
    load_s: float = 0.0
    cpu_ms_per_request: float = 0.0
    completed: int = 0
    load_timed_out: bool = False
    outcomes: dict[str, int] = field(default_factory=dict)
    status_calls: dict[str, int] = field(default_factory=dict)
    dashboard_calls: dict[str, int] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    stderr_tail: str = ""

    def summary(self) -> dict[str, Any]:
        def s(x: Sample | None) -> dict[str, Any] | None:
            if x is None:
                return None
            return {
                "done": x.done,
                "threads": x.threads,
                "rss_mb": round(x.rss_mb, 1),
                "monitor_db_fds": x.db_fds,
                "fds": x.fds,
            }

        return {
            "requests": self.config.requests,
            "concurrency": self.config.concurrency,
            "baseline": s(self.baseline),
            "checkpoints": [s(c) for c in self.checkpoints],
            "peak": {
                "threads": self.peak_threads,
                "monitor_db_fds": self.peak_db_fds,
                "rss_mb": round(self.peak_rss_mb, 1),
            },
            "end_of_load": s(self.end_of_load),
            "final": s(self.final),
            "drain_s": round(self.drain_s, 1),
            "load_s": round(self.load_s, 1),
            "cpu_ms_per_request": round(self.cpu_ms_per_request, 1),
            "completed": self.completed,
            "load_timed_out": self.load_timed_out,
            "outcomes": self.outcomes,
            "status_calls": self.status_calls,
            "dashboard_calls": self.dashboard_calls,
            "diagnostics": self.diagnostics,
        }


# ---------------------------------------------------------------------------
# Fake upstream
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    deltas = 20
    delta_sleep = 0.01

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        stream = b'"stream":true' in raw or b'"stream": true' in raw
        usage = {
            "input_tokens": 1200,
            "output_tokens": 1,
            "cache_read_input_tokens": 90000,
            "cache_creation_input_tokens": 400,
        }
        try:
            if not stream:
                body = json.dumps(
                    {
                        "id": "msg_soak",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-opus-4-8",
                        "content": [{"type": "text", "text": "soak reply"}],
                        "stop_reason": "end_turn",
                        "usage": {**usage, "output_tokens": 42},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def chunk(data: bytes) -> None:
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            chunk(
                _sse(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_soak",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-opus-4-8",
                            "content": [],
                            "stop_reason": None,
                            "usage": usage,
                        },
                    },
                )
            )
            chunk(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            for i in range(self.deltas):
                time.sleep(self.delta_sleep)
                chunk(
                    _sse(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": f"part {i} "},
                        },
                    )
                )
            chunk(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
            chunk(
                _sse(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 42},
                    },
                )
            )
            chunk(_sse("message_stop", {"type": "message_stop"}))
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
            target=self.server.serve_forever, name="soak-upstream", daemon=True
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
    "latency_ms, status_code, endpoint, compilation_mode, session_id, reasoning_effort, "
    "provider_input_tokens, provider_output_tokens, provider_cache_read_tokens, "
    "provider_cache_creation_tokens, cache_read_tokens, cache_creation_tokens, "
    "stream_mode, ttfb_ms, stream_duration_ms"
)


def seed_ledger(db_path: Path, rows: int, sessions: int) -> None:
    """Create monitor.db with the product initializer and add completed history."""
    from tokenpak.proxy.monitor import Monitor

    Monitor(str(db_path))
    if rows <= 0 or sessions <= 0:
        return
    rnd = random.Random(1031)
    now = datetime.now()
    per = max(1, rows // sessions)
    models = ("claude-opus-4-8", "claude-sonnet-4-5", "claude-haiku-4-5")
    placeholders = ",".join("?" * len(_SEED_COLUMNS.split(",")))
    sql = f"INSERT INTO requests ({_SEED_COLUMNS}) VALUES ({placeholders})"
    conn = sqlite3.connect(str(db_path))
    try:
        batch: list[tuple[Any, ...]] = []
        for s in range(sessions):
            sid = f"seed-{s:05d}"
            model = models[s % len(models)]
            start = now - timedelta(hours=8 + (sessions - s) * 0.1)
            for t in range(per):
                inp = rnd.randrange(1_000, 200_000)
                out = rnd.randrange(50, 4_000)
                batch.append(
                    (
                        (start + timedelta(seconds=30 * t)).isoformat(),
                        model,
                        "messages",
                        inp,
                        out,
                        inp * 3e-6 + out * 15e-6,
                        rnd.randrange(200, 9_000),
                        200,
                        "/v1/messages",
                        "hybrid",
                        sid,
                        "high",
                        inp,
                        out,
                        rnd.randrange(0, inp),
                        rnd.randrange(0, 5_000),
                        rnd.randrange(0, inp),
                        rnd.randrange(0, 5_000),
                        "stream",
                        rnd.randrange(100, 2_000),
                        rnd.randrange(100, 9_000),
                    )
                )
                if len(batch) >= 5_000:
                    conn.executemany(sql, batch)
                    batch.clear()
        if batch:
            conn.executemany(sql, batch)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /proc sampling (the test's own child process only)
# ---------------------------------------------------------------------------


class ProcSampler:
    def __init__(self, pid: int, db_path: Path) -> None:
        self.pid = pid
        self.db_path = str(db_path)
        self._tick = os.sysconf("SC_CLK_TCK")

    def sample(self, done: int) -> Sample:
        threads = 0
        rss_kb = 0
        with open(f"/proc/{self.pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("Threads:"):
                    threads = int(line.split()[1])
                elif line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
        fds = db_fds = 0
        fd_dir = f"/proc/{self.pid}/fd"
        for name in os.listdir(fd_dir):
            fds += 1
            try:
                if os.readlink(f"{fd_dir}/{name}") == self.db_path:
                    db_fds += 1
            except OSError:
                pass
        with open(f"/proc/{self.pid}/stat", encoding="ascii") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        cpu = (int(fields[11]) + int(fields[12])) / self._tick
        return Sample(time.monotonic(), done, threads, rss_kb / 1024, db_fds, fds, cpu)


# ---------------------------------------------------------------------------
# Request mix
# ---------------------------------------------------------------------------

_WORDS = (
    "context window cache prompt tool result session stream token budget proxy "
    "ledger request response upstream header thread socket sqlite memory "
).split()


def _filler(seed: int, size: int) -> str:
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
        "description": f"Soak tool {i}. " + " ".join(_WORDS) * 4,
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
        "text": "You are a soak-test assistant. " + " ".join(_WORDS) * 60,
        "cache_control": {"type": "ephemeral"},
    }
]


def build_body(session: str, turn: int, size: int, stream: bool, filler: str) -> bytes:
    chunk = 64 * 1024
    messages: list[dict[str, Any]] = []
    remaining = size
    j = 0
    while remaining > 0:
        n = min(chunk, remaining)
        offset = (turn * 7919 + j * 104729) % max(1, len(filler) - n)
        text = f"[{session} turn {turn} msg {j}] " + filler[offset : offset + n]
        role = "user" if j % 2 == 0 else "assistant"
        if role == "user" and j % 4 == 2:
            content: Any = [
                {"type": "tool_result", "tool_use_id": f"toolu_{turn}_{j}", "content": text}
            ]
        else:
            content = [{"type": "text", "text": text}]
        messages.append({"role": role, "content": content})
        remaining -= n
        j += 1
    if messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": [{"type": "text", "text": "continue"}]})
    tools = _TOOLS if (turn // 50) % 2 == 0 else _TOOLS[:-1]
    body = {
        "model": "claude-opus-4-8",
        "max_tokens": 4096,
        "stream": stream,
        "system": _SYSTEM,
        "tools": tools,
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "metadata": {"user_id": f"user_soak_account__session_{session}"},
        "messages": messages,
    }
    return json.dumps(body, separators=(",", ":")).encode()


def _headers(session: str, length: int) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Content-Length": str(length),
        "x-api-key": "soak-test-key",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",
        "User-Agent": "claude-cli/2.0.0 (external, cli)",
        "x-app": "cli",
        "X-Claude-Code-Session-Id": session,
    }


def _post_full(port: int, path: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _post_disconnect(
    port: int, path: str, body: bytes, headers: dict[str, str], timeout: float
) -> str:
    """Send a streaming request, read the first stream bytes, then reset the socket."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        head = f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        head += "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        sock.sendall(head.encode() + body)
        got = b""
        while b"\r\n\r\n" not in got or len(got.split(b"\r\n\r\n", 1)[1]) < 64:
            data = sock.recv(4096)
            if not data:
                return "closed_early"
            got += data
        return "disconnected"
    finally:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        sock.close()


# ---------------------------------------------------------------------------
# Soak run
# ---------------------------------------------------------------------------


class _Counter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}

    def add(self, key: str) -> None:
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1


def run_soak(cfg: SoakConfig) -> SoakResult:
    home = make_test_home()
    upstream = FakeUpstream()
    proc: subprocess.Popen[bytes] | None = None
    stderr_path = home / "proxy-stderr.log"
    diag_path = home / "soak-diag.json"
    try:
        db_path = home / "monitor.db"
        seed_ledger(db_path, cfg.ledger_rows, cfg.ledger_sessions)
        port = free_port()
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "TOKENPAK_HOME": str(home / ".tokenpak"),
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH", "")) if p
            ),
            "TOKENPAK_TEST_STUB_UPSTREAM": upstream.url,
            "TOKENPAK_PORT": str(port),
            "TOKENPAK_DB": str(db_path),
            "TMPDIR": str(home),
            "TOKENPAK_DLP_ENABLED": "0",
        }
        for passthrough in (
            "PYTHONUSERBASE",
            "PYTHONHASHSEED",
            "LANG",
            "LC_ALL",
            "QA_NET_GUARD",
            "QA_NET_LOG",
            "QA_ROOT",
            "QA_DENY_PORTS",
        ):
            if passthrough in os.environ:
                env[passthrough] = os.environ[passthrough]
        if cfg.diagnostics:
            env["TOKENPAK_SOAK_DIAG"] = str(diag_path)
        stderr_f = open(stderr_path, "ab")
        proc = subprocess.Popen(
            [sys.executable, str(LAUNCHER)],
            env=env,
            cwd=str(home),
            stdout=subprocess.DEVNULL,
            stderr=stderr_f,
        )
        _wait_ready(proc, port)
        sampler = ProcSampler(proc.pid, db_path)

        sessions = [f"soak-session-{i:02d}" for i in range(cfg.sessions)]
        fillers = {s: _filler(i, cfg.max_body + 4096) for i, s in enumerate(sessions)}

        # Warm-up: lazy imports, first ledger writes, one call per poller path.
        for i, s in enumerate(sessions):
            body = build_body(s, 0, cfg.min_body, i % 2 == 0, fillers[s])
            _post_full(port, "/v1/messages", body, _headers(s, len(body)), cfg.request_timeout)
        for path in _DASHBOARD_PATHS:
            _get(port, path, 30.0)
        econ = json.dumps({"session_id": sessions[0]}).encode()
        _post_full(
            port,
            "/v1/messages/session-economics",
            econ,
            {"Content-Type": "application/json", "Content-Length": str(len(econ))},
            120.0,
        )
        time.sleep(3.0)
        baseline = sampler.sample(0)
        result = SoakResult(config=cfg, baseline=baseline)

        stop = threading.Event()
        lock = threading.Lock()
        state = {"next": 0, "done": 0}
        turns = {s: 1 for s in sessions}
        outcomes = _Counter()
        status_calls = _Counter()
        dashboard_calls = _Counter()
        marks = [cfg.requests * k // 4 for k in (1, 2, 3, 4)]

        deadline = time.monotonic() + cfg.load_deadline_s

        def worker() -> None:
            while True:
                with lock:
                    i = state["next"]
                    if i >= cfg.requests or time.monotonic() > deadline:
                        return
                    state["next"] = i + 1
                    s = sessions[i % len(sessions)]
                    turn = turns[s]
                    turns[s] = turn + 1
                rnd = random.Random(i)
                pick = rnd.random()
                low, high = (
                    (cfg.min_body, cfg.max_body)
                    if rnd.random() < cfg.large_share
                    else (cfg.small_min, cfg.small_max)
                )
                size = low + (high - low) * (turn % 20) // 19
                try:
                    if pick < cfg.disconnect_share:
                        body = build_body(s, turn, size, True, fillers[s])
                        outcomes.add(
                            _post_disconnect(
                                port,
                                "/v1/messages",
                                body,
                                _headers(s, len(body)),
                                cfg.request_timeout,
                            )
                        )
                    else:
                        stream = pick >= cfg.disconnect_share + cfg.nonstream_share
                        body = build_body(s, turn, size, stream, fillers[s])
                        code = _post_full(
                            port,
                            "/v1/messages",
                            body,
                            _headers(s, len(body)),
                            cfg.request_timeout,
                        )
                        outcomes.add(f"{'stream' if stream else 'json'}_{code}")
                except (OSError, http.client.HTTPException) as exc:
                    outcomes.add(f"error_{type(exc).__name__}")
                with lock:
                    state["done"] += 1

        def status_poller(session: str) -> None:
            payload = json.dumps({"session_id": session}).encode()
            headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
            while not stop.is_set():
                try:
                    code = _post_full(
                        port,
                        "/v1/messages/session-economics",
                        payload,
                        headers,
                        cfg.status_timeout,
                    )
                    status_calls.add(str(code))
                except (OSError, http.client.HTTPException):
                    status_calls.add("timeout")
                stop.wait(cfg.status_interval)

        def dashboard_poller() -> None:
            while not stop.is_set():
                for path in _DASHBOARD_PATHS:
                    try:
                        dashboard_calls.add(str(_get(port, path, 10.0)))
                    except (OSError, http.client.HTTPException):
                        dashboard_calls.add("timeout")
                stop.wait(cfg.dashboard_interval)

        def sample_loop() -> None:
            next_mark = 0
            while not stop.is_set():
                with lock:
                    done = state["done"]
                try:
                    smp = sampler.sample(done)
                except OSError:
                    return
                result.peak_threads = max(result.peak_threads, smp.threads)
                result.peak_db_fds = max(result.peak_db_fds, smp.db_fds)
                result.peak_rss_mb = max(result.peak_rss_mb, smp.rss_mb)
                while next_mark < len(marks) and done >= marks[next_mark]:
                    result.checkpoints.append(smp)
                    next_mark += 1
                stop.wait(0.5)

        pollers = [
            threading.Thread(target=status_poller, args=(s,), name=f"soak-status-{s}", daemon=True)
            for s in sessions[: cfg.status_pollers]
        ]
        pollers.append(threading.Thread(target=dashboard_poller, name="soak-dash", daemon=True))
        sampler_thread = threading.Thread(target=sample_loop, name="soak-sampler", daemon=True)
        workers = [
            threading.Thread(target=worker, name=f"soak-worker-{i}", daemon=True)
            for i in range(cfg.concurrency)
        ]
        cpu0 = sampler.sample(0).cpu_s
        t0 = time.monotonic()
        sampler_thread.start()
        for t in pollers + workers:
            t.start()
        for t in workers:
            t.join()
        result.load_s = time.monotonic() - t0
        result.completed = state["done"]
        result.load_timed_out = state["done"] < cfg.requests
        end = sampler.sample(state["done"])
        result.end_of_load = end
        if len(result.checkpoints) < len(marks):
            result.checkpoints.append(end)
        result.cpu_ms_per_request = (end.cpu_s - cpu0) * 1000 / max(1, state["done"])
        stop.set()
        for t in pollers + [sampler_thread]:
            t.join(timeout=30)

        drain_start = time.monotonic()
        final = sampler.sample(state["done"])
        while time.monotonic() - drain_start < cfg.drain_timeout:
            final = sampler.sample(state["done"])
            if final.threads <= baseline.threads + cfg.thread_slack:
                break
            time.sleep(0.5)
        time.sleep(2.0)
        final = sampler.sample(state["done"])
        result.final = final
        result.drain_s = time.monotonic() - drain_start
        result.outcomes = dict(sorted(outcomes.counts.items()))
        result.status_calls = dict(sorted(status_calls.counts.items()))
        result.dashboard_calls = dict(sorted(dashboard_calls.counts.items()))
        if cfg.diagnostics:
            try:
                result.diagnostics = json.loads(diag_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result.diagnostics = {}
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
        upstream.close()
        shutil.rmtree(home, ignore_errors=True)


def _get(port: int, path: str, timeout: float) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _wait_ready(proc: subprocess.Popen[bytes], port: int, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"soak proxy exited rc={proc.returncode} during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"soak proxy did not open port {port} within {timeout}s")


if __name__ == "__main__":  # manual runs: python -m tests.proxy._leak_soak [requests]
    cfg = SoakConfig()
    if len(sys.argv) > 1:
        cfg.requests = int(sys.argv[1])
    out = run_soak(cfg)
    print(json.dumps(out.summary(), indent=1))
    print(out.stderr_tail[-1500:], file=sys.stderr)
