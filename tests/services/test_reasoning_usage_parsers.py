"""Regression tests for dynamic per-provider reasoning-usage parsers.

Covers the Anthropic, OpenAI, and Google parsers registered under
``tokenpak.services.providers``. The registry is dynamic — these tests
exercise it via ``get_usage_parser`` (no hardcoded provider literals
in production code per the always-dynamic rule).
"""

from __future__ import annotations

import pytest

from tokenpak.services.providers import (
    get_usage_parser,
    list_registered_providers,
)


def test_registry_discovers_known_providers():
    providers = set(list_registered_providers())
    assert "anthropic" in providers
    assert "openai" in providers
    assert "google" in providers


def test_unknown_provider_returns_unavailable_parser():
    parser = get_usage_parser("nonexistent-provider")
    result = parser({"anything": 1})
    assert result["usage_source"] == "unavailable"
    assert result["reasoning_tokens"] is None
    assert result["total_billable_tokens"] is None


def test_anthropic_parser_no_reasoning_tokens():
    parser = get_usage_parser("anthropic")
    result = parser(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    )
    assert result["usage_source"] == "provider_usage_object"
    assert result["input_tokens"] == 100
    assert result["total_output_tokens"] == 50
    assert result["reasoning_tokens"] is None
    assert result["visible_output_tokens"] is None
    assert result["total_billable_tokens"] == 150
    assert result["provider_usage_ref"]  # non-empty hash


def test_anthropic_parser_extended_thinking_with_thoughts_field():
    parser = get_usage_parser("anthropic")
    result = parser(
        {
            "input_tokens": 200,
            "output_tokens": 300,
            "thinking_tokens": 120,
        }
    )
    assert result["reasoning_tokens"] == 120
    assert result["visible_output_tokens"] == 180
    assert result["total_output_tokens"] == 300
    assert result["total_billable_tokens"] == 500


def test_openai_parser_o_series_reasoning():
    parser = get_usage_parser("openai")
    result = parser(
        {
            "prompt_tokens": 80,
            "completion_tokens": 200,
            "total_tokens": 280,
            "completion_tokens_details": {
                "reasoning_tokens": 150,
            },
        }
    )
    assert result["usage_source"] == "provider_usage_object"
    assert result["input_tokens"] == 80
    assert result["total_output_tokens"] == 200
    assert result["reasoning_tokens"] == 150
    assert result["visible_output_tokens"] == 50
    assert result["total_billable_tokens"] == 280


def test_openai_parser_classic_chat_completion_no_reasoning():
    parser = get_usage_parser("openai")
    result = parser(
        {
            "prompt_tokens": 40,
            "completion_tokens": 60,
            "total_tokens": 100,
        }
    )
    assert result["reasoning_tokens"] is None
    assert result["visible_output_tokens"] is None
    assert result["total_billable_tokens"] == 100


def test_google_parser_thinking_model():
    parser = get_usage_parser("google")
    result = parser(
        {
            "promptTokenCount": 30,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 50,
            "totalTokenCount": 100,
        }
    )
    assert result["usage_source"] == "provider_usage_object"
    assert result["input_tokens"] == 30
    assert result["visible_output_tokens"] == 20
    assert result["reasoning_tokens"] == 50
    assert result["total_output_tokens"] == 70
    assert result["total_billable_tokens"] == 100


def test_google_parser_snake_case_aliases():
    parser = get_usage_parser("google")
    result = parser(
        {
            "prompt_token_count": 30,
            "candidates_token_count": 20,
        }
    )
    assert result["input_tokens"] == 30
    assert result["visible_output_tokens"] == 20
    assert result["reasoning_tokens"] is None


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
def test_parser_handles_none_input(provider):
    parser = get_usage_parser(provider)
    result = parser(None)
    assert result["usage_source"] == "unavailable"
    assert all(
        result[k] is None
        for k in [
            "input_tokens",
            "visible_output_tokens",
            "reasoning_tokens",
            "total_output_tokens",
            "total_billable_tokens",
            "reasoning_effort",
            "provider_usage_ref",
        ]
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
def test_parser_handles_non_dict_input(provider):
    parser = get_usage_parser(provider)
    for bad in ["string", 0, [], 3.14, object()]:
        result = parser(bad)  # type: ignore[arg-type]
        assert result["usage_source"] == "unavailable"
