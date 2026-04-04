"""
Tests for tokenpak/validation/validator.py

Covers:
- ValidationResult: init, __bool__, __repr__, to_dict
- ResponseValidator: init, validate (valid + invalid), _validate_manually,
  _validate_semantics, _check_type, strict mode, log_errors
- Module-level helpers: get_validator, validate_response, is_valid
"""

from __future__ import annotations

import sys
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tokenpak"))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest
from unittest.mock import patch

from tokenpak.validation.validator import (
    ValidationResult,
    ResponseValidator,
    get_validator,
    validate_response,
    is_valid,
)


# ── ValidationResult ──────────────────────────────────────────────────────


class TestValidationResult:
    def test_valid_result_bool_true(self):
        r = ValidationResult(valid=True)
        assert bool(r) is True

    def test_invalid_result_bool_false(self):
        r = ValidationResult(valid=False)
        assert bool(r) is False

    def test_errors_default_empty_list(self):
        r = ValidationResult(valid=True)
        assert r.errors == []

    def test_warnings_default_empty_list(self):
        r = ValidationResult(valid=True)
        assert r.warnings == []

    def test_errors_stored(self):
        err = [{"field": "x", "reason": "missing"}]
        r = ValidationResult(valid=False, errors=err)
        assert r.errors == err

    def test_warnings_stored(self):
        warn = [{"field": "model", "reason": "unknown family"}]
        r = ValidationResult(valid=True, warnings=warn)
        assert r.warnings == warn

    def test_repr_valid(self):
        r = ValidationResult(valid=True)
        assert "valid=True" in repr(r)

    def test_repr_invalid_shows_error_count(self):
        r = ValidationResult(valid=False, errors=[{"field": "x", "reason": "y"}])
        assert "errors=1" in repr(r)

    def test_to_dict_structure(self):
        r = ValidationResult(valid=True, errors=[], warnings=[])
        d = r.to_dict()
        assert d["valid"] is True
        assert d["errors"] == []
        assert d["warnings"] == []

    def test_to_dict_includes_errors(self):
        err = [{"field": "f", "reason": "r"}]
        r = ValidationResult(valid=False, errors=err)
        assert r.to_dict()["errors"] == err


# ── ResponseValidator init ─────────────────────────────────────────────────


class TestResponseValidatorInit:
    def test_default_schema_loaded(self):
        v = ResponseValidator()
        assert v.schema is not None
        assert isinstance(v.schema, dict)

    def test_custom_schema_accepted(self):
        schema = {"type": "object", "required": [], "properties": {}}
        v = ResponseValidator(schema=schema)
        assert v.schema is schema

    def test_strict_default_false(self):
        v = ResponseValidator()
        assert v.strict is False

    def test_strict_mode_stored(self):
        v = ResponseValidator(strict=True)
        assert v.strict is True

    def test_log_errors_default_true(self):
        v = ResponseValidator()
        assert v.log_errors is True

    def test_log_errors_false_stored(self):
        v = ResponseValidator(log_errors=False)
        assert v.log_errors is False


# ── ResponseValidator.validate ─────────────────────────────────────────────


class TestResponseValidatorValidate:
    def test_valid_response_returns_valid_result(self):
        v = ResponseValidator(schema={"type": "object", "required": [], "properties": {}})
        result = v.validate({"any_field": "value"})
        assert result.valid is True

    def test_returns_validation_result_instance(self):
        v = ResponseValidator()
        result = v.validate({})
        assert isinstance(result, ValidationResult)

    def test_invalid_response_with_missing_required(self):
        schema = {
            "type": "object",
            "required": ["must_have"],
            "properties": {"must_have": {"type": "string"}},
        }
        v = ResponseValidator(schema=schema)
        result = v.validate({})
        assert result.valid is False
        assert len(result.errors) > 0

    def test_strict_mode_turns_warnings_into_errors(self):
        # tokens_saved > tokens_sent triggers a warning
        v = ResponseValidator(strict=True)
        result = v.validate({"tokens_sent": 10, "tokens_saved": 100})
        # In strict mode the warning becomes an error
        assert result.valid is False

    def test_non_strict_warnings_not_errors(self):
        # tokens_saved > tokens_sent is a warning only; supply all required fields
        v = ResponseValidator(strict=False)
        result = v.validate({
            "model": "gpt-4o",
            "tokens_received": 50,
            "cost": 0.001,
            "timestamp": "2024-01-01T00:00:00Z",
            "tokens_sent": 10,
            "tokens_saved": 100,
        })
        assert result.valid is True
        assert len(result.warnings) > 0

    def test_warnings_empty_in_strict_mode(self):
        v = ResponseValidator(strict=True)
        result = v.validate({"tokens_sent": 10, "tokens_saved": 100})
        assert result.warnings == []


# ── ResponseValidator._validate_manually ─────────────────────────────────


class TestValidateManually:
    def setup_method(self):
        # Use a simple schema to exercise manual validation path
        self.schema = {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "count": {"type": "integer", "minimum": 0},
                "status": {"type": "string", "enum": ["ok", "error"]},
            },
        }

    def test_missing_required_field_error(self):
        v = ResponseValidator(schema=self.schema)
        errors = v._validate_manually({})
        assert any(e["field"] == "name" for e in errors)

    def test_present_required_field_no_error(self):
        v = ResponseValidator(schema=self.schema)
        errors = v._validate_manually({"name": "test"})
        assert not any(e["field"] == "name" for e in errors)

    def test_wrong_type_produces_error(self):
        v = ResponseValidator(schema=self.schema)
        errors = v._validate_manually({"name": 123})
        assert any(e["field"] == "name" for e in errors)

    def test_below_minimum_produces_error(self):
        v = ResponseValidator(schema=self.schema)
        errors = v._validate_manually({"name": "x", "count": -1})
        assert any(e["field"] == "count" for e in errors)

    def test_enum_violation_produces_error(self):
        v = ResponseValidator(schema=self.schema)
        errors = v._validate_manually({"name": "x", "status": "invalid_status"})
        assert any(e["field"] == "status" for e in errors)

    def test_valid_input_no_errors(self):
        v = ResponseValidator(schema=self.schema)
        errors = v._validate_manually({"name": "hello", "count": 5, "status": "ok"})
        assert errors == []

    def test_string_min_length_violation(self):
        schema = {
            "type": "object",
            "required": [],
            "properties": {"tag": {"type": "string", "minLength": 3}},
        }
        v = ResponseValidator(schema=schema)
        errors = v._validate_manually({"tag": "ab"})  # too short
        assert any(e["field"] == "tag" for e in errors)


# ── ResponseValidator._check_type ─────────────────────────────────────────


class TestCheckType:
    def setup_method(self):
        self.v = ResponseValidator()

    def test_string_matches_str(self):
        assert self.v._check_type("hello", "string") is True

    def test_int_fails_string(self):
        assert self.v._check_type(42, "string") is False

    def test_integer_matches_int(self):
        assert self.v._check_type(10, "integer") is True

    def test_float_matches_number(self):
        assert self.v._check_type(3.14, "number") is True

    def test_bool_matches_boolean(self):
        assert self.v._check_type(True, "boolean") is True

    def test_dict_matches_object(self):
        assert self.v._check_type({}, "object") is True

    def test_list_matches_array(self):
        assert self.v._check_type([], "array") is True

    def test_none_matches_null(self):
        assert self.v._check_type(None, "null") is True

    def test_unknown_type_returns_true(self):
        # Unknown types should not block validation
        assert self.v._check_type("anything", "custom_type") is True


# ── ResponseValidator._validate_semantics ────────────────────────────────


class TestValidateSemantics:
    def setup_method(self):
        self.v = ResponseValidator()

    def test_valid_iso_timestamp_no_error(self):
        errors, warnings = self.v._validate_semantics({"timestamp": "2024-01-01T00:00:00Z"})
        assert not any(e["field"] == "timestamp" for e in errors)

    def test_invalid_timestamp_produces_error(self):
        errors, warnings = self.v._validate_semantics({"timestamp": "not-a-date"})
        assert any(e["field"] == "timestamp" for e in errors)

    def test_tokens_saved_exceeds_sent_warning(self):
        errors, warnings = self.v._validate_semantics({"tokens_sent": 10, "tokens_saved": 100})
        assert any(w["field"] == "tokens_saved" for w in warnings)

    def test_tokens_saved_within_sent_no_warning(self):
        errors, warnings = self.v._validate_semantics({"tokens_sent": 100, "tokens_saved": 50})
        assert not any(w["field"] == "tokens_saved" for w in warnings)

    def test_high_cost_produces_warning(self):
        errors, warnings = self.v._validate_semantics({"cost": 200.0})
        assert any(w["field"] == "cost" for w in warnings)

    def test_normal_cost_no_warning(self):
        errors, warnings = self.v._validate_semantics({"cost": 0.005})
        assert not any(w["field"] == "cost" for w in warnings)

    def test_unrecognized_model_family_warning(self):
        errors, warnings = self.v._validate_semantics({"model": "unknown-llm-xyz"})
        assert any(w["field"] == "model" for w in warnings)

    def test_known_model_family_no_warning(self):
        errors, warnings = self.v._validate_semantics({"model": "claude-3-haiku"})
        assert not any(w["field"] == "model" for w in warnings)


# ── Module-level helpers ──────────────────────────────────────────────────


class TestModuleLevelHelpers:
    def test_get_validator_returns_response_validator(self):
        v = get_validator()
        assert isinstance(v, ResponseValidator)

    def test_get_validator_returns_singleton(self):
        v1 = get_validator()
        v2 = get_validator()
        assert v1 is v2

    def test_validate_response_returns_validation_result(self):
        result = validate_response({})
        assert isinstance(result, ValidationResult)

    def test_validate_response_strict_true(self):
        result = validate_response({"tokens_sent": 10, "tokens_saved": 1000}, strict=True)
        assert result.valid is False

    def test_is_valid_returns_bool(self):
        assert isinstance(is_valid({}), bool)

    def test_is_valid_true_for_empty_response(self):
        # Empty dict has no required fields by default schema
        result = is_valid({})
        assert isinstance(result, bool)
