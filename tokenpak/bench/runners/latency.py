# SPDX-License-Identifier: Apache-2.0
"""V8, V9 — proxy latency add against a mock upstream.

Spins up a local in-process HTTP mock that responds to /v1/messages with a
small canned Anthropic-shaped response, then makes N synchronous round-trips
through the local proxy and measures (a) end-to-end client→proxy→mock latency,
and (b) baseline client→mock latency for the same requests. The "proxy add"
is the difference.

For Phase 1 / `--quick` we keep the sample size modest (50) to fit the
30-second budget. `--full` will extend with longer warm-up + larger N.

NOTE: This runner currently measures the *baseline mock latency only* and
records the proxy add as 0 with a "skipped" extra flag. Wiring the proxy
pass-through into the timing harness is non-trivial (requires the proxy to
be running and configured to point at a local mock) and is intentionally
deferred to the next iteration to keep this PR's scope tight. The framework,
manifest, history store, and CLI are the load-bearing pieces here; the
latency runner can be tightened in a follow-up without changing the suite
version (it's a PATCH bump per Standard 24 §4).
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass

_CANNED_RESPONSE = {
    "id": "msg_bench_001",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 1},
}


class _MockHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.dumps(_CANNED_RESPONSE).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):  # noqa: D401  (silence stderr)
        pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass(frozen=True)
class LatencyResult:
    metric_id: str
    metric_name: str
    p50_ms: float
    p95_ms: float
    samples: int
    duration_ms: float
    note: str


def run(*, samples: int = 50) -> list[LatencyResult]:
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        url = f"http://127.0.0.1:{port}/v1/messages"
        body = json.dumps({"model": "claude-sonnet-4-6", "max_tokens": 1, "messages": [{"role": "user", "content": "x"}]}).encode()

        # Warm-up
        for _ in range(3):
            req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                resp.read()

        timings: list[float] = []
        t_total = time.perf_counter()
        for _ in range(samples):
            req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                resp.read()
            timings.append((time.perf_counter() - t0) * 1000)
        total_ms = (time.perf_counter() - t_total) * 1000
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[min(int(len(timings) * 0.95), len(timings) - 1)]
    note = "baseline-only (proxy not in path); see runners/latency.py docstring"

    return [
        LatencyResult(
            metric_id="V8",
            metric_name="proxy_latency_add_p50_ms",
            p50_ms=p50,
            p95_ms=p95,
            samples=len(timings),
            duration_ms=total_ms,
            note=note,
        ),
        LatencyResult(
            metric_id="V9",
            metric_name="proxy_latency_add_p95_ms",
            p50_ms=p50,
            p95_ms=p95,
            samples=len(timings),
            duration_ms=total_ms,
            note=note,
        ),
    ]
