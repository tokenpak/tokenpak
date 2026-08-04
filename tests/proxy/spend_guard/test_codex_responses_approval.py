# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for Codex Responses Spend Guard approvals."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tokenpak.proxy.request_pipeline import _resolve_session_id
from tokenpak.proxy.spend_guard import orchestrator
from tokenpak.proxy.spend_guard.contracts import PreflightDecision, RiskEstimate
from tokenpak.proxy.spend_guard.intent import Intent, parse_intent
from tokenpak.proxy.spend_guard.pending import PendingStore
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig
from tokenpak.proxy.spend_guard.tip_header import parse_and_strip_tip_header


def _responses_body(text: str, *, segment_type: str = "input_text") -> bytes:
    return json.dumps(
        {
            "model": "gpt-5.6-sol",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": segment_type, "text": text}],
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def _full_history_approval(text: str) -> bytes:
    return json.dumps(
        {
            "model": "gpt-5.6-sol",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "original request"}],
                },
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "yes"}],
                },
                {"type": "function_call", "name": "check", "arguments": "{}"},
                {"type": "function_call_output", "output": "ok"},
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": "data:image/png;base64,"},
                        {"type": "input_text", "text": text},
                    ],
                },
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "done"}]},
                {"type": "function_call", "name": "after_user", "arguments": "{}"},
            ],
        },
        separators=(",", ":"),
    ).encode()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", Intent.POSITIVE),
        ("No.", Intent.NEGATIVE),
        ("yes, and explain why", Intent.AMBIGUOUS),
    ],
)
@pytest.mark.parametrize("segment_type", ["input_text", "text"])
def test_responses_intent_yes_no_and_ambiguous(text, expected, segment_type):
    assert parse_intent(_responses_body(text, segment_type=segment_type)) is expected


def test_responses_intent_scans_back_from_reasoning_and_tool_items():
    assert parse_intent(_full_history_approval("yes")) is Intent.POSITIVE


def test_responses_tip_allow_once_is_parsed_and_stripped():
    body = json.dumps(
        {
            "model": "gpt-5.6-sol",
            "input": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": "data:image/png;base64,"},
                        {"type": "input_text", "text": " [TIP: allow=once] continue"},
                    ],
                },
                {"type": "function_call", "name": "later", "arguments": "{}"},
            ],
        },
        separators=(",", ":"),
    ).encode()

    directive, stripped = parse_and_strip_tip_header(body)

    assert directive is not None
    assert directive.allow_scope == "once"
    parsed = json.loads(stripped)
    assert parsed["input"][1]["content"][1]["text"] == "continue"
    assert parsed["input"][0] == {"type": "reasoning", "summary": []}
    assert parsed["input"][2]["type"] == "function_call"


def test_responses_tip_uses_latest_user_turn_in_full_history():
    body = _full_history_approval("[TIP: allow=once] continue")

    directive, stripped = parse_and_strip_tip_header(body)

    assert directive is not None
    assert directive.allow_scope == "once"
    parsed = json.loads(stripped)
    assert parsed["input"][0]["content"][0]["text"] == "original request"
    assert parsed["input"][2]["content"][0]["text"] == "yes"
    assert parsed["input"][5]["content"][0]["type"] == "input_image"
    assert parsed["input"][5]["content"][1]["text"] == "continue"
    assert parsed["input"][6]["type"] == "reasoning"
    assert parsed["input"][7]["type"] == "function_call"


def test_responses_tip_does_not_reuse_historical_user_directive():
    parsed = json.loads(_full_history_approval("continue"))
    parsed["input"][0]["content"][0]["text"] = "[TIP: allow=once] historical"
    body = json.dumps(parsed, separators=(",", ":")).encode()

    directive, stripped = parse_and_strip_tip_header(body)

    assert directive is None
    assert stripped is body


def test_codex_session_id_precedes_thread_id_with_thread_fallback():
    assert (
        _resolve_session_id(
            {"session-id": "codex-session", "thread-id": "codex-thread"},
            "fallback-model",
        )
        == "codex-session"
    )
    assert _resolve_session_id({"thread-id": "codex-thread"}, "fallback-model") == "codex-thread"


def _guard_config(tmp_path) -> SpendGuardConfig:
    cfg = SpendGuardConfig()
    cfg.enabled = True
    cfg.audit_db_path = str(tmp_path / "spend_guard.db")
    return cfg


def _force_block(monkeypatch) -> None:
    estimate = RiskEstimate(
        model="gpt-5.6-sol",
        current_context_tokens=520_000,
        request_tokens=8_000,
        projected_input_tokens=528_000,
        projected_output_tokens=8_000,
        projected_cost_usd=9.99,
        cache_hit_ratio=0.0,
        rates={},
    )
    decision = PreflightDecision(
        decision="block",
        reason="projected_tokens_exceeded",
        requires_approval=True,
        threshold_hit="block_tokens",
        risk=estimate,
    )
    monkeypatch.setattr(orchestrator, "run_estimate", lambda _body, _model: estimate)
    monkeypatch.setattr(orchestrator, "decide", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(orchestrator, "_LAST_EXPIRE_SWEEP", 0.0)


def test_concurrent_codex_sessions_keep_pending_and_approvals_isolated(tmp_path, monkeypatch):
    _force_block(monkeypatch)
    cfg = _guard_config(tmp_path)
    body = _responses_body("perform the expensive task")
    session_a = _resolve_session_id({"session-id": "codex-A"}, "")
    session_b = _resolve_session_id({"session-id": "codex-B"}, "")

    blocked_a = orchestrator.evaluate(body, "gpt-5.6-sol", session_a, {}, config=cfg)
    blocked_b = orchestrator.evaluate(body, "gpt-5.6-sol", session_b, {}, config=cfg)

    assert blocked_a.kind == blocked_b.kind == "block"
    assert blocked_a.pending_id != blocked_b.pending_id
    store = PendingStore(cfg.audit_db_path)
    pending_a = store.get_by_session(session_a)
    pending_b = store.get_by_session(session_b)
    assert pending_a is not None and pending_a.pending_id == blocked_a.pending_id
    assert pending_b is not None and pending_b.pending_id == blocked_b.pending_id

    replay_a = orchestrator.evaluate(
        _full_history_approval("yes"),
        "gpt-5.6-sol",
        session_a,
        {},
        config=cfg,
    )
    assert replay_a.kind == "replay"
    assert replay_a.body == body
    assert store.get_by_session(session_a) is None
    assert store.get_by_session(session_b) is not None

    replay_b = orchestrator.evaluate(
        _responses_body("[TIP: allow=once]"),
        "gpt-5.6-sol",
        session_b,
        {},
        config=cfg,
    )
    assert replay_b.kind == "replay"
    assert replay_b.body == body
    assert store.get_by_session(session_b) is None


def test_missing_identity_blocks_without_creating_global_pending_row(tmp_path, monkeypatch):
    _force_block(monkeypatch)
    cfg = _guard_config(tmp_path)
    body = _responses_body("perform the expensive task")

    outcome = orchestrator.evaluate(body, "gpt-5.6-sol", "", {}, config=cfg)

    assert outcome.kind == "block"
    assert outcome.pending_id is None
    error = json.loads(outcome.response_body)["error"]
    assert error["pending_id"] is None
    assert error["approval_prompt_available"] is False
    assert "stable session identity" in error["message"]

    with sqlite3.connect(cfg.audit_db_path) as conn:
        count = conn.execute(
            "SELECT count(*) FROM pending_requests WHERE session_id = ''"
        ).fetchone()[0]
    assert count == 0

    store = PendingStore(cfg.audit_db_path)
    with pytest.raises(ValueError, match="non-empty session id"):
        store.store(
            session_id="",
            body=body,
            headers={},
            target_url="http://127.0.0.1:8766/v1/responses",
            provider="openai",
            model="gpt-5.6-sol",
            projected_tokens=528_000,
            projected_cost_usd=9.99,
        )
