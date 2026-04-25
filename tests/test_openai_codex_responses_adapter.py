# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenAICodexResponsesAdapter."""

from __future__ import annotations

import json

from tokenpak.proxy.adapters import build_default_registry
from tokenpak.proxy.adapters.canonical import CanonicalRequest
from tokenpak.proxy.adapters.openai_codex_responses_adapter import (
    OpenAICodexResponsesAdapter,
    _is_chatgpt_oauth_token,
)

JWT_SAMPLE = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.signature"


class TestJWTDetection:
    def test_eyJ_with_dot_is_jwt(self):
        assert _is_chatgpt_oauth_token(JWT_SAMPLE) is True

    def test_bearer_prefix_stripped(self):
        assert _is_chatgpt_oauth_token(f"Bearer {JWT_SAMPLE}") is True

    def test_lowercase_bearer_prefix_stripped(self):
        assert _is_chatgpt_oauth_token(f"bearer {JWT_SAMPLE}") is True

    def test_sk_api_key_is_not_jwt(self):
        assert _is_chatgpt_oauth_token("Bearer sk-abc123") is False

    def test_empty_header_is_not_jwt(self):
        assert _is_chatgpt_oauth_token("") is False

    def test_eyJ_without_dot_is_not_jwt(self):
        assert _is_chatgpt_oauth_token("eyJabcdefg") is False


class TestDetect:
    def test_v1_responses_with_jwt_matches(self):
        adapter = OpenAICodexResponsesAdapter()
        assert (
            adapter.detect(
                "/v1/responses",
                {"Authorization": f"Bearer {JWT_SAMPLE}"},
                None,
            )
            is True
        )

    def test_v1_responses_with_lowercase_authorization_matches(self):
        adapter = OpenAICodexResponsesAdapter()
        assert (
            adapter.detect(
                "/v1/responses",
                {"authorization": f"Bearer {JWT_SAMPLE}"},
                None,
            )
            is True
        )

    def test_v1_responses_with_sk_key_does_not_match(self):
        adapter = OpenAICodexResponsesAdapter()
        assert (
            adapter.detect(
                "/v1/responses",
                {"Authorization": "Bearer sk-abc123"},
                None,
            )
            is False
        )

    def test_other_path_does_not_match(self):
        adapter = OpenAICodexResponsesAdapter()
        assert (
            adapter.detect(
                "/v1/chat/completions",
                {"Authorization": f"Bearer {JWT_SAMPLE}"},
                None,
            )
            is False
        )

    def test_missing_auth_does_not_match(self):
        adapter = OpenAICodexResponsesAdapter()
        assert adapter.detect("/v1/responses", {}, None) is False


class TestUpstreamAndPath:
    def test_default_upstream_is_chatgpt_backend(self):
        adapter = OpenAICodexResponsesAdapter()
        assert adapter.get_default_upstream() == "https://chatgpt.com/backend-api"

    def test_upstream_path_is_codex_responses(self):
        adapter = OpenAICodexResponsesAdapter()
        assert adapter.get_upstream_path() == "/codex/responses"

    def test_sse_format_inherited(self):
        adapter = OpenAICodexResponsesAdapter()
        assert adapter.get_sse_format() == "openai-responses-sse"


class TestDenormalize:
    def _canonical(self, **overrides) -> CanonicalRequest:
        defaults = dict(
            model="gpt-5-codex",
            system="",
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            generation={},
            stream=False,
            raw_extra={"_input_format": "string"},
            source_format="openai-codex-responses",
        )
        defaults.update(overrides)
        return CanonicalRequest(**defaults)

    def test_forces_stream_true(self):
        adapter = OpenAICodexResponsesAdapter()
        payload = json.loads(adapter.denormalize(self._canonical(stream=False)))
        assert payload["stream"] is True

    def test_forces_store_false(self):
        adapter = OpenAICodexResponsesAdapter()
        payload = json.loads(adapter.denormalize(self._canonical()))
        assert payload["store"] is False

    def test_drops_max_output_tokens(self):
        adapter = OpenAICodexResponsesAdapter()
        canonical = self._canonical(generation={"max_output_tokens": 1024})
        payload = json.loads(adapter.denormalize(canonical))
        assert "max_output_tokens" not in payload

    def test_promotes_string_input_to_message_list(self):
        adapter = OpenAICodexResponsesAdapter()
        payload = json.loads(adapter.denormalize(self._canonical()))
        assert isinstance(payload["input"], list)
        assert payload["input"][0]["role"] == "user"
        assert payload["input"][0]["content"][0]["type"] == "input_text"
        assert payload["input"][0]["content"][0]["text"] == "Hello"

    def test_empty_string_input_becomes_empty_list(self):
        adapter = OpenAICodexResponsesAdapter()
        canonical = self._canonical(messages=[{"role": "user", "content": ""}])
        payload = json.loads(adapter.denormalize(canonical))
        assert payload["input"] == []

    def test_existing_list_input_preserved(self):
        adapter = OpenAICodexResponsesAdapter()
        canonical = self._canonical(
            messages=[
                {"role": "system", "content": "ctx"},
                {"role": "user", "content": "Hi"},
            ],
            raw_extra={"_input_format": "message_array"},
        )
        payload = json.loads(adapter.denormalize(canonical))
        assert isinstance(payload["input"], list)
        assert len(payload["input"]) == 2
        assert payload["input"][0]["role"] == "system"
        assert payload["input"][1]["role"] == "user"


class TestRegistryRegistration:
    def test_codex_adapter_registered_above_standard_responses(self):
        registry = build_default_registry()
        # JWT bearer to /v1/responses must select the Codex adapter,
        # not the standard OpenAIResponsesAdapter at priority 260.
        adapter = registry.detect(
            "/v1/responses",
            {"Authorization": f"Bearer {JWT_SAMPLE}"},
            None,
        )
        assert isinstance(adapter, OpenAICodexResponsesAdapter)

    def test_sk_key_falls_through_to_standard_responses(self):
        registry = build_default_registry()
        adapter = registry.detect(
            "/v1/responses",
            {"Authorization": "Bearer sk-test"},
            None,
        )
        assert not isinstance(adapter, OpenAICodexResponsesAdapter)
        assert adapter.source_format == "openai-responses"
