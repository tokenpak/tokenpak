
import pytest

pytest.importorskip("tokenpak.validation_gate", reason="module not available in current build")
import json

from tokenpak.validation_gate import ValidationGate


def _body(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


def test_gate_blocks_over_budget():
    gate = ValidationGate(enabled=True, token_budget_cap=100)
    result = gate.validate_request(
        request_body=_body({"model": "claude-sonnet", "messages": [{"role": "user", "content": "hello"}]}),
        model="claude-sonnet",
        input_tokens=101,
        router_meta={"intent": "query", "recipe_used": "pipeline-v1", "slots": {}, "fallback": False},
    )
    assert result.valid is False
    assert any("token budget exceeded" in e for e in result.errors)


def test_gate_blocks_malformed_deterministic_context():
    gate = ValidationGate(enabled=True, token_budget_cap=1000)
    result = gate.validate_request(
        request_body=_body(
            {
                "model": "claude-sonnet",
                "messages": [{"role": "user", "content": "run"}],
                "tokenpak": {"deterministic": True},
            }
        ),
        model="claude-sonnet",
        input_tokens=50,
        router_meta={"intent": "execute", "recipe_used": "pipeline-v1", "slots": {"mode": "apply"}, "fallback": False},
    )
    assert result.valid is False
    assert any("missing required context block" in e for e in result.errors)


def test_gate_dry_run_returns_plan_and_no_block():
    gate = ValidationGate(enabled=True, token_budget_cap=1000)
    result = gate.validate_request(
        request_body=_body(
            {
                "model": "claude-sonnet",
                "messages": [{"role": "user", "content": "run"}],
                "tokenpak": {"deterministic": True, "context_block": "ctx", "dry_run": True},
            }
        ),
        model="claude-sonnet",
        input_tokens=50,
        router_meta={"intent": "execute", "recipe_used": "pipeline-v1", "slots": {"mode": "dry_run"}, "fallback": False},
    )
    assert result.valid is True
    assert result.dry_run is True
    assert result.plan["forward"] is False
    assert result.fingerprint


def test_gate_disabled_is_backward_compatible():
    gate = ValidationGate(enabled=False, token_budget_cap=1)
    result = gate.validate_request(
        request_body=_body({"messages": []}),
        model="x",
        input_tokens=9999,
        router_meta={},
    )
    assert result.valid is True
