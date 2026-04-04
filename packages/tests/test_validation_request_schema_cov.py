"""
Tests for tokenpak.validation.request_schema module.

Coverage targets:
- ANTHROPIC_MESSAGE_SCHEMA structure and fields
- OPENAI_CHAT_SCHEMA structure and fields
- OPENAI_RESPONSES_SCHEMA structure and fields
- GOOGLE_GENERATE_CONTENT_SCHEMA structure and fields
- get_request_schema() function behavior
"""

import pytest

from tokenpak.validation.request_schema import (
    ANTHROPIC_MESSAGE_SCHEMA,
    GOOGLE_GENERATE_CONTENT_SCHEMA,
    OPENAI_CHAT_SCHEMA,
    OPENAI_RESPONSES_SCHEMA,
    get_request_schema,
)


# ---------------------------------------------------------------------------
# ANTHROPIC_MESSAGE_SCHEMA tests
# ---------------------------------------------------------------------------


class TestAnthropicMessageSchema:
    """Tests for Anthropic /v1/messages schema."""

    def test_schema_type_is_object(self):
        """Schema type must be object."""
        assert ANTHROPIC_MESSAGE_SCHEMA["type"] == "object"

    def test_required_fields(self):
        """Model, max_tokens, and messages are required."""
        required = ANTHROPIC_MESSAGE_SCHEMA["required"]
        assert "model" in required
        assert "max_tokens" in required
        assert "messages" in required

    def test_model_property(self):
        """Model property has type string with minLength."""
        props = ANTHROPIC_MESSAGE_SCHEMA["properties"]
        assert props["model"]["type"] == "string"
        assert props["model"]["minLength"] == 1

    def test_max_tokens_property(self):
        """max_tokens must be integer with minimum 1."""
        props = ANTHROPIC_MESSAGE_SCHEMA["properties"]
        assert props["max_tokens"]["type"] == "integer"
        assert props["max_tokens"]["minimum"] == 1

    def test_messages_property_is_array(self):
        """messages must be an array with minItems 1."""
        props = ANTHROPIC_MESSAGE_SCHEMA["properties"]
        assert props["messages"]["type"] == "array"
        assert props["messages"]["minItems"] == 1

    def test_temperature_bounds(self):
        """temperature must be between 0.0 and 1.0."""
        props = ANTHROPIC_MESSAGE_SCHEMA["properties"]
        assert props["temperature"]["minimum"] == 0.0
        assert props["temperature"]["maximum"] == 1.0

    def test_additional_properties_allowed(self):
        """Schema allows additional properties."""
        assert ANTHROPIC_MESSAGE_SCHEMA["additionalProperties"] is True


# ---------------------------------------------------------------------------
# OPENAI_CHAT_SCHEMA tests
# ---------------------------------------------------------------------------


class TestOpenAIChatSchema:
    """Tests for OpenAI /v1/chat/completions schema."""

    def test_schema_type_is_object(self):
        """Schema type must be object."""
        assert OPENAI_CHAT_SCHEMA["type"] == "object"

    def test_required_fields(self):
        """Model and messages are required (max_tokens optional in OpenAI)."""
        required = OPENAI_CHAT_SCHEMA["required"]
        assert "model" in required
        assert "messages" in required

    def test_openai_role_enum(self):
        """OpenAI roles include system, user, assistant, tool, function."""
        messages_items = OPENAI_CHAT_SCHEMA["properties"]["messages"]["items"]
        role_enum = messages_items["properties"]["role"]["enum"]
        assert "system" in role_enum
        assert "user" in role_enum
        assert "assistant" in role_enum
        assert "tool" in role_enum
        assert "function" in role_enum

    def test_temperature_bounds_openai(self):
        """OpenAI temperature can go up to 2.0."""
        props = OPENAI_CHAT_SCHEMA["properties"]
        assert props["temperature"]["maximum"] == 2.0

    def test_presence_penalty_bounds(self):
        """presence_penalty ranges from -2.0 to 2.0."""
        props = OPENAI_CHAT_SCHEMA["properties"]
        assert props["presence_penalty"]["minimum"] == -2.0
        assert props["presence_penalty"]["maximum"] == 2.0


# ---------------------------------------------------------------------------
# OPENAI_RESPONSES_SCHEMA tests
# ---------------------------------------------------------------------------


class TestOpenAIResponsesSchema:
    """Tests for OpenAI /v1/responses (Codex) schema."""

    def test_schema_type_is_object(self):
        """Schema type must be object."""
        assert OPENAI_RESPONSES_SCHEMA["type"] == "object"

    def test_required_fields(self):
        """Model and input are required."""
        required = OPENAI_RESPONSES_SCHEMA["required"]
        assert "model" in required
        assert "input" in required

    def test_input_is_oneof(self):
        """input can be string or array."""
        input_schema = OPENAI_RESPONSES_SCHEMA["properties"]["input"]
        assert "oneOf" in input_schema
        types = [opt.get("type") for opt in input_schema["oneOf"]]
        assert "string" in types
        assert "array" in types


# ---------------------------------------------------------------------------
# GOOGLE_GENERATE_CONTENT_SCHEMA tests
# ---------------------------------------------------------------------------


class TestGoogleGenerateContentSchema:
    """Tests for Google Gemini generateContent schema."""

    def test_schema_type_is_object(self):
        """Schema type must be object."""
        assert GOOGLE_GENERATE_CONTENT_SCHEMA["type"] == "object"

    def test_contents_required(self):
        """contents is the only required field."""
        required = GOOGLE_GENERATE_CONTENT_SCHEMA["required"]
        assert "contents" in required
        # model is NOT required in Google schema (comes from URL path)
        assert "model" not in required

    def test_contents_is_array(self):
        """contents must be a non-empty array."""
        props = GOOGLE_GENERATE_CONTENT_SCHEMA["properties"]
        assert props["contents"]["type"] == "array"
        assert props["contents"]["minItems"] == 1

    def test_contents_items_have_parts(self):
        """Each content item requires parts."""
        items = GOOGLE_GENERATE_CONTENT_SCHEMA["properties"]["contents"]["items"]
        assert "parts" in items["required"]


# ---------------------------------------------------------------------------
# get_request_schema() tests
# ---------------------------------------------------------------------------


class TestGetRequestSchema:
    """Tests for the schema registry function."""

    def test_anthropic_provider(self):
        """Returns Anthropic schema for 'anthropic' provider."""
        schema = get_request_schema("anthropic")
        assert schema is ANTHROPIC_MESSAGE_SCHEMA

    def test_openai_provider(self):
        """Returns OpenAI chat schema for 'openai' provider."""
        schema = get_request_schema("openai")
        assert schema is OPENAI_CHAT_SCHEMA

    def test_openai_codex_provider(self):
        """Returns Responses schema for 'openai-codex' provider."""
        schema = get_request_schema("openai-codex")
        assert schema is OPENAI_RESPONSES_SCHEMA

    def test_google_provider(self):
        """Returns Google schema for 'google' provider."""
        schema = get_request_schema("google")
        assert schema is GOOGLE_GENERATE_CONTENT_SCHEMA

    def test_unknown_provider_returns_permissive(self):
        """Unknown provider returns permissive schema."""
        schema = get_request_schema("unknown-provider")
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is True
        assert "required" not in schema

    def test_empty_provider_returns_permissive(self):
        """Empty string provider returns permissive schema."""
        schema = get_request_schema("")
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is True
