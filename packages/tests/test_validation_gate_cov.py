"""
Tests for tokenpak/validation_gate.py

Covers:
- ValidationResult dataclass
- ValidationGate.__init__
- ValidationGate.validate (capsule entry point)
- ValidationGate.validate_request (full JSON request validation)
- ValidationGate._extract_dry_run
- ValidationGate._is_deterministic
- ValidationGate._is_explicitly_deterministic
- ValidationGate._has_context_block
- ValidationGate._compute_fingerprint
"""

from __future__ import annotations

import sys
import os
import json
import hashlib

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tokenpak"))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from tokenpak.validation_gate import ValidationGate, ValidationResult


# ── ValidationResult dataclass ────────────────────────────────────────────


class TestValidationResultDataclass:
    def test_valid_field_stored(self):
        r = ValidationResult(valid=True)
        assert r.valid is True

    def test_errors_default_empty(self):
        r = ValidationResult(valid=True)
        assert r.errors == []

    def test_warnings_default_empty(self):
        r = ValidationResult(valid=True)
        assert r.warnings == []

    def test_budget_used_default_zero(self):
        r = ValidationResult(valid=True)
        assert r.budget_used == 0

    def test_budget_limit_default_zero(self):
        r = ValidationResult(valid=True)
        assert r.budget_limit == 0

    def test_fingerprint_default_empty_string(self):
        r = ValidationResult(valid=True)
        assert r.fingerprint == ""

    def test_dry_run_default_false(self):
        r = ValidationResult(valid=True)
        assert r.dry_run is False

    def test_plan_default_empty_dict(self):
        r = ValidationResult(valid=True)
        assert r.plan == {}

    def test_all_fields_set(self):
        r = ValidationResult(
            valid=False,
            errors=["err"],
            warnings=["warn"],
            budget_used=500,
            budget_limit=1000,
            fingerprint="abc123",
            dry_run=True,
            plan={"key": "val"},
        )
        assert r.errors == ["err"]
        assert r.warnings == ["warn"]
        assert r.budget_used == 500
        assert r.budget_limit == 1000
        assert r.fingerprint == "abc123"
        assert r.dry_run is True
        assert r.plan == {"key": "val"}


# ── ValidationGate init ───────────────────────────────────────────────────


class TestValidationGateInit:
    def test_enabled_default_true(self):
        gate = ValidationGate()
        assert gate.enabled is True

    def test_enabled_false(self):
        gate = ValidationGate(enabled=False)
        assert gate.enabled is False

    def test_token_budget_cap_default(self):
        gate = ValidationGate()
        assert gate.token_budget_cap == 120000

    def test_token_budget_cap_custom(self):
        gate = ValidationGate(token_budget_cap=50000)
        assert gate.token_budget_cap == 50000

    def test_enabled_coerced_to_bool(self):
        gate = ValidationGate(enabled=1)
        assert gate.enabled is True

    def test_budget_cap_coerced_to_int(self):
        gate = ValidationGate(token_budget_cap="80000")
        assert gate.token_budget_cap == 80000


# ── ValidationGate.validate (capsule endpoint) ───────────────────────────


class TestValidateCapsule:
    def test_disabled_gate_always_valid(self):
        gate = ValidationGate(enabled=False)

        class FakeCapsule:
            token_count = 999999

        result = gate.validate(FakeCapsule())
        assert result.valid is True

    def test_within_budget_is_valid(self):
        gate = ValidationGate(token_budget_cap=10000)

        class Capsule:
            token_count = 500

        result = gate.validate(Capsule())
        assert result.valid is True

    def test_over_budget_is_invalid(self):
        gate = ValidationGate(token_budget_cap=100)

        class Capsule:
            token_count = 200

        result = gate.validate(Capsule())
        assert result.valid is False
        assert any("budget exceeded" in e for e in result.errors)

    def test_budget_used_reported(self):
        gate = ValidationGate(token_budget_cap=10000)

        class Capsule:
            token_count = 300

        result = gate.validate(Capsule())
        assert result.budget_used == 300

    def test_dry_run_flag_passed_through(self):
        gate = ValidationGate()

        class Capsule:
            token_count = 50

        result = gate.validate(Capsule(), dry_run=True)
        assert result.dry_run is True

    def test_capsule_tokens_attribute_fallback(self):
        gate = ValidationGate(token_budget_cap=10000)

        class Capsule:
            tokens = 400  # uses 'tokens' not 'token_count'

        result = gate.validate(Capsule())
        assert result.budget_used == 400


# ── ValidationGate.validate_request ──────────────────────────────────────


class TestValidateRequest:
    def _body(self, payload: dict) -> bytes:
        return json.dumps(payload).encode()

    def test_disabled_gate_always_valid(self):
        gate = ValidationGate(enabled=False)
        result = gate.validate_request(self._body({"model": "gpt-4o"}), "gpt-4o", 100)
        assert result.valid is True

    def test_invalid_json_returns_error(self):
        gate = ValidationGate()
        result = gate.validate_request(b"not-json", "gpt-4o", 100)
        assert result.valid is False
        assert any("invalid JSON" in e for e in result.errors)

    def test_within_budget_valid(self):
        gate = ValidationGate(token_budget_cap=10000)
        result = gate.validate_request(self._body({"model": "gpt-4o", "messages": []}), "gpt-4o", 500)
        assert result.valid is True

    def test_over_budget_invalid(self):
        gate = ValidationGate(token_budget_cap=100)
        result = gate.validate_request(self._body({"model": "gpt-4o"}), "gpt-4o", 200)
        assert result.valid is False
        assert any("budget exceeded" in e for e in result.errors)

    def test_dry_run_top_level_flag(self):
        gate = ValidationGate()
        result = gate.validate_request(self._body({"dry_run": True}), "gpt-4o", 50)
        assert result.dry_run is True

    def test_dry_run_in_tokenpak_block(self):
        gate = ValidationGate()
        result = gate.validate_request(
            self._body({"tokenpak": {"dry_run": True}}), "gpt-4o", 50
        )
        assert result.dry_run is True

    def test_explicit_deterministic_without_context_block_errors(self):
        gate = ValidationGate()
        payload = {"model": "gpt-4o", "tokenpak": {"deterministic": True}}
        result = gate.validate_request(self._body(payload), "gpt-4o", 50)
        assert result.valid is False
        assert any("context block" in e for e in result.errors)

    def test_explicit_deterministic_with_context_block_ok(self):
        gate = ValidationGate()
        payload = {
            "model": "gpt-4o",
            "tokenpak": {"deterministic": True, "context_block": "some context"},
        }
        result = gate.validate_request(self._body(payload), "gpt-4o", 50)
        assert result.valid is True

    def test_plan_included_in_result(self):
        gate = ValidationGate()
        result = gate.validate_request(self._body({"model": "gpt-4o"}), "gpt-4o", 50)
        assert "model" in result.plan
        assert result.plan["model"] == "gpt-4o"

    def test_fingerprint_computed(self):
        gate = ValidationGate()
        result = gate.validate_request(
            self._body({"model": "gpt-4o"}),
            "gpt-4o",
            50,
            router_meta={"intent": "query", "recipe_used": "pipeline-v1"},
        )
        assert len(result.fingerprint) > 0


# ── Static helpers ────────────────────────────────────────────────────────


class TestExtractDryRun:
    def test_top_level_dry_run_true(self):
        assert ValidationGate._extract_dry_run({"dry_run": True}) is True

    def test_top_level_dry_run_false(self):
        assert ValidationGate._extract_dry_run({"dry_run": False}) is False

    def test_tokenpak_dry_run_true(self):
        assert ValidationGate._extract_dry_run({"tokenpak": {"dry_run": True}}) is True

    def test_metadata_dry_run_true(self):
        assert ValidationGate._extract_dry_run({"metadata": {"dry_run": True}}) is True

    def test_absent_returns_false(self):
        assert ValidationGate._extract_dry_run({}) is False


class TestIsDeterministic:
    def test_intent_without_fallback_is_deterministic(self):
        assert ValidationGate._is_deterministic({}, {"intent": "summarize", "fallback": False}) is True

    def test_fallback_true_not_deterministic_via_intent(self):
        assert ValidationGate._is_deterministic({}, {"intent": "summarize", "fallback": True}) is False

    def test_tokenpak_deterministic_flag(self):
        assert ValidationGate._is_deterministic({"tokenpak": {"deterministic": True}}, {}) is True

    def test_no_signals_not_deterministic(self):
        assert ValidationGate._is_deterministic({}, {}) is False


class TestIsExplicitlyDeterministic:
    def test_tokenpak_deterministic_true(self):
        assert ValidationGate._is_explicitly_deterministic({"tokenpak": {"deterministic": True}}) is True

    def test_tokenpak_deterministic_false(self):
        assert ValidationGate._is_explicitly_deterministic({"tokenpak": {"deterministic": False}}) is False

    def test_no_tokenpak_block(self):
        assert ValidationGate._is_explicitly_deterministic({}) is False


class TestHasContextBlock:
    def test_tokenpak_context_block_string(self):
        assert ValidationGate._has_context_block({"tokenpak": {"context_block": "some context"}}) is True

    def test_tokenpak_context_block_empty_string(self):
        assert ValidationGate._has_context_block({"tokenpak": {"context_block": ""}}) is False

    def test_tokenpak_context_block_dict(self):
        assert ValidationGate._has_context_block({"tokenpak": {"context_block": {"key": "val"}}}) is True

    def test_top_level_context_string(self):
        assert ValidationGate._has_context_block({"context": "text context"}) is True

    def test_top_level_context_empty(self):
        assert ValidationGate._has_context_block({"context": ""}) is False

    def test_no_context_false(self):
        assert ValidationGate._has_context_block({}) is False


class TestComputeFingerprint:
    def test_returns_24_char_hex(self):
        fp = ValidationGate._compute_fingerprint(
            {"intent": "query", "recipe_used": "pipeline-v1"}, {}
        )
        assert len(fp) == 24
        assert all(c in "0123456789abcdef" for c in fp)

    def test_same_inputs_same_fingerprint(self):
        meta = {"intent": "summarize", "recipe_used": "pipeline-v1"}
        fp1 = ValidationGate._compute_fingerprint(meta, {})
        fp2 = ValidationGate._compute_fingerprint(meta, {})
        assert fp1 == fp2

    def test_different_intent_different_fingerprint(self):
        fp1 = ValidationGate._compute_fingerprint({"intent": "a", "recipe_used": "r"}, {})
        fp2 = ValidationGate._compute_fingerprint({"intent": "b", "recipe_used": "r"}, {})
        assert fp1 != fp2
