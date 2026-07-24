"""
Regression tests for the per-(provider, session) semaphore eviction race

Acquisition is fetch-semaphore -> acquire -> increment (not atomic).
Releasing used to evict the semaphore object the moment the in-flight
counter hit zero, so a thread that had already fetched the object could
acquire the orphaned semaphore while the next request minted a fresh one
for the same key — doubling the effective concurrency cap.

Pinned behavior:
- the semaphore OBJECT survives the counter returning to zero
- zero-count entries are evicted only after the idle window
- in-flight count never exceeds the cap under concurrent churn
- the /health snapshot omits lingering zero-count entries
"""

from __future__ import annotations

import threading
import time

import pytest

from tokenpak.proxy import server as srv


@pytest.fixture(autouse=True)
def _clean_semaphore_state():
    with srv._upstream_sem_lock:
        srv._upstream_semaphores.clear()
        srv._upstream_inflight.clear()
        srv._upstream_sem_last_activity.clear()
    yield
    with srv._upstream_sem_lock:
        srv._upstream_semaphores.clear()
        srv._upstream_inflight.clear()
        srv._upstream_sem_last_activity.clear()


def test_semaphore_object_survives_release_to_zero():
    sem1 = srv._get_upstream_semaphore("prov", "sess")
    srv._upstream_inflight_delta("prov", +1, "sess")
    srv._upstream_inflight_delta("prov", -1, "sess")  # count -> 0

    sem2 = srv._get_upstream_semaphore("prov", "sess")
    assert sem2 is sem1, (
        "release-to-zero must not evict the semaphore object — a waiter that "
        "already fetched it would gate on an orphan while a fresh semaphore "
        "is minted, allowing 2x the concurrency cap"
    )


def test_idle_zero_entries_evicted_after_window(monkeypatch):
    monkeypatch.setattr(srv, "_UPSTREAM_SEM_IDLE_EVICT_SECONDS", 0.05)
    srv._get_upstream_semaphore("prov", "sess-idle")
    srv._upstream_inflight_delta("prov", +1, "sess-idle")
    srv._upstream_inflight_delta("prov", -1, "sess-idle")
    key = ("prov", "sess-idle")
    assert key in srv._upstream_semaphores  # not evicted immediately

    time.sleep(0.08)
    # Any release-to-zero sweeps idle entries.
    srv._get_upstream_semaphore("prov", "sess-trigger")
    srv._upstream_inflight_delta("prov", +1, "sess-trigger")
    srv._upstream_inflight_delta("prov", -1, "sess-trigger")

    assert key not in srv._upstream_semaphores
    assert key not in srv._upstream_inflight
    assert key not in srv._upstream_sem_last_activity


def test_active_entries_survive_the_idle_sweep(monkeypatch):
    monkeypatch.setattr(srv, "_UPSTREAM_SEM_IDLE_EVICT_SECONDS", 0.01)
    sem = srv._get_upstream_semaphore("prov", "sess-busy")
    srv._upstream_inflight_delta("prov", +1, "sess-busy")  # in flight
    time.sleep(0.03)
    # Backdate the activity stamp: even a stale-looking entry must survive
    # while its count is non-zero.
    with srv._upstream_sem_lock:
        srv._upstream_sem_last_activity[("prov", "sess-busy")] = 0.0
    srv._get_upstream_semaphore("prov", "other")
    srv._upstream_inflight_delta("prov", +1, "other")
    srv._upstream_inflight_delta("prov", -1, "other")  # triggers sweep

    assert srv._upstream_semaphores.get(("prov", "sess-busy")) is sem
    assert srv._upstream_inflight[("prov", "sess-busy")] == 1
    srv._upstream_inflight_delta("prov", -1, "sess-busy")


def test_inflight_never_exceeds_cap_under_churn(monkeypatch):
    """Loop acquire/release across threads; the observed in-flight count must
    never exceed the cap. With eviction-at-zero this raced to ~2x the cap."""
    cap = 2
    monkeypatch.setattr(srv, "_UPSTREAM_CONCURRENCY", cap)
    max_seen = 0
    seen_lock = threading.Lock()
    deadline = time.time() + 2.0

    def worker():
        nonlocal max_seen
        while time.time() < deadline:
            sem = srv._get_upstream_semaphore("prov", "sess")
            if not sem.acquire(timeout=1):
                continue
            try:
                n = srv._upstream_inflight_delta("prov", +1, "sess")
                with seen_lock:
                    max_seen = max(max_seen, n)
            finally:
                # Decrement BEFORE releasing the slot so the counter is a
                # faithful gauge of concurrent holders. (Production decrements
                # after release, which can transiently overshoot the telemetry
                # counter — benign there, but it would make this assertion
                # measure the wrong thing.)
                srv._upstream_inflight_delta("prov", -1, "sess")
                sem.release()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert 0 < max_seen <= cap, f"in-flight count reached {max_seen}, exceeding the cap of {cap}"


def test_snapshot_omits_lingering_zero_count_entries():
    srv._get_upstream_semaphore("prov", "sess")
    srv._upstream_inflight_delta("prov", +1, "sess")
    assert srv.get_upstream_inflight_snapshot() == {"prov::sess": 1}

    srv._upstream_inflight_delta("prov", -1, "sess")
    # Entry still exists (eviction deferred) but reports no in-flight work.
    assert ("prov", "sess") in srv._upstream_semaphores
    assert srv.get_upstream_inflight_snapshot() == {}
