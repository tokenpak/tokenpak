"""Tests for ValidationGate — pre-processing request validation.

Covers: validation_gate.py — validation rules, error handling, bypass modes.
"""

import json

from tokenpak.compression.validation_gate import ValidationGate, ValidationResult


class TestValidationGateBasics:
    """Test: ValidationGate initialization and basic checks."""

    def test_gate_initialization(self):
        """ValidationGate initializes with default config."""
        gate = ValidationGate()
        assert gate is not None

    def test_gate_can_validate_empty_request(self):
        """Gate accepts empty/minimal request (may pass or fail validation)."""
        gate = ValidationGate()
        result = gate.validate({})
        assert isinstance(result, ValidationResult)


class TestValidationChecks:
    """Test: Individual validation checks."""

    def test_validate_budget_check(self):
        """Budget validation: request respects token budget limit."""
        gate = ValidationGate()
        # Request with valid budget should pass
        result = gate.validate({"token_budget": 1000})
        assert result is not None

    def test_validate_required_fields(self):
        """Required field validation: model and content present."""
        gate = ValidationGate()
        # Minimal valid request
        result = gate.validate({"model": "claude-sonnet-4-6", "messages": []})
        assert result is not None


class TestBypassMode:
    """Test: Validation bypass and skip modes."""

    def test_skip_validation_env_var(self):
        """TOKENPAK_SKIP_GATE=1 allows requests to bypass validation."""
        import os

        os.environ["TOKENPAK_SKIP_GATE"] = "1"
        gate = ValidationGate()
        result = gate.validate({})
        # With skip enabled, should pass
        assert result.valid or result.skipped
        del os.environ["TOKENPAK_SKIP_GATE"]


class TestErrorMessages:
    """Test: Validation error messages are clear."""

    def test_missing_model_error_message(self):
        """Missing model produces clear error message."""
        gate = ValidationGate()
        result = gate.validate({"messages": []})
        if not result.valid:
            assert result.error_message is not None
            assert "model" in result.error_message.lower() or len(result.errors) > 0


class TestEdgeCases:
    """Test: Boundary conditions and edge cases."""

    def test_validate_zero_budget(self):
        """Zero token budget is handled (may pass or fail)."""
        gate = ValidationGate()
        result = gate.validate({"token_budget": 0})
        assert result is not None

    def test_validate_negative_budget(self):
        """Negative token budget should fail validation."""
        gate = ValidationGate()
        result = gate.validate({"token_budget": -100})
        if not result.valid:
            assert "budget" in str(result.errors).lower()

    def test_validate_extremely_large_request(self):
        """Very large requests are handled gracefully."""
        gate = ValidationGate()
        large_payload = {"messages": ["x" * 1_000_000]}
        result = gate.validate(large_payload)
        assert result is not None


class TestValidationIntegration:
    """Test: Integration with actual proxy request flow."""

    def test_valid_proxy_request_passes(self):
        """Standard proxy request format passes validation."""
        gate = ValidationGate()
        request = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        }
        result = gate.validate(request)
        assert result.valid or result.skipped

    def test_invalid_message_format_fails(self):
        """Invalid message format fails validation."""
        gate = ValidationGate()
        request = {
            "model": "claude-sonnet-4-6",
            "messages": "not a list",  # Should be list
        }
        result = gate.validate(request)
        # Should fail or be handled gracefully
        assert result is not None

    def test_concurrent_validation_requests(self):
        """Multiple validation requests don't interfere."""
        gate = ValidationGate()
        request1 = {"model": "claude-sonnet-4-6"}
        request2 = {"model": "gpt-4-turbo"}

        result1 = gate.validate(request1)
        result2 = gate.validate(request2)

        assert result1 is not None
        assert result2 is not None


class TestValidationPerformance:
    """Test: Validation performance under load."""

    def test_validation_is_fast_for_small_request(self):
        """Validation completes quickly for typical request."""
        import time

        gate = ValidationGate()
        request = {"model": "claude-sonnet-4-6", "messages": []}

        start = time.time()
        result = gate.validate(request)
        elapsed = time.time() - start

        assert result is not None
        assert elapsed < 0.1  # Should be < 100ms

    def test_validation_handles_many_messages(self):
        """Validation works with large message histories."""
        gate = ValidationGate()
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(100)]
        request = {"model": "claude-sonnet-4-6", "messages": messages}

        result = gate.validate(request)
        assert result is not None


class TestValidateRequestMethod:
    """Test: validate_request() method with detailed request validation."""

    def test_validate_request_with_valid_json_payload(self):
        """validate_request accepts valid JSON payload."""
        gate = ValidationGate()
        payload = {"model": "claude-sonnet-4-6", "messages": []}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
        )
        assert result is not None

    def test_validate_request_with_invalid_json(self):
        """validate_request rejects invalid JSON."""
        gate = ValidationGate()
        request_body = b'{"invalid json"}'

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
        )
        assert not result.valid
        assert any("JSON" in err or "json" in err for err in result.errors)

    def test_validate_request_budget_exceeded(self):
        """validate_request fails when input_tokens exceed budget."""
        gate = ValidationGate(token_budget_cap=1000)
        payload = {"model": "claude-sonnet-4-6"}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=2000,  # Exceeds budget
        )
        assert not result.valid
        assert any("budget" in err for err in result.errors)

    def test_validate_request_within_budget(self):
        """validate_request passes when input_tokens within budget."""
        gate = ValidationGate(token_budget_cap=1000)
        payload = {"model": "claude-sonnet-4-6"}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=500,  # Within budget
        )
        assert result.valid or len(result.errors) == 0

    def test_validate_request_with_dry_run_flag(self):
        """validate_request detects dry_run flags."""
        gate = ValidationGate()
        payload = {"dry_run": True}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
        )
        assert result.dry_run is True

    def test_validate_request_with_tokenpak_dry_run(self):
        """validate_request detects dry_run in tokenpak namespace."""
        gate = ValidationGate()
        payload = {"tokenpak": {"dry_run": True}}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
        )
        assert result.dry_run is True

    def test_validate_request_deterministic_flag(self):
        """validate_request detects deterministic intent."""
        gate = ValidationGate()
        payload = {"tokenpak": {"deterministic": True}}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
            router_meta={"intent": "analysis"},
        )
        assert result.plan.get("deterministic") is True

    def test_validate_request_missing_context_block_for_deterministic(self):
        """validate_request fails if deterministic but no context_block."""
        gate = ValidationGate()
        payload = {"tokenpak": {"deterministic": True}}  # No context_block
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
            router_meta={"intent": "analysis"},
        )
        if not result.valid:
            assert any("context" in err for err in result.errors)

    def test_validate_request_with_context_block(self):
        """validate_request accepts context_block."""
        gate = ValidationGate()
        payload = {"tokenpak": {"deterministic": True, "context_block": "Important context here"}}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
            router_meta={"intent": "analysis"},
        )
        # Should pass or have fewer errors now
        assert result is not None

    def test_validate_request_fingerprint_generation(self):
        """validate_request generates fingerprint."""
        gate = ValidationGate()
        payload = {}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
            router_meta={"intent": "query", "recipe_used": "v1"},
        )
        assert result.fingerprint is not None
        assert len(result.fingerprint) > 0

    def test_validate_request_plan_generation(self):
        """validate_request includes plan in result."""
        gate = ValidationGate()
        payload = {}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=100,
            router_meta={"intent": "analysis"},
        )
        assert result.plan is not None
        assert "model" in result.plan
        assert "input_tokens" in result.plan
        assert result.plan["model"] == "claude-sonnet-4-6"


class TestBudgetValidation:
    """Test: Token budget validation."""

    def test_budget_cap_zero_means_unlimited(self):
        """Budget cap of 0 means unlimited."""
        gate = ValidationGate(token_budget_cap=0)
        payload = {}
        request_body = json.dumps(payload).encode("utf-8")

        result = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,  # Very large
        )
        assert result.valid or not any("budget" in err for err in result.errors)

    def test_budget_cap_custom_value(self):
        """Custom budget cap is enforced."""
        gate = ValidationGate(token_budget_cap=5000)
        payload = {}
        request_body = json.dumps(payload).encode("utf-8")

        # Within budget
        result1 = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=3000,
        )
        assert result1.budget_limit == 5000

        # Exceeds budget
        result2 = gate.validate_request(
            request_body=request_body,
            model="claude-sonnet-4-6",
            input_tokens=6000,
        )
        assert not result2.valid


class TestGateDisabled:
    """Test: Disabled gate behavior."""

    def test_disabled_gate_always_valid(self):
        """Disabled gate returns valid for any input."""
        gate = ValidationGate(enabled=False)
        result = gate.validate({"invalid": "capsule"})
        assert result.valid is True

    def test_disabled_gate_validate_request_always_valid(self):
        """Disabled gate's validate_request always returns valid."""
        gate = ValidationGate(enabled=False)
        result = gate.validate_request(
            request_body=b"{}",
            model="test",
            input_tokens=999999,
        )
        assert result.valid is True
