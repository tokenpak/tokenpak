# SPDX-License-Identifier: Apache-2.0
"""Pending-store atomicity, block-path fail-closed, and row reconciliation.

Covers three failure modes around the pending store:

1. consume() must transition pending → consumed exactly once even under
   concurrent approvals (a lost race here means the SAME held request is
   replayed to the provider twice — double spend).
2. When the policy decides BLOCK but the pending store cannot persist the
   held request, the guard must still return a block — a store failure can
   never downgrade a block into a forward.
3. Rows abandoned by crashed/blocked sessions must be reconciled: the
   orchestrator sweeps expired pending rows on first evaluation in a
   process and periodically thereafter.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading

import pytest

from tokenpak.proxy.spend_guard import orchestrator
from tokenpak.proxy.spend_guard.contracts import PreflightDecision, RiskEstimate
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig

_BODY = b'{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"hi"}]}'


@pytest.fixture
def store(tmp_path):
    return PendingStore(str(tmp_path / "spend_guard.db"))


def _store_one(
    store: PendingStore,
    session_id: str = "sess-A",
    ttl_seconds: int = 600,
    body: bytes = _BODY,
):
    return store.store(
        session_id=session_id,
        body=body,
        headers={"content-type": "application/json"},
        target_url="https://api.anthropic.com/v1/messages",
        provider="anthropic",
        model="claude-sonnet-4-6",
        projected_tokens=600_000,
        projected_cost_usd=12.50,
        ttl_seconds=ttl_seconds,
    )


def _guard_cfg(tmp_path) -> SpendGuardConfig:
    cfg = SpendGuardConfig()
    cfg.enabled = True
    cfg.audit_db_path = str(tmp_path / "spend_guard.db")
    return cfg


# ---------------------------------------------------------------------------
# 1. consume() single-winner semantics under concurrency
# ---------------------------------------------------------------------------

def test_threaded_double_consume_exactly_one_replay(store):
    """N concurrent consume() calls on one pending row → exactly one winner."""
    p = _store_one(store)
    n = 4
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = store.consume(p.pending_id)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, (
        f"{len(winners)} threads consumed the same pending request — "
        "each extra winner is a duplicate provider replay"
    )
    assert winners[0].raw_request_blob == _BODY
    # And the row is terminally consumed.
    assert store.consume(p.pending_id) is None


def test_consume_marks_row_consumed(store):
    p = _store_one(store)
    assert store.consume(p.pending_id) is not None
    row = store.get_by_id(p.pending_id)
    assert row is not None
    assert row.status == "consumed"


# ---------------------------------------------------------------------------
# 2. block-path store failure fails closed
# ---------------------------------------------------------------------------

def _block_decision(est: RiskEstimate) -> PreflightDecision:
    return PreflightDecision(
        decision="block",
        reason="projected_tokens_exceeded",
        requires_approval=True,
        threshold_hit="block_tokens",
        risk=est,
    )


def test_block_path_store_failure_returns_block(tmp_path, monkeypatch, caplog):
    """Policy says BLOCK + pending store unwritable → still a 402 block."""
    est = RiskEstimate(
        model="claude-sonnet-4-6",
        current_context_tokens=0,
        request_tokens=500_000,
        projected_input_tokens=500_000,
        projected_output_tokens=8_000,
        projected_cost_usd=9.99,
        cache_hit_ratio=0.0,
        rates={},
    )
    monkeypatch.setattr(orchestrator, "run_estimate", lambda body, model: est)
    monkeypatch.setattr(
        orchestrator, "decide",
        lambda *a, **k: _block_decision(est),
    )

    def _raise_store(self, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(PendingStore, "store", _raise_store)

    with caplog.at_level(logging.WARNING):
        outcome = orchestrator.evaluate(
            _BODY, "claude-sonnet-4-6", "sess-storefail", {},
            config=_guard_cfg(tmp_path),
        )

    assert outcome.kind == "block"
    assert outcome.http_status == 402
    payload = json.loads(outcome.response_body.decode("utf-8"))["error"]
    assert payload["type"] == "tokenpak_spend_guard_blocked"
    assert payload["pending_id"] is None
    assert payload["retryable"] is False
    assert payload["recovery_status"] == "operator_action_required"
    assert "pending-store write failed" in caplog.text


# ---------------------------------------------------------------------------
# 3. expired pending rows are reconciled by evaluate()
# ---------------------------------------------------------------------------

def test_evaluate_sweeps_expired_pending_rows(tmp_path, monkeypatch):
    """First evaluate() in a process expires stale pending rows."""
    cfg = _guard_cfg(tmp_path)
    store = PendingStore(cfg.audit_db_path)
    # Distinct body: the stale row must not collide with the evaluated
    # request in the anti-loop request-hash cache.
    stale = _store_one(
        store, session_id="sess-crashed", ttl_seconds=-5,
        body=b'{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"old"}]}',
    )

    monkeypatch.setattr(orchestrator, "_LAST_EXPIRE_SWEEP", 0.0)
    outcome = orchestrator.evaluate(
        _BODY, "claude-sonnet-4-6", "sess-live", {}, config=cfg,
    )
    assert outcome.kind in ("forward", "forward_modified")

    row = store.get_by_id(stale.pending_id)
    assert row is not None
    assert row.status == "expired", (
        "crashed-session pending row was not reconciled by evaluate()"
    )


def test_expire_sweep_is_rate_limited(tmp_path, monkeypatch):
    """Within the sweep interval, evaluate() does not re-run expire_old()."""
    cfg = _guard_cfg(tmp_path)
    store = PendingStore(cfg.audit_db_path)

    calls: list[int] = []
    real_expire = PendingStore.expire_old

    def counting_expire(self):
        calls.append(1)
        return real_expire(self)

    monkeypatch.setattr(PendingStore, "expire_old", counting_expire)
    monkeypatch.setattr(orchestrator, "_LAST_EXPIRE_SWEEP", 0.0)

    orchestrator.evaluate(_BODY, "claude-sonnet-4-6", "sess-1", {}, config=cfg)
    orchestrator.evaluate(_BODY, "claude-sonnet-4-6", "sess-2", {}, config=cfg)

    assert len(calls) == 1, "sweep must run once per interval, not per request"
    assert store.expire_old() == 0  # direct call still works (test/CLI path)
