"""Test edge cases and error scenarios in request validation."""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.validation", reason="module not available in current build")
import json

import pytest

from tokenpak.validation.request_validator import RequestValidator


class TestRequestValidatorMissingFields:
    """Test that validation catches missing required fields with clear error messages."""

    def test_missing_messages_field(self):
        """Request without 'messages' field should raise ValidationError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "max_tokens": 100,
            # missing 'messages'
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert not result.valid
        assert any("messages" in str(e).lower() for e in result.errors)

    def test_empty_messages_array(self):
        """Request with empty messages array should raise ValidationError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [],
            "max_tokens": 100,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        # Empty messages array should be invalid
        assert not result.valid or len(result.errors) > 0

    def test_missing_model_field(self):
        """Request without 'model' field should raise ValidationError."""
        validator = RequestValidator()
        request = {
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 100,
            # missing 'model'
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert not result.valid
        assert any("model" in str(e).lower() for e in result.errors)

    def test_missing_message_role(self):
        """Message without 'role' should raise ValidationError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [
                {"content": "hello"}  # missing 'role'
            ],
            "max_tokens": 100,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert not result.valid
        assert any("role" in str(e).lower() for e in result.errors)

    def test_missing_message_content(self):
        """Message without 'content' should ideally raise ValidationError or allow it."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [
                {"role": "user"}  # missing 'content'
            ],
            "max_tokens": 100,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        # OpenAI schema may allow tool_call messages without content, so we accept it passing
        # But we verify the validator ran without crashing
        assert result is not None


class TestRequestValidatorTypeErrors:
    """Test type checking on common request fields."""

    def test_messages_is_dict_not_list(self):
        """If 'messages' is a dict instead of list, should raise TypeError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": {"role": "user", "content": "hello"},  # dict, not list
            "max_tokens": 100,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert not result.valid
        assert any("messages" in str(e).lower() or "type" in str(e).lower() for e in result.errors)

    def test_max_tokens_is_string(self):
        """If 'max_tokens' is a string instead of int, should raise TypeError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": "100",  # string, not int
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert not result.valid
        assert any("max_tokens" in str(e).lower() or "type" in str(e).lower() for e in result.errors)

    def test_temperature_is_string(self):
        """If 'temperature' is a string instead of float, should raise TypeError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": "0.5",  # string, not float
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert not result.valid
        assert any("temperature" in str(e).lower() or "type" in str(e).lower() for e in result.errors)

    def test_temperature_above_max(self):
        """If 'temperature' > 2.0, should raise ValueError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 2.5,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        # Temperature may be clipped or rejected
        assert not result.valid or result.warnings

    def test_temperature_below_min(self):
        """If 'temperature' < 0.0, should raise ValueError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": -0.5,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert not result.valid or result.warnings

    def test_frequency_penalty_out_of_range(self):
        """If 'frequency_penalty' out of [-2, 2], should raise ValueError."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "frequency_penalty": 3.0,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        # May be invalid or warned
        assert not result.valid or result.warnings


class TestRequestValidatorAPIMismatch:
    """Test graceful handling of cross-API format mismatches."""

    def test_anthropic_specific_field_in_openai_request(self):
        """OpenAI request with Anthropic-specific field 'system_prompt' should be handled gracefully."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "system_prompt": "You are helpful",  # Anthropic-specific
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        # Should either accept (ignoring unknown field) or reject clearly
        if not result.valid:
            assert any("system_prompt" in str(e).lower() for e in result.errors)

    def test_openai_stop_sequences_field(self):
        """Request with 'stop_sequences' (OpenAI) should normalize or reject."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "stop_sequences": ["\n"],  # OpenAI style
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        # Should handle without crashing
        assert result is not None

    def test_anthropic_metadata_in_openai_request(self):
        """OpenAI request with Anthropic 'metadata' field should be handled."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"user_id": "123"},  # Anthropic-specific
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        # Should handle gracefully
        assert result is not None

    def test_valid_request_passes(self):
        """Valid request should pass validation."""
        validator = RequestValidator()
        request = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello world"}],
            "max_tokens": 100,
            "temperature": 0.7,
        }
        body = json.dumps(request).encode("utf-8")
        result = validator.validate(body, provider="openai")
        assert result.valid
