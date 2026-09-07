# SPDX-License-Identifier: Apache-2.0
"""Conversation instructions and decisions survive the Pak builder verbatim."""

from __future__ import annotations

import json

import pytest

from tokenpak.companion.capsules.builder import CapsuleBuilder
from tokenpak.proxy.capsule_integration import capsule_request_hook, clear_cache


def _protected_text(label: str) -> str:
    return (
        f"{label} BEGIN "
        + ("background narrative " * 80)
        + f"{label} MIDDLE: keep this exact constraint. "
        + ("more historical detail " * 80)
        + f"{label} END: do not edit files or restart services."
    )


@pytest.mark.parametrize("role", ["user", "system", "developer"])
def test_messages_protected_roles_survive_at_beginning_middle_and_end(role: str) -> None:
    protected = _protected_text(role)
    payload = {
        "model": "fixture-model",
        "system": _protected_text("top-level-system"),
        "messages": [
            {"role": role, "content": protected},
            {"role": "assistant", "content": "acknowledged"},
            {"role": "user", "content": "continue"},
        ],
    }
    original = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    emitted, stats = CapsuleBuilder(enabled=True, hot_window=0).process(original)

    assert emitted == original
    assert stats["blocks_capsulized"] == 0
    assert json.loads(emitted)["messages"][0]["content"] == protected


def test_responses_input_preserves_instructions_tools_and_pairing() -> None:
    instruction = _protected_text("responses-user")
    decision = _protected_text("assistant-decision")
    payload = {
        "model": "fixture-model",
        "instructions": _protected_text("responses-system"),
        "tools": [
            {
                "type": "function",
                "name": "inspect_state",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": instruction}],
            },
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "inspect_state",
                "arguments": '{"path":"x.py"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "Traceback: exact failure evidence",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": decision}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "continue from that decision"}],
            },
        ],
    }
    original = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    emitted, stats = CapsuleBuilder(enabled=True, hot_window=0).process(original)

    assert emitted == original
    assert stats["blocks_capsulized"] == 0
    restored = json.loads(emitted)
    assert restored["input"][0]["content"][0]["text"] == instruction
    assert restored["input"][3]["content"][0]["text"] == decision
    assert restored["input"][1]["call_id"] == restored["input"][2]["call_id"] == "call-1"
    assert restored["input"][2]["output"] == "Traceback: exact failure evidence"


def test_assistant_decision_and_rationale_survive_continuation() -> None:
    decision = (
        "Decision: keep the migration paused. Rationale: the rollback receipt is missing. "
        + ("Supporting evidence remains unresolved. " * 60)
        + "Resume only after the exact rollback artifact is verified."
    )
    payload = {
        "messages": [
            {"role": "assistant", "content": decision},
            {"role": "user", "content": "Continue from the prior decision."},
        ]
    }
    original = json.dumps(payload, separators=(",", ":")).encode()

    emitted, _ = CapsuleBuilder(enabled=True, hot_window=0).process(original)

    continuation_context = json.loads(emitted)["messages"][0]["content"]
    assert continuation_context == decision
    assert "rollback receipt is missing" in continuation_context
    assert continuation_context.endswith("exact rollback artifact is verified.")


def test_proxy_request_hook_preserves_role_bearing_messages_and_responses(
    monkeypatch,
) -> None:
    payloads = [
        {
            "model": "fixture-model",
            "messages": [
                {"role": "system", "content": _protected_text("system")},
                {"role": "user", "content": _protected_text("messages-user")},
                {"role": "assistant", "content": _protected_text("assistant")},
            ],
        },
        {
            "model": "fixture-model",
            "instructions": _protected_text("responses-system"),
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": _protected_text("input-user")}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": _protected_text("input-assistant")}
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
        },
    ]
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "1")

    try:
        for payload in payloads:
            clear_cache()
            original = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

            emitted, _, _, _ = capsule_request_hook(original, "fixture-model")

            assert emitted == original
    finally:
        clear_cache()
