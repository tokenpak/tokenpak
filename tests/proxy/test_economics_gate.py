"""Session-economics evaluations are bounded and shared.

Companion status lines poll ``/v1/messages/session-economics`` every two
seconds with a 0.5 s client timeout. Before the gate, every request ran its own
evaluation on its own thread to completion, so a slow ledger read turned into
an unbounded pile of request threads and open ledger connections.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.proxy._proxy_subprocess import free_port
from tokenpak.proxy import server as server_module
from tokenpak.proxy.server import ProxyServer

# ---------------------------------------------------------------------------
# EconomicsGate unit behaviour
# ---------------------------------------------------------------------------


def _gate(max_concurrent: int = 2, wait_s: float = 0.3):
    from tokenpak.proxy.economics_gate import EconomicsGate

    return EconomicsGate(max_concurrent=max_concurrent, wait_s=wait_s)


def test_identical_concurrent_requests_share_one_evaluation() -> None:
    gate = _gate(wait_s=2.0)
    calls = []
    release = threading.Event()

    def compute() -> str:
        calls.append(1)
        release.wait(2.0)
        return "economics"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(gate.run, ("s1", "", None, "db"), compute) for _ in range(8)]
        time.sleep(0.2)
        release.set()
        results = [f.result() for f in futures]
    assert results == ["economics"] * 8
    assert len(calls) == 1
    assert gate.in_flight() == 0


def test_distinct_evaluations_are_capped_and_excess_fails_fast() -> None:
    from tokenpak.proxy.economics_gate import EconomicsBusy

    gate = _gate(max_concurrent=1, wait_s=0.2)
    started = threading.Event()
    release = threading.Event()

    def slow() -> str:
        started.set()
        release.wait(5.0)
        return "first"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(gate.run, "a", slow)
        assert started.wait(2.0)
        t0 = time.monotonic()
        with pytest.raises(EconomicsBusy):
            gate.run("b", lambda: "second")
        assert time.monotonic() - t0 < 1.0
        release.set()
        assert first.result() == "first"
    # The slot is free again once the evaluation finished.
    assert gate.run("b", lambda: "second") == "second"


def test_a_failed_evaluation_is_not_shared_as_a_result() -> None:
    from tokenpak.proxy.economics_gate import EconomicsBusy

    gate = _gate(wait_s=1.0)
    entered = threading.Event()

    def boom() -> str:
        entered.set()
        time.sleep(0.2)
        raise RuntimeError("ledger unreadable")

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(gate.run, "k", boom)
        assert entered.wait(2.0)
        follower = pool.submit(gate.run, "k", lambda: "never")
        with pytest.raises(RuntimeError):
            leader.result()
        with pytest.raises(EconomicsBusy):
            follower.result()
    assert gate.run("k", lambda: "fresh") == "fresh"


def test_gate_configuration_has_floors(monkeypatch) -> None:
    from tokenpak.proxy import economics_gate

    monkeypatch.setattr(economics_gate, "_GATE", None)
    monkeypatch.setenv("TOKENPAK_ECONOMICS_MAX_CONCURRENT", "0")
    monkeypatch.setenv("TOKENPAK_ECONOMICS_WAIT_S", "not-a-number")
    gate = economics_gate.get_economics_gate()
    assert gate.max_concurrent == 1
    assert gate.wait_s == economics_gate.DEFAULT_WAIT_S


# ---------------------------------------------------------------------------
# HTTP endpoint: bounded concurrency through a real in-process proxy
# ---------------------------------------------------------------------------


def test_endpoint_bounds_concurrent_evaluations(tmp_path, monkeypatch) -> None:
    db = tmp_path / "monitor.db"
    from tokenpak.proxy.monitor import Monitor

    Monitor(str(db))
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")
    monkeypatch.setenv("TOKENPAK_ECONOMICS_MAX_CONCURRENT", "2")
    monkeypatch.setenv("TOKENPAK_ECONOMICS_WAIT_S", "0.3")
    try:
        from tokenpak.proxy import economics_gate

        monkeypatch.setattr(economics_gate, "_GATE", None)
    except ImportError:  # pre-gate code: nothing to reset
        pass

    active = 0
    peak = 0
    evaluations = 0
    lock = threading.Lock()

    def slow_evaluation(session_id, db_path, **_kwargs):
        nonlocal active, peak, evaluations
        with lock:
            active += 1
            evaluations += 1
            peak = max(peak, active)
        try:
            time.sleep(1.5)
            from tokenpak.proxy.session_forecast import _build_session_economics

            return _build_session_economics(session_id, monitor_db_path=str(db_path))
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(
        "tokenpak.proxy.forecast_endpoint._build_session_economics_response", slow_evaluation
    )
    http_server_type = server_module._ThreadedHTTPServer

    def bind_ephemeral(address, handler):
        return http_server_type((address[0], 0), handler)

    monkeypatch.setattr(server_module, "_ThreadedHTTPServer", bind_ephemeral)
    proxy = ProxyServer(host="127.0.0.1", port=free_port())
    proxy.start(blocking=False)
    assert proxy._server is not None
    port = int(proxy._server.server_address[1])

    def poll(session: str) -> tuple[int, float]:
        body = json.dumps({"session_id": session}).encode()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        t0 = time.monotonic()
        try:
            conn.request(
                "POST",
                "/v1/messages/session-economics",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            resp.read()
            return resp.status, time.monotonic() - t0
        finally:
            conn.close()

    try:
        # Six status lines polling the same session, plus six other sessions.
        sessions = ["shared"] * 6 + [f"other-{i}" for i in range(6)]
        with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
            outcomes = list(pool.map(poll, sessions))
    finally:
        proxy.stop()

    statuses = [s for s, _ in outcomes]
    assert peak <= 2, f"{peak} evaluations ran at once"
    assert evaluations <= 3, f"{evaluations} evaluations for {len(sessions)} requests"
    assert set(statuses) <= {200, 503}
    assert statuses.count(200) >= 2
    # A request that could not start an evaluation returned promptly
    # instead of holding its thread for one.
    rejected = [elapsed for status, elapsed in outcomes if status == 503]
    assert rejected and max(rejected) < 1.4


# ---------------------------------------------------------------------------
# Abandoned requests
# ---------------------------------------------------------------------------


def test_client_disconnected_detects_a_closed_peer() -> None:
    import socket

    from tokenpak.proxy.economics_gate import client_disconnected

    ours, theirs = socket.socketpair()
    try:
        assert not client_disconnected(ours)
        theirs.sendall(b"x")
        assert not client_disconnected(ours)  # unread data is not a hang-up
        assert ours.recv(1) == b"x"
        theirs.close()
        assert client_disconnected(ours)
    finally:
        ours.close()


def test_client_disconnects_are_not_logged_as_tracebacks(capsys) -> None:
    proxy_server = server_module._ThreadedHTTPServer.__new__(server_module._ThreadedHTTPServer)
    try:
        raise BrokenPipeError(32, "Broken pipe")
    except BrokenPipeError:
        proxy_server.handle_error(None, ("127.0.0.1", 50000))
    assert "Traceback" not in capsys.readouterr().err
    try:
        raise ValueError("a real handler bug")
    except ValueError:
        proxy_server.handle_error(None, ("127.0.0.1", 50000))
    assert "ValueError: a real handler bug" in capsys.readouterr().err
