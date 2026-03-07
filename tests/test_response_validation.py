"""Tests for TokenPak response validation."""

import pytest
from datetime import datetime, timezone

from tokenpak.validation import (
    ResponseValidator,
    ValidationResult,
    validate_response,
    is_valid,
    RESPONSE_SCHEMA,
    get_schema,
)


# ============ Test Fixtures ============

@pytest.fixture
def valid_response():
    """A fully valid response with all fields."""
    return {
        "model": "claude-sonnet-4-6",
        "tokens_sent": 1000,
        "tokens_received": 500,
        "tokens_saved": 200,
        "cost": 0.015,
        "cost_saved": 0.003,
        "cached": False,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 500,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": "req_abc123",
        "latency_ms": 450,
        "compilation_mode": "hybrid",
        "status": "ok",
    }


@pytest.fixture
def minimal_response():
    """Response with only required fields."""
    return {
        "model": "claude-haiku-4-5",
        "tokens_sent": 100,
        "tokens_received": 50,
        "cost": 0.001,
        "timestamp": "2026-03-06T15:30:00Z",
    }


@pytest.fixture
def validator():
    """Default validator instance."""
    return ResponseValidator()


@pytest.fixture
def strict_validator():
    """Strict validator instance."""
    return ResponseValidator(strict=True)


# ============ Valid Response Tests ============

class TestValidResponses:
    """Tests for valid responses that should pass validation."""
    
    def test_full_valid_response(self, validator, valid_response):
        """Full response with all fields passes validation."""
        result = validator.validate(valid_response)
        assert result.valid
        assert len(result.errors) == 0
    
    def test_minimal_valid_response(self, validator, minimal_response):
        """Minimal response with required fields passes."""
        result = validator.validate(minimal_response)
        assert result.valid
        assert len(result.errors) == 0
    
    def test_is_valid_helper(self, valid_response):
        """is_valid() helper returns True for valid response."""
        assert is_valid(valid_response) is True
    
    def test_validate_response_helper(self, valid_response):
        """validate_response() helper works correctly."""
        result = validate_response(valid_response)
        assert result.valid
        assert isinstance(result, ValidationResult)
    
    def test_response_with_extra_fields(self, validator, valid_response):
        """Extra fields are allowed (additionalProperties: true)."""
        valid_response["custom_field"] = "custom_value"
        valid_response["another_field"] = 12345
        result = validator.validate(valid_response)
        assert result.valid
    
    def test_zero_tokens(self, validator, minimal_response):
        """Zero tokens is valid (minimum: 0)."""
        minimal_response["tokens_sent"] = 0
        minimal_response["tokens_received"] = 0
        result = validator.validate(minimal_response)
        assert result.valid
    
    def test_all_compilation_modes(self, validator, minimal_response):
        """All valid compilation modes pass."""
        for mode in ["none", "light", "hybrid", "aggressive"]:
            minimal_response["compilation_mode"] = mode
            result = validator.validate(minimal_response)
            assert result.valid, f"Mode {mode} should be valid"
    
    def test_all_status_values(self, validator, minimal_response):
        """All valid status values pass."""
        for status in ["ok", "error", "timeout", "rate_limited"]:
            minimal_response["status"] = status
            result = validator.validate(minimal_response)
            assert result.valid, f"Status {status} should be valid"


# ============ Invalid Response Tests ============

class TestInvalidResponses:
    """Tests for invalid responses that should fail validation."""
    
    def test_missing_required_field(self, validator, minimal_response):
        """Missing required field fails validation."""
        del minimal_response["model"]
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("model" in str(e) for e in result.errors)
    
    def test_all_missing_required_fields(self, validator):
        """Empty response fails with all required fields."""
        result = validator.validate({})
        assert not result.valid
        # Should have errors for: model, tokens_sent, tokens_received, cost, timestamp
        assert len(result.errors) >= 4
    
    def test_wrong_type_string(self, validator, minimal_response):
        """Wrong type for string field fails."""
        minimal_response["model"] = 12345  # Should be string
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("model" in str(e) for e in result.errors)
    
    def test_wrong_type_integer(self, validator, minimal_response):
        """Wrong type for integer field fails."""
        minimal_response["tokens_sent"] = "one thousand"  # Should be int
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("tokens_sent" in str(e) for e in result.errors)
    
    def test_wrong_type_number(self, validator, minimal_response):
        """Wrong type for number field fails."""
        minimal_response["cost"] = "expensive"  # Should be number
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("cost" in str(e) for e in result.errors)
    
    def test_negative_tokens(self, validator, minimal_response):
        """Negative token count fails (minimum: 0)."""
        minimal_response["tokens_sent"] = -100
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("tokens_sent" in str(e) for e in result.errors)
    
    def test_negative_cost(self, validator, minimal_response):
        """Negative cost fails (minimum: 0)."""
        minimal_response["cost"] = -0.01
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("cost" in str(e) for e in result.errors)
    
    def test_empty_model_string(self, validator, minimal_response):
        """Empty model string fails (minLength: 1)."""
        minimal_response["model"] = ""
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("model" in str(e) for e in result.errors)
    
    def test_invalid_enum_value(self, validator, minimal_response):
        """Invalid enum value fails."""
        minimal_response["compilation_mode"] = "super_aggressive"
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("compilation_mode" in str(e) for e in result.errors)
    
    def test_invalid_timestamp_format(self, validator, minimal_response):
        """Invalid timestamp format fails semantic check."""
        minimal_response["timestamp"] = "not-a-timestamp"
        result = validator.validate(minimal_response)
        assert not result.valid
        assert any("timestamp" in str(e) for e in result.errors)
    
    def test_null_required_field(self, validator, minimal_response):
        """Null value for required field fails."""
        minimal_response["model"] = None
        result = validator.validate(minimal_response)
        assert not result.valid


# ============ Warning Tests ============

class TestWarnings:
    """Tests for validation warnings (non-fatal issues)."""
    
    def test_tokens_saved_exceeds_sent(self, validator, valid_response):
        """Warning when tokens_saved > tokens_sent."""
        valid_response["tokens_saved"] = 2000
        valid_response["tokens_sent"] = 1000
        result = validator.validate(valid_response)
        assert result.valid  # Still valid, just a warning
        assert len(result.warnings) > 0
        assert any("tokens_saved" in str(w) for w in result.warnings)
    
    def test_unusually_high_cost(self, validator, valid_response):
        """Warning for unusually high cost."""
        valid_response["cost"] = 150.00
        result = validator.validate(valid_response)
        assert result.valid  # Still valid, just a warning
        assert any("cost" in str(w) for w in result.warnings)
    
    def test_strict_mode_warnings_become_errors(self, strict_validator, valid_response):
        """In strict mode, warnings become errors."""
        valid_response["tokens_saved"] = 2000
        valid_response["tokens_sent"] = 1000
        result = strict_validator.validate(valid_response)
        assert not result.valid  # Fails in strict mode
        assert len(result.warnings) == 0  # Warnings moved to errors


# ============ Edge Cases ============

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_very_large_tokens(self, validator, minimal_response):
        """Very large token counts are valid."""
        minimal_response["tokens_sent"] = 1_000_000
        minimal_response["tokens_received"] = 500_000
        result = validator.validate(minimal_response)
        assert result.valid
    
    def test_very_small_cost(self, validator, minimal_response):
        """Very small costs are valid."""
        minimal_response["cost"] = 0.000001
        result = validator.validate(minimal_response)
        assert result.valid
    
    def test_zero_cost(self, validator, minimal_response):
        """Zero cost is valid."""
        minimal_response["cost"] = 0
        result = validator.validate(minimal_response)
        assert result.valid
    
    def test_boolean_cached_field(self, validator, minimal_response):
        """Boolean cached field validates correctly."""
        minimal_response["cached"] = True
        result = validator.validate(minimal_response)
        assert result.valid
        
        minimal_response["cached"] = False
        result = validator.validate(minimal_response)
        assert result.valid
    
    def test_nested_error_object(self, validator, minimal_response):
        """Nested error object validates."""
        minimal_response["error"] = {
            "type": "rate_limit",
            "message": "Too many requests",
            "code": 429
        }
        minimal_response["status"] = "rate_limited"
        result = validator.validate(minimal_response)
        assert result.valid
    
    def test_metadata_object(self, validator, minimal_response):
        """Arbitrary metadata object is allowed."""
        minimal_response["metadata"] = {
            "provider": "anthropic",
            "region": "us-east-1",
            "custom": {"nested": "value"}
        }
        result = validator.validate(minimal_response)
        assert result.valid


# ============ Schema Tests ============

class TestSchema:
    """Tests for schema access and configuration."""
    
    def test_get_full_schema(self):
        """get_schema('full') returns complete schema."""
        schema = get_schema("full")
        assert "required" in schema
        assert len(schema["required"]) >= 4
        assert "properties" in schema
    
    def test_get_minimal_schema(self):
        """get_schema('minimal') returns minimal schema."""
        schema = get_schema("minimal")
        assert "required" in schema
        assert len(schema["required"]) == 3  # model, tokens_sent, cost
    
    def test_schema_has_expected_fields(self):
        """Schema contains all expected field definitions."""
        expected_fields = [
            "model", "tokens_sent", "tokens_received", "cost",
            "timestamp", "cached", "compilation_mode", "status"
        ]
        for field in expected_fields:
            assert field in RESPONSE_SCHEMA["properties"]
    
    def test_custom_schema(self):
        """Validator can use custom schema."""
        custom_schema = {
            "type": "object",
            "required": ["custom_field"],
            "properties": {
                "custom_field": {"type": "string"}
            }
        }
        validator = ResponseValidator(schema=custom_schema)
        
        result = validator.validate({"custom_field": "value"})
        assert result.valid
        
        result = validator.validate({})
        assert not result.valid


# ============ ValidationResult Tests ============

class TestValidationResult:
    """Tests for ValidationResult class."""
    
    def test_bool_conversion(self):
        """ValidationResult converts to bool correctly."""
        valid = ValidationResult(valid=True)
        assert bool(valid) is True
        
        invalid = ValidationResult(valid=False, errors=[{"field": "x"}])
        assert bool(invalid) is False
    
    def test_repr(self):
        """ValidationResult has useful repr."""
        valid = ValidationResult(valid=True)
        assert "valid=True" in repr(valid)
        
        invalid = ValidationResult(valid=False, errors=[{}, {}])
        assert "valid=False" in repr(invalid)
        assert "errors=2" in repr(invalid)
    
    def test_to_dict(self):
        """ValidationResult converts to dict."""
        result = ValidationResult(
            valid=False,
            errors=[{"field": "x", "reason": "bad"}],
            warnings=[{"field": "y", "reason": "warn"}]
        )
        d = result.to_dict()
        assert d["valid"] is False
        assert len(d["errors"]) == 1
        assert len(d["warnings"]) == 1


# ============ Integration Tests ============

class TestIntegration:
    """Integration tests simulating real usage."""
    
    def test_batch_validation(self, validator):
        """Validate a batch of responses."""
        responses = [
            {"model": "claude-sonnet-4-6", "tokens_sent": 100, "tokens_received": 50, "cost": 0.01, "timestamp": "2026-03-06T15:00:00Z"},
            {"model": "claude-haiku-4-5", "tokens_sent": 200, "tokens_received": 100, "cost": 0.005, "timestamp": "2026-03-06T15:01:00Z"},
            {"model": "", "tokens_sent": -1, "cost": "bad"},  # Invalid
        ]
        
        results = [validator.validate(r) for r in responses]
        assert results[0].valid
        assert results[1].valid
        assert not results[2].valid
    
    def test_real_proxy_response(self, validator):
        """Validate a response matching real proxy output."""
        response = {
            "model": "claude-opus-4-6",
            "tokens_sent": 15980,
            "tokens_received": 2500,
            "tokens_saved": 1443,
            "cost": 0.2181,
            "cost_saved": 0.0216,
            "cached": False,
            "cache_read_tokens": 12000,
            "cache_creation_tokens": 0,
            "timestamp": "2026-03-06T15:26:00-08:00",
            "request_id": "req_xyz789",
            "latency_ms": 806,
            "compilation_mode": "hybrid",
            "status": "ok",
            "metadata": {
                "provider": "anthropic",
                "vault_blocks_injected": 5
            }
        }
        result = validator.validate(response)
        assert result.valid
        assert len(result.errors) == 0
