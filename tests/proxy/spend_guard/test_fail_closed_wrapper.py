# SPDX-License-Identifier: Apache-2.0
"""Fail-closed coverage for the public Spend Guard evaluate wrapper."""

from __future__ import annotations

import json
import logging
import sqlite3

from tokenpak.proxy import spend_guard
from tokenpak.proxy.spend_guard import orchestrator
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig


def test_internal_guard_error_blocks_before_provider_send(monkeypatch, caplog):
    def raise_corrupt_store(*_args, **_kwargs):
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(orchestrator, "evaluate", raise_corrupt_store)

    with caplog.at_level(logging.WARNING):
        outcome = spend_guard.evaluate(
            b'{"model":"claude-opus-4-7","messages":[]}',
            "claude-opus-4-7",
            "session-corrupt-store",
            {},
        )

    assert outcome.kind == "block"
    assert outcome.body is None
    assert outcome.http_status == 402
    assert outcome.audit_event == "fail_closed_internal_error"

    payload = json.loads(outcome.response_body.decode("utf-8"))
    assert payload["error"]["type"] == "tokenpak_spend_guard_blocked"
    assert payload["error"]["reason"] == "spend_guard_state_unavailable"
    assert payload["error"]["failure_kind"] == "spend_guard_internal_error"
    assert payload["error"]["threshold_hit"] == "internal_error:DatabaseError"
    assert payload["error"]["pending_id"] is None
    assert payload["error"]["approval_prompt"] is None
    assert payload["error"]["approval_prompt_available"] is False
    assert payload["error"]["auto_proceed_available"] is False
    assert payload["error"]["continuum_auto_proceed_available"] is False
    assert payload["error"]["continuum_status"] == "not_active"
    assert payload["error"]["recovery_status"] == "operator_action_required"
    assert payload["error"]["recovery_actions"] == [
        "run tokenpak doctor",
        "repair or restore the local Spend Guard state store",
        "restart the TokenPak proxy after repair",
    ]
    assert payload["error"]["retryable"] is False
    assert "file is not a database" not in outcome.response_body.decode("utf-8")
    assert "internal error (fail closed): DatabaseError" in caplog.text


def test_transient_lock_error_retries_then_succeeds(monkeypatch):
    body = b'{"model":"claude-opus-4-7","messages":[]}'
    calls = {"n": 0}

    def locked_twice_then_forward(fwd_body, *_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        from tokenpak.proxy.spend_guard.contracts import GuardOutcome

        return GuardOutcome.passthrough(fwd_body)

    sleeps: list[float] = []
    monkeypatch.setattr(orchestrator, "evaluate", locked_twice_then_forward)
    monkeypatch.setattr(spend_guard.time, "sleep", sleeps.append)

    outcome = spend_guard.evaluate(body, "claude-opus-4-7", "session-lock-recovers", {})

    assert outcome.kind == "forward"
    assert outcome.body == body
    assert calls["n"] == 3
    assert sleeps == list(spend_guard._LOCK_RETRY_BACKOFF_SEC)


def test_lock_error_exhausted_returns_retryable_state_busy(monkeypatch, caplog):
    def always_locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(orchestrator, "evaluate", always_locked)
    monkeypatch.setattr(spend_guard.time, "sleep", lambda _s: None)

    with caplog.at_level(logging.WARNING):
        outcome = spend_guard.evaluate(
            b'{"model":"claude-opus-4-7","messages":[]}',
            "claude-opus-4-7",
            "session-lock-exhausted",
            {},
        )

    assert outcome.kind == "block"
    assert outcome.http_status == 402
    assert outcome.audit_event == "fail_closed_state_busy"

    payload = json.loads(outcome.response_body.decode("utf-8"))
    err = payload["error"]
    assert err["type"] == "tokenpak_spend_guard_unavailable"
    assert err["reason"] == "guard_state_busy"
    assert err["failure_kind"] == "spend_guard_state_busy"
    assert err["retryable"] is True
    assert err["retry_after_seconds"] == 5
    assert err["recovery_status"] == "retry_safe"
    assert err["pending_id"] is None
    assert err["approval_prompt_available"] is False
    assert "NOT a spend or budget block" in err["message"]
    assert "state store still busy" in caplog.text


def test_non_lock_operational_error_fails_closed_without_retry(monkeypatch):
    calls = {"n": 0}

    def disk_io_error(*_args, **_kwargs):
        calls["n"] += 1
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(orchestrator, "evaluate", disk_io_error)

    outcome = spend_guard.evaluate(
        b'{"model":"claude-opus-4-7","messages":[]}',
        "claude-opus-4-7",
        "session-disk-io",
        {},
    )

    assert calls["n"] == 1
    payload = json.loads(outcome.response_body.decode("utf-8"))
    assert payload["error"]["reason"] == "spend_guard_state_unavailable"
    assert payload["error"]["retryable"] is False
    assert "NOT a spend or budget block" in payload["error"]["message"]


def test_explicitly_disabled_guard_still_passthrough(monkeypatch):
    def raise_if_called(*_args, **_kwargs):
        raise AssertionError("orchestrator should not run when guard is disabled")

    monkeypatch.setattr(orchestrator, "evaluate", raise_if_called)
    cfg = SpendGuardConfig()
    cfg.enabled = False
    body = b'{"model":"claude-opus-4-7","messages":[]}'

    outcome = spend_guard.evaluate(body, "claude-opus-4-7", "session-disabled", {}, config=cfg)

    assert outcome.kind == "forward"
    assert outcome.body == body
