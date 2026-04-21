"""
TokenPak Response Schema — JSON Schema definition for validated responses.

This module defines the canonical schema for TokenPak response objects,
ensuring type safety and field validation across the pipeline.
"""

from __future__ import annotations

from typing import Any, Dict

# JSON Schema for TokenPak responses
RESPONSE_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://tokenpak.dev/schemas/response.json",
    "title": "TokenPak Response",
    "description": "Schema for validated TokenPak proxy responses",
    "type": "object",
    "required": ["model", "tokens_sent", "tokens_received", "cost", "timestamp"],
    "properties": {
        "model": {
            "type": "string",
            "minLength": 1,
            "description": "Model identifier (e.g., claude-sonnet-4-6)",
        },
        "tokens_sent": {
            "type": "integer",
            "minimum": 0,
            "description": "Number of input tokens sent to the model",
        },
        "tokens_received": {
            "type": "integer",
            "minimum": 0,
            "description": "Number of output tokens received from the model",
        },
        "tokens_saved": {
            "type": "integer",
            "minimum": 0,
            "description": "Tokens saved via compression (optional)",
        },
        "cost": {"type": "number", "minimum": 0, "description": "Estimated cost in USD"},
        "cost_saved": {
            "type": "number",
            "minimum": 0,
            "description": "Cost saved via compression (optional)",
        },
        "cached": {"type": "boolean", "description": "Whether response was served from cache"},
        "cache_read_tokens": {
            "type": "integer",
            "minimum": 0,
            "description": "Tokens read from prompt cache",
        },
        "cache_creation_tokens": {
            "type": "integer",
            "minimum": 0,
            "description": "Tokens written to prompt cache",
        },
        "timestamp": {
            "type": "string",
            "format": "date-time",
            "description": "ISO8601 timestamp of the response",
        },
        "request_id": {"type": "string", "description": "Unique request identifier"},
        "latency_ms": {
            "type": "integer",
            "minimum": 0,
            "description": "Request latency in milliseconds",
        },
        "compilation_mode": {
            "type": "string",
            "enum": ["none", "light", "hybrid", "aggressive"],
            "description": "Compression mode used",
        },
        "status": {
            "type": "string",
            "enum": ["ok", "error", "timeout", "rate_limited"],
            "description": "Response status",
        },
        "error": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "message": {"type": "string"},
                "code": {"type": "integer"},
            },
            "description": "Error details if status != ok",
        },
        "metadata": {
            "type": "object",
            "additionalProperties": True,
            "description": "Additional metadata (provider-specific)",
        },
    },
    "additionalProperties": True,
}

# Minimal schema for lightweight validation
RESPONSE_SCHEMA_MINIMAL: Dict[str, Any] = {
    "type": "object",
    "required": ["model", "tokens_sent", "cost"],
    "properties": {
        "model": {"type": "string", "minLength": 1},
        "tokens_sent": {"type": "integer", "minimum": 0},
        "cost": {"type": "number", "minimum": 0},
    },
}


def get_schema(mode: str = "full") -> Dict[str, Any]:
    """Get response schema by mode.

    Args:
        mode: "full" for complete validation, "minimal" for required fields only

    Returns:
        JSON Schema dictionary
    """
    if mode == "minimal":
        return RESPONSE_SCHEMA_MINIMAL
    return RESPONSE_SCHEMA
