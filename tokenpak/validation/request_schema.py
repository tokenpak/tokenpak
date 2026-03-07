"""
TokenPak Request Schemas — JSON Schema definitions for Anthropic and OpenAI requests.

Used by RequestValidator to validate incoming proxy requests before forwarding.
"""

from __future__ import annotations

from typing import Any, Dict

# ---------------------------------------------------------------------------
# Anthropic /v1/messages schema
# ---------------------------------------------------------------------------

ANTHROPIC_MESSAGE_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AnthropicMessagesRequest",
    "description": "Anthropic /v1/messages API request",
    "type": "object",
    "required": ["model", "max_tokens", "messages"],
    "additionalProperties": True,
    "properties": {
        "model": {
            "type": "string",
            "minLength": 1,
            "description": "Anthropic model identifier (e.g. claude-sonnet-4-6)",
        },
        "max_tokens": {
            "type": "integer",
            "minimum": 1,
            "description": "Maximum tokens to generate",
        },
        "messages": {
            "type": "array",
            "minItems": 1,
            "description": "Conversation messages",
            "items": {
                "type": "object",
                "required": ["role", "content"],
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["user", "assistant"],
                    },
                    "content": {
                        "oneOf": [
                            {"type": "string", "minLength": 0},
                            {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["type"],
                                    "properties": {
                                        "type": {"type": "string"},
                                    },
                                },
                            },
                        ]
                    },
                },
            },
        },
        "system": {
            "oneOf": [
                {"type": "string"},
                {
                    "type": "array",
                    "items": {"type": "object"},
                },
            ],
            "description": "System prompt",
        },
        "stream": {
            "type": "boolean",
            "description": "Enable streaming responses",
        },
        "temperature": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Sampling temperature",
        },
        "top_p": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "top_k": {
            "type": "integer",
            "minimum": 0,
        },
        "stop_sequences": {
            "type": "array",
            "items": {"type": "string"},
        },
        "metadata": {"type": "object"},
        "tools": {"type": "array"},
        "tool_choice": {"type": "object"},
    },
}

# ---------------------------------------------------------------------------
# OpenAI /v1/chat/completions schema
# ---------------------------------------------------------------------------

OPENAI_CHAT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OpenAIChatCompletionsRequest",
    "description": "OpenAI /v1/chat/completions API request",
    "type": "object",
    "required": ["model", "messages"],
    "additionalProperties": True,
    "properties": {
        "model": {
            "type": "string",
            "minLength": 1,
            "description": "OpenAI model identifier (e.g. gpt-4o)",
        },
        "messages": {
            "type": "array",
            "minItems": 1,
            "description": "Conversation messages",
            "items": {
                "type": "object",
                "required": ["role"],
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["system", "user", "assistant", "tool", "function"],
                    },
                    "content": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "null"},
                            {
                                "type": "array",
                                "items": {"type": "object"},
                            },
                        ]
                    },
                    "name": {"type": "string"},
                    "tool_call_id": {"type": "string"},
                    "tool_calls": {"type": "array"},
                },
            },
        },
        "stream": {
            "type": "boolean",
            "description": "Enable streaming responses",
        },
        "max_tokens": {
            "type": "integer",
            "minimum": 1,
        },
        "max_completion_tokens": {
            "type": "integer",
            "minimum": 1,
        },
        "temperature": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
        },
        "top_p": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "n": {
            "type": "integer",
            "minimum": 1,
        },
        "stop": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        },
        "presence_penalty": {
            "type": "number",
            "minimum": -2.0,
            "maximum": 2.0,
        },
        "frequency_penalty": {
            "type": "number",
            "minimum": -2.0,
            "maximum": 2.0,
        },
        "logit_bias": {"type": "object"},
        "user": {"type": "string"},
        "tools": {"type": "array"},
        "tool_choice": {},
        "response_format": {"type": "object"},
        "seed": {"type": "integer"},
        "logprobs": {"type": "boolean"},
        "top_logprobs": {"type": "integer"},
        "parallel_tool_calls": {"type": "boolean"},
    },
}


# ---------------------------------------------------------------------------
# Schema registry
# ---------------------------------------------------------------------------


def get_request_schema(provider: str) -> Dict[str, Any]:
    """Return the appropriate request schema for a provider.

    Args:
        provider: "anthropic" or "openai"

    Returns:
        JSON Schema dict, or an empty permissive schema for unknown providers.
    """
    if provider == "anthropic":
        return ANTHROPIC_MESSAGE_SCHEMA
    if provider == "openai":
        return OPENAI_CHAT_SCHEMA
    # Unknown provider — return permissive schema (no required fields)
    return {"type": "object", "additionalProperties": True}
