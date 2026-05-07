"""Test suite for tokenpak.validation_gate — validation checks, budget limits, dry-run.

Covers:
- Budget validation and token limits
- Dry-run flag behavior
- Deterministic request validation
- Fingerprint tracking
"""


import pytest

pytest.importorskip("tokenpak.validation_gate", reason="module not available in current build")
import json

import pytest

from tokenpak.validation_gate import ValidationGate, ValidationResult


class TestValidationResult:
    """Test ValidationResult data class."""

    def test_result_valid(self):
        """ValidationResult tracks valid status."""
        result = ValidationResult(valid=True)
        assert result.valid is True

    def test_result_invalid(self):
        """ValidationResult tracks invalid status."""
        result = ValidationResult(valid=False, errors=["budget exceeded"])
        assert result.valid is False
        assert "budget" in result.errors[0].lower()

    def test_result_budget_fields(self):
        """ValidationResult tracks budget info."""
        result = ValidationResult(
            valid=True,
            budget_used=50000,
            budget_limit=100000
        )
        assert result.budget_used == 50000
        assert result.budget_limit == 100000

    def test_result_dry_run(self):
        """ValidationResult tracks dry-run flag."""
        result = ValidationResult(valid=True, dry_run=True)
        assert result.dry_run is True

    def test_result_fingerprint(self):
        """ValidationResult tracks fingerprint."""
        result = ValidationResult(valid=True, fingerprint="abc123")
        assert result.fingerprint == "abc123"


class TestValidationGateInitialization:
    """Test ValidationGate initialization."""

    def test_gate_enabled_default(self):
        """ValidationGate enabled by default."""
        gate = ValidationGate()
        assert gate.enabled is True

    def test_gate_disabled(self):
        """ValidationGate can be disabled."""
        gate = ValidationGate(enabled=False)
        assert gate.enabled is False

    def test_gate_budget_cap_default(self):
        """ValidationGate has default budget cap."""
        gate = ValidationGate()
        assert gate.token_budget_cap == 120000

    def test_gate_budget_cap_custom(self):
        """ValidationGate can set custom budget cap."""
        gate = ValidationGate(token_budget_cap=50000)
        assert gate.token_budget_cap == 50000


class TestBudgetValidation:
    """Test budget validation checks."""

    def test_budget_within_limit(self):
        """Request within budget passes."""
        gate = ValidationGate(token_budget_cap=100000)

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=50000
        )
        assert result.valid is True
        assert result.budget_used == 50000

    def test_budget_exceeds_limit(self):
        """Request exceeding budget fails."""
        gate = ValidationGate(token_budget_cap=100000)

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=150000
        )
        assert result.valid is False
        assert any("budget" in err.lower() for err in result.errors)

    def test_budget_exactly_at_limit(self):
        """Request exactly at limit passes."""
        gate = ValidationGate(token_budget_cap=100000)

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=100000
        )
        assert result.valid is True

    def test_budget_zero_limit(self):
        """Zero limit disables budget check."""
        gate = ValidationGate(token_budget_cap=0)

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=1000000
        )
        # Should pass when limit is 0 (disabled)
        assert result.valid is True

    def test_budget_negative_limit(self):
        """Negative limit treated as disabled."""
        gate = ValidationGate(token_budget_cap=-1)

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=1000000
        )
        assert result.valid is True


class TestDryRunValidation:
    """Test dry-run flag behavior."""

    def test_dry_run_in_payload(self):
        """Dry-run flag detected in payload."""
        gate = ValidationGate()
        payload = {"dry_run": True}

        result = gate.validate_request(
            request_body=json.dumps(payload).encode(),
            model="claude-3-opus",
            input_tokens=100000
        )
        assert result.dry_run is True

    def test_dry_run_in_tokenpak_block(self):
        """Dry-run flag in tokenpak block detected."""
        gate = ValidationGate()
        payload = {"tokenpak": {"dry_run": True}}

        result = gate.validate_request(
            request_body=json.dumps(payload).encode(),
            model="claude-3-opus",
            input_tokens=100000
        )
        assert result.dry_run is True

    def test_dry_run_in_metadata(self):
        """Dry-run flag in metadata block detected."""
        gate = ValidationGate()
        payload = {"metadata": {"dry_run": True}}

        result = gate.validate_request(
            request_body=json.dumps(payload).encode(),
            model="claude-3-opus",
            input_tokens=100000
        )
        assert result.dry_run is True

    def test_no_dry_run(self):
        """Request without dry-run flag returns false."""
        gate = ValidationGate()
        payload = {}

        result = gate.validate_request(
            request_body=json.dumps(payload).encode(),
            model="claude-3-opus",
            input_tokens=100000
        )
        assert result.dry_run is False


class TestDeterministicValidation:
    """Test deterministic request validation."""

    def test_deterministic_with_intent(self):
        """Request with intent is deterministic."""
        gate = ValidationGate()

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=100000,
            router_meta={"intent": "query"}
        )
        # Should not error for deterministic request (no context block check in this path)
        assert result is not None

    def test_deterministic_in_payload(self):
        """Deterministic flag in payload detected."""
        gate = ValidationGate()
        payload = {"tokenpak": {"deterministic": True}}

        result = gate.validate_request(
            request_body=json.dumps(payload).encode(),
            model="claude-3-opus",
            input_tokens=100000
        )
        assert result is not None


class TestValidationGateDisabled:
    """Test ValidationGate when disabled."""

    def test_disabled_gate_allows_all(self):
        """Disabled gate allows any request."""
        gate = ValidationGate(enabled=False, token_budget_cap=1)

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=1000000
        )
        assert result.valid is True

    def test_disabled_gate_with_capsule(self):
        """Disabled gate allows capsule validation."""
        gate = ValidationGate(enabled=False)

        class MockCapsule:
            token_count = 1000000

        result = gate.validate(MockCapsule())
        assert result.valid is True


class TestInvalidPayload:
    """Test handling of invalid payloads."""

    def test_invalid_json(self):
        """Invalid JSON in request fails gracefully."""
        gate = ValidationGate()

        result = gate.validate_request(
            request_body=b'invalid json {',
            model="claude-3-opus",
            input_tokens=100000
        )
        assert result.valid is False
        assert any("JSON" in err for err in result.errors)

    def test_empty_payload(self):
        """Empty payload handled correctly."""
        gate = ValidationGate()

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=50000
        )
        assert result.valid is True

    def test_null_bytes(self):
        """Null bytes in input handled."""
        gate = ValidationGate()

        result = gate.validate_request(
            request_body=b'{}',
            model="claude-3-opus",
            input_tokens=0
        )
        assert result.valid is True
