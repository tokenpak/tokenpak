"""
Tests for tokenpak.core.validation.response_schema module.

Coverage targets:
- RESPONSE_SCHEMA structure and required fields
- RESPONSE_SCHEMA_MINIMAL structure
- get_schema() function behavior
- Token count fields
- Finish reason / status handling
- Missing/null field handling
"""

import pytest

from tokenpak.core.validation.response_schema import (
    RESPONSE_SCHEMA,
    RESPONSE_SCHEMA_MINIMAL,
    get_schema,
)


# ---------------------------------------------------------------------------
# RESPONSE_SCHEMA structure tests
# ---------------------------------------------------------------------------


class TestResponseSchema:
    """Tests for full RESPONSE_SCHEMA definition."""

    def test_schema_type_is_object(self):
        """Schema type must be object."""
        assert RESPONSE_SCHEMA["type"] == "object"

    def test_required_fields(self):
        """Required fields include model, tokens_sent, tokens_received, cost, timestamp."""
        required = RESPONSE_SCHEMA["required"]
        assert "model" in required
        assert "tokens_sent" in required
        assert "tokens_received" in required
        assert "cost" in required
        assert "timestamp" in required

    def test_model_property(self):
        """Model property has type string with minLength."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["model"]["type"] == "string"
        assert props["model"]["minLength"] == 1

    def test_tokens_sent_property(self):
        """tokens_sent must be integer with minimum 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["tokens_sent"]["type"] == "integer"
        assert props["tokens_sent"]["minimum"] == 0

    def test_tokens_received_property(self):
        """tokens_received must be integer with minimum 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["tokens_received"]["type"] == "integer"
        assert props["tokens_received"]["minimum"] == 0

    def test_tokens_saved_property(self):
        """tokens_saved is optional integer >= 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["tokens_saved"]["type"] == "integer"
        assert props["tokens_saved"]["minimum"] == 0

    def test_cost_property(self):
        """cost must be number with minimum 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["cost"]["type"] == "number"
        assert props["cost"]["minimum"] == 0

    def test_cost_saved_property(self):
        """cost_saved is optional number >= 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["cost_saved"]["type"] == "number"
        assert props["cost_saved"]["minimum"] == 0

    def test_cached_property(self):
        """cached is boolean type."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["cached"]["type"] == "boolean"

    def test_cache_read_tokens_property(self):
        """cache_read_tokens is integer >= 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["cache_read_tokens"]["type"] == "integer"
        assert props["cache_read_tokens"]["minimum"] == 0

    def test_cache_creation_tokens_property(self):
        """cache_creation_tokens is integer >= 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["cache_creation_tokens"]["type"] == "integer"
        assert props["cache_creation_tokens"]["minimum"] == 0

    def test_timestamp_format(self):
        """timestamp has date-time format."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["timestamp"]["type"] == "string"
        assert props["timestamp"]["format"] == "date-time"

    def test_latency_ms_property(self):
        """latency_ms is integer >= 0."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["latency_ms"]["type"] == "integer"
        assert props["latency_ms"]["minimum"] == 0

    def test_status_enum(self):
        """status has enum of valid values."""
        props = RESPONSE_SCHEMA["properties"]
        status_enum = props["status"]["enum"]
        assert "ok" in status_enum
        assert "error" in status_enum
        assert "timeout" in status_enum
        assert "rate_limited" in status_enum

    def test_compilation_mode_enum(self):
        """compilation_mode has enum of compression modes."""
        props = RESPONSE_SCHEMA["properties"]
        mode_enum = props["compilation_mode"]["enum"]
        assert "none" in mode_enum
        assert "light" in mode_enum
        assert "hybrid" in mode_enum
        assert "aggressive" in mode_enum

    def test_error_property_structure(self):
        """error property has type, message, code fields."""
        props = RESPONSE_SCHEMA["properties"]
        error_props = props["error"]["properties"]
        assert "type" in error_props
        assert "message" in error_props
        assert "code" in error_props

    def test_metadata_allows_additional_properties(self):
        """metadata object allows additional properties."""
        props = RESPONSE_SCHEMA["properties"]
        assert props["metadata"]["type"] == "object"
        assert props["metadata"]["additionalProperties"] is True

    def test_schema_allows_additional_properties(self):
        """Top-level schema allows additional properties."""
        assert RESPONSE_SCHEMA["additionalProperties"] is True

    def test_schema_has_id_and_title(self):
        """Schema has $id and title metadata."""
        assert "$id" in RESPONSE_SCHEMA
        assert "title" in RESPONSE_SCHEMA
        assert "TokenPak" in RESPONSE_SCHEMA["title"]


# ---------------------------------------------------------------------------
# RESPONSE_SCHEMA_MINIMAL tests
# ---------------------------------------------------------------------------


class TestResponseSchemaMinimal:
    """Tests for minimal response schema."""

    def test_minimal_type_is_object(self):
        """Minimal schema type is object."""
        assert RESPONSE_SCHEMA_MINIMAL["type"] == "object"

    def test_minimal_required_fields(self):
        """Minimal schema only requires model, tokens_sent, cost."""
        required = RESPONSE_SCHEMA_MINIMAL["required"]
        assert "model" in required
        assert "tokens_sent" in required
        assert "cost" in required
        # These should NOT be required in minimal
        assert "tokens_received" not in required
        assert "timestamp" not in required

    def test_minimal_has_fewer_properties(self):
        """Minimal schema has fewer defined properties than full."""
        minimal_props = len(RESPONSE_SCHEMA_MINIMAL["properties"])
        full_props = len(RESPONSE_SCHEMA["properties"])
        assert minimal_props < full_props

    def test_minimal_model_property(self):
        """Minimal schema has model with minLength."""
        props = RESPONSE_SCHEMA_MINIMAL["properties"]
        assert props["model"]["type"] == "string"
        assert props["model"]["minLength"] == 1


# ---------------------------------------------------------------------------
# get_schema() tests
# ---------------------------------------------------------------------------


class TestGetSchema:
    """Tests for get_schema() function."""

    def test_full_mode_returns_full_schema(self):
        """Mode 'full' returns RESPONSE_SCHEMA."""
        schema = get_schema(mode="full")
        assert schema is RESPONSE_SCHEMA

    def test_minimal_mode_returns_minimal_schema(self):
        """Mode 'minimal' returns RESPONSE_SCHEMA_MINIMAL."""
        schema = get_schema(mode="minimal")
        assert schema is RESPONSE_SCHEMA_MINIMAL

    def test_default_mode_is_full(self):
        """Default mode (no argument) returns full schema."""
        schema = get_schema()
        assert schema is RESPONSE_SCHEMA

    def test_unknown_mode_returns_full(self):
        """Unknown mode falls back to full schema."""
        schema = get_schema(mode="unknown_mode")
        assert schema is RESPONSE_SCHEMA

    def test_empty_mode_returns_full(self):
        """Empty string mode returns full schema."""
        schema = get_schema(mode="")
        assert schema is RESPONSE_SCHEMA
