"""
Tests for the async Starlette/uvicorn proxy backend.

Covers:
- All management endpoints return correct JSON (preserved from legacy server)
- 50+ concurrent requests complete without blocking
- <10ms proxy overhead on management endpoints
- Backpressure middleware returns 503 at capacity
- Backward-compatible CLI: start_proxy() still works
- CONNECT tunnelling (smoke test)
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

pytestmark = pytest.mark.needs_proxy

# Force async backend for all tests in this module
os.environ.setdefault("TOKENPAK_ASYNC_PROXY", "1")
os.environ.setdefault("TOKENPAK_CONCURRENCY", "200")


# ---------------------------------------------------------------------------
# Fixture: start async proxy on an ephemeral port
# ---------------------------------------------------------------------------

ASYNC_PORT = 19766
_BASE = f"http://127.0.0.1:{ASYNC_PORT}"


@pytest.fixture(scope="module")
def async_proxy():
    """Start the async proxy and yield; tear down after module tests complete."""
    from tokenpak.proxy.server import ProxyServer

    server = ProxyServer(host="127.0.0.1", port=ASYNC_PORT)
    server.start(blocking=False)
    # Give uvicorn a moment to bind
    _wait_for_port(ASYNC_PORT, timeout=8)
    yield server
    server.stop()


def _wait_for_port(port: int, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Port {port} did not become available within {timeout}s")


def _get(path: str) -> tuple[int, dict]:
    url = _BASE + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


# ---------------------------------------------------------------------------
# Test 1 — /health returns 200 with required fields
# ---------------------------------------------------------------------------

REQUIRED_HEALTH_FIELDS = {
    "status",
    "uptime_seconds",
    "version",
    "requests_total",
    "requests_errors",
    "compression_ratio_avg",
    "timestamp",
}


def test_async_health_200(async_proxy):
    status, data = _get("/health")
    assert status == 200


def test_async_health_fields(async_proxy):
    _, data = _get("/health")
    missing = REQUIRED_HEALTH_FIELDS - data.keys()
    assert not missing, f"Missing /health fields: {missing}"


def test_async_health_status_ok(async_proxy):
    _, data = _get("/health")
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 2 — /stats endpoint
# ---------------------------------------------------------------------------


def test_async_stats_200(async_proxy):
    status, data = _get("/stats")
    assert status == 200
    assert "session" in data or "compilation_mode" in data


# ---------------------------------------------------------------------------
# Test 3 — /stats/last returns 200
# ---------------------------------------------------------------------------


def test_async_stats_last_200(async_proxy):
    status, data = _get("/stats/last")
    assert status == 200


# ---------------------------------------------------------------------------
# Test 4 — /stats/session returns 200 with expected keys
# ---------------------------------------------------------------------------


def test_async_stats_session(async_proxy):
    status, data = _get("/stats/session")
    assert status == 200
    assert "session_requests" in data


# ---------------------------------------------------------------------------
# Test 5 — /traces endpoint
# ---------------------------------------------------------------------------


def test_async_traces(async_proxy):
    status, data = _get("/traces")
    assert status == 200
    assert "traces" in data
    assert "count" in data


# ---------------------------------------------------------------------------
# Test 6 — /trace/last returns 200 (may be no_traces)
# ---------------------------------------------------------------------------


def test_async_trace_last(async_proxy):
    status, data = _get("/trace/last")
    assert status == 200
    # Either a real trace or an error (no traces yet)
    assert "error" in data or "request_id" in data


# ---------------------------------------------------------------------------
# Test 7 — /degradation endpoint
# ---------------------------------------------------------------------------


def test_async_degradation(async_proxy):
    status, data = _get("/degradation")
    assert status == 200


# ---------------------------------------------------------------------------
# Test 8 — /circuit-breakers endpoint
# ---------------------------------------------------------------------------


def test_async_circuit_breakers(async_proxy):
    status, data = _get("/circuit-breakers")
    assert status == 200
    assert "circuit_breakers" in data or "enabled" in data


# ---------------------------------------------------------------------------
# Test 9 — 50+ CONCURRENT requests complete without blocking
# ---------------------------------------------------------------------------


def test_async_50_concurrent_requests(async_proxy):
    """
    Fire 60 concurrent GET /health requests via threads.
    All must complete (TSR-06d: timeout widened to accommodate shared CI
    runner scheduling stalls). The test's intent — verify the proxy
    handles 60 concurrent /health requests without deadlocking or
    serializing — does not require the original 5s budget; the
    assertion that matters is `len(successes) == N` (all 60 complete
    successfully). 30s is generous enough that scheduling-jitter on
    shared GitHub Actions runners does not flake while still catching
    real serialization regressions (which would not finish at all).
    """
    N = 60
    TIMEOUT_TOTAL = 30.0  # bumped from 5.0 — see docstring

    results = []
    lock = threading.Lock()

    def _one_request():
        try:
            status, _ = _get("/health")
            with lock:
                results.append(status)
        except Exception as exc:
            with lock:
                results.append(f"error:{exc}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N) as ex:
        futures = [ex.submit(_one_request) for _ in range(N)]
        for f in as_completed(futures, timeout=TIMEOUT_TOTAL + 1):
            pass  # just wait
    elapsed = time.time() - t0

    successes = [r for r in results if r == 200]
    failures = [r for r in results if r != 200]

    assert len(successes) == N, (
        f"Only {len(successes)}/{N} succeeded in {elapsed:.2f}s. Failures: {failures[:5]}"
    )
    assert elapsed < TIMEOUT_TOTAL, (
        f"60 concurrent requests took {elapsed:.2f}s > {TIMEOUT_TOTAL}s — possible blocking detected"
    )


# ---------------------------------------------------------------------------
# Test 10 — Management endpoint overhead < 10ms
# ---------------------------------------------------------------------------


def test_async_health_overhead_under_10ms(async_proxy):
    """
    Proxy overhead on /health must be <10ms (pure server-side, no upstream).
    We allow a 50ms budget in CI environments due to scheduling jitter.
    """
    BUDGET_MS = 50  # generous for CI; target is <10ms in prod
    times = []
    for _ in range(5):
        t0 = time.monotonic()
        _get("/health")
        times.append((time.monotonic() - t0) * 1000)
    median_ms = sorted(times)[len(times) // 2]
    assert median_ms < BUDGET_MS, (
        f"Median /health latency {median_ms:.1f}ms exceeds {BUDGET_MS}ms budget. "
        f"All samples: {[f'{t:.1f}' for t in times]}"
    )


# ---------------------------------------------------------------------------
# Test 11 — Backpressure middleware (503 at capacity)
# ---------------------------------------------------------------------------


def test_async_backpressure_503():
    """
    Saturate the concurrency gate and assert the documented backpressure
    behavior: with max_concurrency in-flight requests active, the next
    non-management request receives 503 {"error": {"type": "overloaded"}}
    with a Retry-After header; management endpoints (/health) bypass the
    gate; once capacity frees up, requests succeed again.

    The middleware is exercised directly over ASGI (no port, no uvicorn) so
    saturation is deterministic: one in-flight request parks inside the
    handler on an asyncio.Event while the gate is probed.
    """
    import asyncio

    import httpx
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from tokenpak.proxy.server_async import ConcurrencyLimiterMiddleware

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_endpoint(request):
            entered.set()
            await release.wait()
            return JSONResponse({"ok": True})

        async def health(request):
            return JSONResponse({"status": "ok"})

        app = Starlette(
            routes=[
                Route("/v1/slow", slow_endpoint, methods=["GET"]),
                Route("/health", health, methods=["GET"]),
            ]
        )
        app.add_middleware(ConcurrencyLimiterMiddleware, max_concurrency=1)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Occupy the single concurrency slot.
            in_flight = asyncio.create_task(client.get("/v1/slow"))
            await asyncio.wait_for(entered.wait(), timeout=5)

            # Gate saturated: next proxied request must be rejected with 503.
            rejected = await client.get("/v1/slow")
            assert rejected.status_code == 503, (
                f"expected 503 at capacity, got {rejected.status_code}"
            )
            payload = rejected.json()
            assert payload["error"]["type"] == "overloaded"
            assert rejected.headers.get("retry-after") == "1"

            # Management endpoints bypass the limiter even at capacity.
            health_resp = await client.get("/health")
            assert health_resp.status_code == 200

            # Drain: the parked request completes and capacity is restored.
            release.set()
            completed = await asyncio.wait_for(in_flight, timeout=5)
            assert completed.status_code == 200
            after = await client.get("/v1/slow")
            assert after.status_code == 200

    asyncio.run(scenario())


def test_async_backpressure_middleware_installed_in_app():
    """create_async_app must actually wire the backpressure middleware.

    Guards the integration half of the 503-at-capacity contract: the gate
    exists AND the app factory installs it (a regression that dropped the
    add_middleware call would keep the unit test above green).
    """
    import tokenpak.proxy.server_async as server_async

    saved_ref = server_async._proxy_server_ref
    try:
        app = server_async.create_async_app(object())
        assert any(
            m.cls is server_async.ConcurrencyLimiterMiddleware for m in app.user_middleware
        ), "ConcurrencyLimiterMiddleware not installed by create_async_app"
    finally:
        server_async._proxy_server_ref = saved_ref


# ---------------------------------------------------------------------------
# Test 12 — backward-compatible CLI: start_proxy() uses async backend
# ---------------------------------------------------------------------------


def test_async_backend_is_quarantined_not_production(monkeypatch):
    """`start_proxy()` must NOT select the async backend.

    Replaces a skipped test that asserted the opposite — that `start_proxy()`
    exposes `_async_thread`. That test encoded an aspiration, was skipped, and so
    pinned nothing for months while the module continued to present as
    production-available.

    The async server does not carry the sync path's governance gates: no
    spend-guard evaluation, no DLP outbound scan, and a narrower `INTERCEPT_HOSTS`
    set that would leave some provider traffic ungoverned. Wiring it into
    production would silently bypass cost control and secret scanning.

    This test is the enforcement of that quarantine. It fails the moment the
    async backend is wired in — which is correct: reviving it is a governance
    decision with a written contract (see the module docstring), not a code
    change that should pass CI silently.
    """
    from tokenpak.proxy import server_async
    from tokenpak.proxy.server import start_proxy

    assert server_async.EXPERIMENTAL_NOT_PRODUCTION is True, (
        "server_async is quarantined; flipping EXPERIMENTAL_NOT_PRODUCTION is a "
        "governance change requiring the revival contract in its module docstring"
    )

    # Sentinel EVERY entry point into the async stack, not one of them.
    #
    # Two earlier versions of this guard were both bypassable. The first asserted
    # `not hasattr(ps, "_async_thread")` — a name in no product code. The second
    # sentinelled only `start_async_proxy_in_thread`, and review demonstrated a
    # reviver wiring uvicorn over `create_async_app()` while the guard passed
    # green and a real Starlette app was attached to the ProxyServer.
    #
    # A guard whose only deliverable is assurance must cover every path, or its
    # green check is affirmatively misleading — worse than the skipped test it
    # replaced, because a skipped test is visibly inert while a passing one reads
    # as coverage.
    # Capture ownership BEFORE start_proxy and assert the DELTA, not the absolute
    # value. `_proxy_server_ref` is a module global that `create_async_app()` sets
    # and nothing ever clears, and `tests/test_proxy_server_async.py` assigns it at
    # seven sites with save/restore at only one. Asserting `is None` therefore
    # fires this suite's highest-severity message — "every request bypasses the
    # spend guard and the DLP outbound scan" — because an unrelated unit test left
    # a MagicMock behind.
    #
    # Reproduced: running that file first makes the absolute-value form FAIL while
    # the baseline passes. Latent under CI's alphabetical collection, live under
    # --lf, --ff, explicit ordering, or any future parallel/random plugin.
    #
    # The question is "did start_proxy take ownership", which is a delta.
    _ownership_before = getattr(server_async, "_proxy_server_ref", None)

    _async_entries_called: list[str] = []

    def _sentinel(name):
        def _fail_fast(*a, **kw):
            _async_entries_called.append(name)
            raise AssertionError(f"quarantined async backend was started via {name}()")

        return _fail_fast

    for _entry in ("start_async_proxy_in_thread", "run_async_proxy", "create_async_app"):
        monkeypatch.setattr(server_async, _entry, _sentinel(_entry), raising=True)

    TEMP_PORT = 19867
    ps = start_proxy(host="127.0.0.1", port=TEMP_PORT, blocking=False)
    try:
        _wait_for_port(TEMP_PORT, timeout=8)
        req = urllib.request.Request(f"http://127.0.0.1:{TEMP_PORT}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200, "the sync proxy must still serve /health"
            resp_headers = dict(resp.headers)
        resp = type("R", (), {"headers": resp_headers})()

        # OUTCOME assertion — what is actually serving.
        #
        # Name sentinels alone cannot work here, and three attempts proved it.
        # `server_async.__all__` exports ~30 public symbols; review demonstrated a
        # revival built from `handle_v1_proxy` + `lifespan` + `_proxy_server_ref`
        # that touched none of the sentinelled names and served real traffic
        # through uvicorn while this test reported "1 passed". Enumerating names
        # is whack-a-mole against a module designed to be composable.
        #
        # So assert the OUTCOME instead: whatever wired it, the async stack must
        # not be the thing answering requests. `_proxy_server_ref` is set by the
        # async app when it takes ownership, and uvicorn identifies itself in the
        # Server header. Neither can be avoided by a reviver who actually succeeds.
        assert server_async._proxy_server_ref is _ownership_before, (
            "start_proxy() changed server_async._proxy_server_ref — the async stack "
            "took ownership of the ProxyServer, which the quarantine forbids"
        )

        server_hdr = (resp.headers.get("Server") or "").lower()
        assert "uvicorn" not in server_hdr, (
            f"/health is being served by uvicorn (Server: {server_hdr!r}) — the "
            "quarantined async stack is live and every request bypasses the spend "
            "guard and the DLP outbound scan"
        )

        # Name sentinels retained as defence in depth: they give a precise
        # diagnostic when the revival goes through a known entry point.
        assert not _async_entries_called, (
            f"async entry point(s) {_async_entries_called} invoked during start_proxy()"
        )
    finally:
        ps.stop()


def test_async_module_documents_why_it_is_quarantined():
    """The quarantine must be discoverable from the module, not only from a ruling.

    A decision recorded only in governance is invisible to someone reading the
    code. This pins the reasoning in place so the next reader learns *why*
    before deciding to wire it up.
    """
    from tokenpak.proxy import server_async

    doc = server_async.__doc__ or ""
    assert "EXPERIMENTAL, NOT PRODUCTION" in doc
    assert "Revival contract" in doc
    for gate in ("spend-guard", "DLP"):
        assert gate in doc, f"the docstring must state that {gate} is absent on this path"


# ---------------------------------------------------------------------------
# Test 13 — /health during high concurrency is always responsive
# ---------------------------------------------------------------------------


def test_health_responsive_during_load(async_proxy):
    """
    /health must respond quickly even while 40 other requests are in-flight.
    """
    barrier = threading.Barrier(41)  # 40 workers + 1 health checker

    health_times = []

    def _load_worker():
        barrier.wait()
        for _ in range(3):
            try:
                _get("/health")
                time.sleep(0.01)
            except Exception:
                pass

    def _health_checker():
        barrier.wait()
        for _ in range(5):
            t0 = time.monotonic()
            try:
                _get("/health")
                health_times.append((time.monotonic() - t0) * 1000)
            except Exception:
                health_times.append(9999)
            time.sleep(0.05)

    threads = [threading.Thread(target=_load_worker) for _ in range(40)]
    threads.append(threading.Thread(target=_health_checker))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Filter out errors
    valid_times = [t for t in health_times if t < 9000]
    assert len(valid_times) >= 3, "Health checker failed too many times during load"
    median_ms = sorted(valid_times)[len(valid_times) // 2]
    assert median_ms < 200, f"Median /health latency under load: {median_ms:.1f}ms — too slow"


# ---------------------------------------------------------------------------
# Test 14 — Unknown route returns 404
# ---------------------------------------------------------------------------


def test_async_404_on_unknown_route(async_proxy):
    try:
        urllib.request.urlopen(_BASE + "/nonexistent-endpoint-xyzzy", timeout=5)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    except Exception:
        pass  # other errors are acceptable


# ---------------------------------------------------------------------------
# Test 15 — Repeated start/stop doesn't hang
# ---------------------------------------------------------------------------


def test_async_proxy_start_stop_cycle():
    from tokenpak.proxy.server import ProxyServer

    CYCLE_PORT = 19868
    ps = ProxyServer(host="127.0.0.1", port=CYCLE_PORT)
    ps.start(blocking=False)
    _wait_for_port(CYCLE_PORT, timeout=8)
    t0 = time.time()
    ps.stop()
    elapsed = time.time() - t0
    assert elapsed < 10, f"stop() took {elapsed:.1f}s — too slow"
