# SPDX-License-Identifier: Apache-2.0
"""Deterministic tests for the managed background-agent concurrency gate.

Every test drives the gate directly with threads + barriers — no real
sockets, no provider calls, no sleeps in the assertions path. This proves the
packet's local-only guarantees: bounded in-flight cap, FIFO queue ordering,
bounded queue with a structured overflow, degraded-mode serialization, and
config/env resolution.
"""

from __future__ import annotations

import json
import threading

import pytest

from tokenpak.proxy.admission import (
    ADMITTED,
    DEFAULT_MAX_PARALLEL,
    QUEUE_FULL,
    WAIT_TIMEOUT,
    AgentConcurrencyGate,
    build_busy_response,
    resolve_agent_concurrency,
)

# ---------------------------------------------------------------------------
# Config / env resolution
# ---------------------------------------------------------------------------


def test_config_default_is_two(monkeypatch):
    monkeypatch.delenv("TOKENPAK_LOCAL_AGENT_CONCURRENCY", raising=False)
    monkeypatch.setattr(
        "tokenpak.proxy.admission._config_max_parallel", lambda: DEFAULT_MAX_PARALLEL
    )
    cap, source = resolve_agent_concurrency()
    assert cap == 2
    assert source == "config"


def test_env_off_disables_gate(monkeypatch):
    monkeypatch.setenv("TOKENPAK_LOCAL_AGENT_CONCURRENCY", "off")
    cap, source = resolve_agent_concurrency()
    assert cap is None
    assert source == "env:off"


def test_env_numeric_override(monkeypatch):
    monkeypatch.setenv("TOKENPAK_LOCAL_AGENT_CONCURRENCY", "1")
    cap, source = resolve_agent_concurrency()
    assert cap == 1
    assert source == "env"


def test_env_auto_defers_to_config(monkeypatch):
    monkeypatch.setenv("TOKENPAK_LOCAL_AGENT_CONCURRENCY", "auto")
    monkeypatch.setattr("tokenpak.proxy.admission._config_max_parallel", lambda: 3)
    cap, source = resolve_agent_concurrency()
    assert cap == 3
    assert source == "env:auto"


def test_invalid_env_falls_back_never_unlimited(monkeypatch):
    monkeypatch.setenv("TOKENPAK_LOCAL_AGENT_CONCURRENCY", "banana")
    monkeypatch.setattr(
        "tokenpak.proxy.admission._config_max_parallel", lambda: DEFAULT_MAX_PARALLEL
    )
    cap, source = resolve_agent_concurrency()
    assert cap == 2  # falls back to config default, not None/unlimited


@pytest.mark.parametrize("bad", ["0", "-4"])
def test_nonpositive_env_falls_back(monkeypatch, bad):
    monkeypatch.setenv("TOKENPAK_LOCAL_AGENT_CONCURRENCY", bad)
    monkeypatch.setattr("tokenpak.proxy.admission._config_max_parallel", lambda: 2)
    cap, _ = resolve_agent_concurrency()
    assert cap == 2


def test_invalid_config_value_warns_and_falls_back(monkeypatch):
    from tokenpak.proxy import admission

    monkeypatch.setattr(admission, "_cfg_absent_marker", None, raising=False)
    monkeypatch.setattr(
        "tokenpak.core.config_loader.get", lambda *a, **k: "not-a-number"
    )
    assert admission._config_max_parallel() == DEFAULT_MAX_PARALLEL


# ---------------------------------------------------------------------------
# In-flight cap + FIFO queue ordering
# ---------------------------------------------------------------------------


def test_burst_never_exceeds_cap():
    """A burst of managed acquires never runs more than `cap` concurrently."""
    gate = AgentConcurrencyGate(2, max_queue=10)
    peak = 0
    current = 0
    lock = threading.Lock()
    start = threading.Barrier(6)
    release_all = threading.Event()

    def worker():
        nonlocal peak, current
        start.wait()
        assert gate.acquire(wait_timeout=5) == ADMITTED
        with lock:
            current += 1
            peak = max(peak, current)
        release_all.wait(5)
        with lock:
            current -= 1
        gate.release()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    # Give queued workers time to (not) breach the cap, then release.
    # (The barrier above already rendezvous-synchronizes the 6 workers;
    # the main thread does not wait on it — it only has 6 parties.)
    threading.Event().wait(0.3)
    assert peak <= 2
    release_all.set()
    for t in threads:
        t.join(5)
    assert peak == 2  # cap was actually reached, not just never breached


def test_queue_is_fifo_third_waits_for_first_release():
    """Subagent 3 starts only after subagent 1 or 2 releases a slot."""
    gate = AgentConcurrencyGate(2, max_queue=10)
    admit_order = []
    lock = threading.Lock()

    # Occupy both slots.
    assert gate.acquire() == ADMITTED
    assert gate.acquire() == ADMITTED

    def waiter(idx):
        assert gate.acquire(wait_timeout=5) == ADMITTED
        with lock:
            admit_order.append(idx)
        gate.release()

    # Start each waiter and let it fully enter the FIFO queue before the next
    # starts — the gate's queue order is arrival order, so this fixes 0,1,2
    # deterministically (no barrier: a barrier would release them to race).
    waiters = [threading.Thread(target=waiter, args=(i,)) for i in range(3)]
    for t in waiters:
        t.start()
        threading.Event().wait(0.1)  # waiter reaches self._queue.append(ticket)
    with lock:
        assert admit_order == []  # nobody admitted while both slots held
    gate.release()  # frees one slot -> waiter 0 (head of FIFO)
    threading.Event().wait(0.1)
    with lock:
        assert admit_order[:1] == [0]
    gate.release()  # frees the second original slot -> waiter 1
    for t in waiters:
        t.join(5)
    assert admit_order == [0, 1, 2]  # strict FIFO


# ---------------------------------------------------------------------------
# Bounded queue overflow + structured busy response
# ---------------------------------------------------------------------------


def test_queue_full_rejects_when_bound_exceeded():
    gate = AgentConcurrencyGate(1, max_queue=1)
    assert gate.acquire() == ADMITTED  # slot taken
    # One waiter allowed in the queue; a second must be rejected fast.
    enqueued = threading.Event()

    def waiter():
        enqueued.set()
        gate.acquire(wait_timeout=5)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    enqueued.wait(1)
    threading.Event().wait(0.1)  # let the waiter actually enter the queue
    assert gate.acquire(wait_timeout=0.01) == QUEUE_FULL
    snap = gate.snapshot()
    assert snap["rejected_queue_full"] == 1
    gate.release()
    t.join(5)


def test_wait_timeout_returns_structured_reason():
    gate = AgentConcurrencyGate(1, max_queue=5)
    assert gate.acquire() == ADMITTED
    assert gate.acquire(wait_timeout=0.05) == WAIT_TIMEOUT
    assert gate.snapshot()["rejected_wait_timeout"] == 1


@pytest.mark.parametrize("reason", [QUEUE_FULL, WAIT_TIMEOUT])
def test_busy_response_is_complete_json(reason):
    raw = build_busy_response(reason)
    head, sep, body = raw.partition(b"\r\n\r\n")
    assert sep == b"\r\n\r\n"
    status_line, *header_lines = head.split(b"\r\n")
    assert status_line == b"HTTP/1.1 503 Service Unavailable"
    headers = {}
    for line in header_lines:
        name, _, value = line.partition(b":")
        headers[name.strip().lower()] = value.strip()
    assert int(headers[b"content-length"]) == len(body)  # exact framing
    assert headers[b"content-type"] == b"application/json"
    assert headers[b"connection"] == b"close"
    assert b"retry-after" in headers
    payload = json.loads(body.decode("utf-8"))  # complete, parseable JSON
    assert payload["error"]["type"] == "local_agent_concurrency_busy"
    assert payload["error"]["reason"] == reason


# ---------------------------------------------------------------------------
# Degraded-mode dynamic cap
# ---------------------------------------------------------------------------


def test_degraded_probe_serializes_to_one():
    degraded = {"on": False}
    gate = AgentConcurrencyGate(2, max_queue=5, degraded_probe=lambda: degraded["on"])
    assert gate.effective_cap() == 2
    degraded["on"] = True
    assert gate.effective_cap() == 1
    assert gate.acquire() == ADMITTED
    # Second concurrent acquire blocks because the degraded cap is 1.
    assert gate.acquire(wait_timeout=0.05) == WAIT_TIMEOUT
    snap = gate.snapshot()
    assert snap["effective_cap"] == 1
    assert snap["degraded_serial"] is True
    gate.release()


def test_degraded_probe_failure_is_treated_as_healthy():
    def boom():
        raise RuntimeError("probe exploded")

    gate = AgentConcurrencyGate(2, max_queue=5, degraded_probe=boom)
    # A broken probe must never wedge admission to serial forever.
    assert gate.effective_cap() == 2


# ---------------------------------------------------------------------------
# Observability snapshot
# ---------------------------------------------------------------------------


def test_snapshot_exposes_required_fields():
    gate = AgentConcurrencyGate(2, max_queue=5, source="config")
    snap = gate.snapshot()
    for key in (
        "enabled",
        "max_parallel_subagents",
        "effective_cap",
        "in_flight",
        "queued",
        "queue_depth_max",
        "rejected_queue_full",
        "rejected_wait_timeout",
        "source",
    ):
        assert key in snap
    assert snap["enabled"] is True
    assert snap["max_parallel_subagents"] == 2
