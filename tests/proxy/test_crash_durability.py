"""SIGKILL durability regression: the request ledger must survive a hard crash.

Starts a real ProxyServer subprocess against the canned stub upstream,
drives a first batch of requests to a *confirmed-committed* state, then
SIGKILLs the process in the middle of a second burst and asserts:

  1. monitor.db reopens and passes PRAGMA integrity_check ("ok") after
     WAL/journal recovery — a kill -9 must never corrupt the ledger.
  2. Every row committed before the crash (phase-1 rows) is still there —
     committed data survives.
  3. The recovered row count never exceeds the number of requests actually
     sent — recovery must not invent rows.
  4. Recovered rows are well-formed (model + endpoint populated, status 200).

Documented semantics being pinned: ledger writes go through an async
in-process write queue (enqueue-then-commit). Durability is therefore
*at-most-acknowledged*: a response can be acked to the client shortly
before its row is committed, so rows_committed <= requests_acked at the
moment of a crash. Anything stronger (rows == acked at kill time) would
require synchronous commits and is intentionally NOT asserted here; if
that guarantee is ever introduced, tighten this test alongside it.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from tests.proxy._proxy_subprocess import ProxyProc

pytestmark = [pytest.mark.needs_proxy, pytest.mark.timeout(120)]

PHASE1_REQUESTS = 4
PHASE2_REQUESTS = 8


def test_sigkill_mid_burst_ledger_recovers(stub_upstream):
    proxy = ProxyProc(f"http://127.0.0.1:{stub_upstream.server_port}")
    try:
        proxy.wait_ready()

        # ---- Phase 1: reach a known-committed baseline -------------------
        for i in range(PHASE1_REQUESTS):
            status, _, _ = proxy.post_message(f"phase1-{i}")
            assert status == 200
        committed_before_kill = proxy.wait_row_count(PHASE1_REQUESTS)
        assert committed_before_kill == PHASE1_REQUESTS, (
            f"phase-1 rows never reached the ledger "
            f"({committed_before_kill}/{PHASE1_REQUESTS}); cannot test durability"
        )

        # ---- Phase 2: burst + SIGKILL mid-flight --------------------------
        acked = []
        failed = []
        lock = threading.Lock()
        first_ack = threading.Event()

        def one(i: int) -> None:
            try:
                status, _, _ = proxy.post_message(f"phase2-{i}", timeout=30)
                with lock:
                    if status == 200:
                        acked.append(i)
                first_ack.set()
            except Exception:
                # Expected for requests in flight when the process dies.
                with lock:
                    failed.append(i)
                first_ack.set()

        threads = [threading.Thread(target=one, args=(i,)) for i in range(PHASE2_REQUESTS)]
        for t in threads:
            t.start()
        # Kill as soon as the burst is demonstrably in progress: at least one
        # request resolved while others are still in flight.
        first_ack.wait(timeout=30)
        proxy.sigkill()
        for t in threads:
            t.join(timeout=30)

        total_sent = PHASE1_REQUESTS + PHASE2_REQUESTS
        total_acked = PHASE1_REQUESTS + len(acked)

        # ---- Recovery assertions ------------------------------------------
        conn = sqlite3.connect(str(proxy.db_path), timeout=10)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok", (
                f"monitor.db failed integrity_check after SIGKILL: {integrity}"
            )
            rows = conn.execute("SELECT model, status_code, endpoint FROM requests").fetchall()
        finally:
            conn.close()

        # (2) committed-before-crash rows survived
        assert len(rows) >= committed_before_kill, (
            f"rows committed before the crash were lost: {len(rows)} < {committed_before_kill}"
        )
        # (3) recovery did not invent rows
        assert len(rows) <= total_sent, (
            f"ledger holds {len(rows)} rows but only {total_sent} requests "
            f"were ever sent ({total_acked} acked)"
        )
        # (4) recovered rows are well-formed
        for model, status_code, endpoint in rows:
            assert model == "claude-sonnet-4-5"
            assert status_code == 200
            assert "/v1/messages" in endpoint
    finally:
        proxy.cleanup()
