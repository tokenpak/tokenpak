"""Regression: unattended clients can identify and report guard intervention."""

import json

import pytest

from tokenpak.proxy.spend_guard import orchestrator
from tokenpak.proxy.spend_guard.estimator import estimate
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig, decide


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_unattended_soft_block_is_session_scoped_and_never_auto_approved(tmp_path, client):
    cfg = SpendGuardConfig(audit_db_path=str(tmp_path / "guard.db"))
    text = "x" * 2_100_000
    body = {"model": "unknown-context-test", "max_tokens": 1000}
    body.update(
        {"messages": [{"role": "user", "content": text}]} if client == "claude" else {"input": text}
    )
    wire = json.dumps(body).encode()
    out = orchestrator.evaluate(wire, body["model"], client, {}, config=cfg)
    assert out.kind == "block" and out.http_status == 402
    error = json.loads(out.response_body)["error"]
    assert error["type"] == "tokenpak_spend_guard_blocked"
    assert error["recovery_status"] == "user_action_required"
    assert error["session_id"] == client
    assert error["automatic_retry_allowed"] is False
    assert error["headless_action"] == "stop_and_report"
    assert PendingStore(cfg.audit_db_path).get_by_session(client) is not None
    retry = orchestrator.evaluate(wire, body["model"], client, {}, config=cfg)
    assert retry.kind not in {"forward", "replay", "forward_modified"}


def test_unknown_context_fallback_bands_are_unchanged():
    cfg = SpendGuardConfig()
    assert (cfg.warn_tokens, cfg.block_tokens, cfg.hard_block_tokens) == (
        100_000,
        500_000,
        1_000_000,
    )
    for tokens, expected in [(100_000, "warn"), (500_000, "block"), (1_000_000, "hard_block")]:
        risk = estimate(
            json.dumps(
                {"messages": [{"role": "user", "content": "x" * (tokens * 4)}], "max_tokens": 1}
            ).encode(),
            "unknown-context-test",
        )
        assert decide(risk, cfg, model_max_context_tokens=None).decision == expected
