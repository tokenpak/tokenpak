# SPDX-License-Identifier: Apache-2.0
"""TIP capability declarations on FormatAdapter classes.

Verifies the consolidation pass: each adapter declares its TIP-1.0
capability set, the proxy hot path uses the registry to count tokens
correctly across formats (codex, google, etc.), and the capability
labels conform to the canonical TIP vocabulary.
"""

from __future__ import annotations

import json
import re

from tokenpak.core.contracts.capabilities import SELF_CAPABILITIES_PROXY
from tokenpak.proxy.adapters import build_default_registry
from tokenpak.proxy.adapters.anthropic_adapter import AnthropicAdapter
from tokenpak.proxy.adapters.base import FormatAdapter
from tokenpak.proxy.adapters.google_adapter import GoogleGenerativeAIAdapter
from tokenpak.proxy.adapters.openai_chat_adapter import OpenAIChatAdapter
from tokenpak.proxy.adapters.openai_responses_adapter import OpenAIResponsesAdapter
from tokenpak.proxy.adapters.passthrough_adapter import PassthroughAdapter
from tokenpak.proxy.server import _extract_tokens_via_adapters

# TIP capability label format: ``tip.<group>.<feature>`` or ``ext.<vendor>.<feature>``.
_TIP_LABEL_RE = re.compile(r"^(tip|ext)\.[a-z0-9._-]+$")


class TestCapabilityFieldShape:
    """Each adapter declares a frozenset of TIP-vocabulary labels."""

    def test_base_class_default_is_empty_frozenset(self):
        assert FormatAdapter.capabilities == frozenset()

    def test_anthropic_declares_byte_preserved_and_ttl_ordering(self):
        # Anthropic Messages: cache_control routing depends on byte
        # fidelity, so byte-preserved is mandatory; ttl-ordering applies
        # to the 1h-block-must-precede-default-ttl rule.
        caps = AnthropicAdapter.capabilities
        assert "tip.compression.v1" in caps
        assert "tip.byte-preserved-passthrough" in caps
        assert "tip.cache.proxy-managed" in caps
        assert "tip.cache.ttl-ordering" in caps

    def test_openai_responses_declares_compression_and_cache(self):
        caps = OpenAIResponsesAdapter.capabilities
        assert "tip.compression.v1" in caps
        assert "tip.cache.proxy-managed" in caps
        # Responses API is re-serialised on denormalize → not byte-preserved.
        assert "tip.byte-preserved-passthrough" not in caps

    def test_openai_chat_declares_compression_and_cache(self):
        caps = OpenAIChatAdapter.capabilities
        assert "tip.compression.v1" in caps
        assert "tip.cache.proxy-managed" in caps

    def test_google_declares_compression_and_cache(self):
        caps = GoogleGenerativeAIAdapter.capabilities
        assert "tip.compression.v1" in caps
        assert "tip.cache.proxy-managed" in caps

    def test_passthrough_declares_byte_preserved_only(self):
        # The catch-all fallback can only forward bytes — no opt-in to
        # compression / cache (we don't know the format).
        caps = PassthroughAdapter.capabilities
        assert "tip.byte-preserved-passthrough" in caps
        assert "tip.compression.v1" not in caps
        assert "tip.cache.proxy-managed" not in caps


class TestCapabilityLabelsAreValid:
    """Every declared label conforms to the TIP vocabulary pattern."""

    def test_all_adapter_labels_match_tip_pattern(self):
        for cls in (
            AnthropicAdapter,
            OpenAIResponsesAdapter,
            OpenAIChatAdapter,
            GoogleGenerativeAIAdapter,
            PassthroughAdapter,
        ):
            for label in cls.capabilities:
                assert _TIP_LABEL_RE.match(label), (
                    f"{cls.__name__}.capabilities contains non-TIP-shaped "
                    f"label {label!r}"
                )

    def test_adapter_capabilities_subset_of_proxy_self_published(self):
        # The proxy publishes a SELF_CAPABILITIES_PROXY set at boot.
        # Every per-adapter capability must be in that set (otherwise
        # we'd be claiming features the proxy doesn't actually
        # implement).
        union = set()
        for cls in (
            AnthropicAdapter,
            OpenAIResponsesAdapter,
            OpenAIChatAdapter,
            GoogleGenerativeAIAdapter,
            PassthroughAdapter,
        ):
            union |= cls.capabilities
        missing = union - SELF_CAPABILITIES_PROXY
        assert not missing, (
            f"Adapters declare {missing} but the proxy doesn't publish "
            f"those in SELF_CAPABILITIES_PROXY — either implement them "
            f"or drop them from the adapter."
        )


class TestDescribeIntrospection:
    """``describe()`` exposes adapter metadata for docs/discovery."""

    def test_describe_is_jsonable(self):
        for cls in (AnthropicAdapter, OpenAIResponsesAdapter, GoogleGenerativeAIAdapter):
            d = cls.describe()
            json.dumps(d)  # must not raise
            assert d["source_format"] == cls.source_format
            assert d["class_name"] == cls.__name__
            assert isinstance(d["capabilities"], list)
            assert d["capabilities"] == sorted(cls.capabilities)

    def test_registry_can_emit_full_adapter_inventory(self):
        # Used by ``tokenpak doctor`` / docs generator to enumerate
        # every registered adapter + its capabilities in one shot.
        registry = build_default_registry()
        inventory = [a.describe() for a in registry.adapters()]
        assert len(inventory) >= 5
        formats = {entry["source_format"] for entry in inventory}
        assert "anthropic-messages" in formats
        assert "openai-responses" in formats
        assert "google-generative-ai" in formats
        assert "passthrough" in formats


class TestProxyHotPathFormatAgnostic:
    """``_extract_tokens_via_adapters`` counts tokens per format correctly."""

    def test_anthropic_messages_body_counts_via_adapter(self):
        body = json.dumps({
            "model": "claude-haiku-4-5",
            "messages": [
                {"role": "user", "content": "hello world this is anthropic"},
            ],
        }).encode()
        model, tokens = _extract_tokens_via_adapters(
            body, {"x-api-key": "sk-test"}, "/v1/messages"
        )
        assert tokens > 0
        assert model == "claude-haiku-4-5"

    def test_openai_responses_body_counts_via_adapter(self):
        # The historical ``_estimate_tokens_from_body`` returned 0 for
        # this shape — that was the codex regression. After
        # consolidation, the adapter handles ``input`` and counts
        # correctly.
        body = json.dumps({
            "model": "gpt-5.4",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello world this is codex"}],
                }
            ],
        }).encode()
        model, tokens = _extract_tokens_via_adapters(body, {}, "/v1/responses")
        assert tokens > 0
        assert model == "gpt-5.4"

    def test_codex_responses_path_also_counts(self):
        # OpenClaw's pi-ai connector posts to /codex/responses — the
        # OpenAICodexResponsesAdapter (priority 270) detects + counts.
        body = json.dumps({
            "model": "gpt-5.4",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "another codex prompt"}],
                }
            ],
        }).encode()
        # JWT-shape Authorization header to match the codex adapter's
        # detect() on /v1/responses; for /codex/responses path we just
        # need any registered adapter to recognise the body shape.
        long_jwt = "eyJ" + "x" * 300 + ".y.z"
        _model, tokens = _extract_tokens_via_adapters(
            body,
            {"Authorization": f"Bearer {long_jwt}"},
            "/v1/responses",
        )
        assert tokens > 0

    def test_google_generative_ai_body_counts(self):
        body = json.dumps({
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "hello world this is gemini"}],
                }
            ],
        }).encode()
        # Google adapter's detect requires ``/v1beta/`` in path or
        # ``x-goog-api-key`` header. Either selects the adapter so
        # extract_request_tokens reads ``contents`` correctly.
        _model, tokens = _extract_tokens_via_adapters(
            body,
            {"x-goog-api-key": "AIza-test"},
            "/v1beta/models/gemini-pro:generateContent",
        )
        assert tokens > 0

    def test_empty_body_returns_zero(self):
        model, tokens = _extract_tokens_via_adapters(b"", {}, "/v1/messages")
        assert tokens == 0
        assert model == "unknown"

    def test_malformed_body_does_not_raise(self):
        model, tokens = _extract_tokens_via_adapters(b"not json", {}, "/v1/messages")
        # PassthroughAdapter accepts any body; it'll normalise to a
        # canonical with no messages → 0 tokens. Critical: no exception.
        assert tokens == 0
