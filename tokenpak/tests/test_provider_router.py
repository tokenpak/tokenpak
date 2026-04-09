"""test_provider_router.py — Tests for ProviderRouter, estimate_cost, get_model_tier.

Bypasses FastAPI/Starlette incompatibility by patching the broken ingest module
before triggering the import chain.
"""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch the broken ingest module BEFORE importing anything from tokenpak.agent
# ---------------------------------------------------------------------------
_fake_ingest = types.ModuleType("tokenpak.agent.ingest")
_fake_ingest.create_ingest_app = MagicMock()
_fake_ingest.ingest_router = MagicMock()
_fake_ingest.schema_converter = types.ModuleType("tokenpak.agent.ingest.schema_converter")
_fake_ingest.schema_converter.should_serve_schema = lambda intent: False
_fake_ingest.schema_converter.convert_document = MagicMock(return_value={})
sys.modules.setdefault("tokenpak.agent.ingest", _fake_ingest)
sys.modules.setdefault("tokenpak.agent.ingest.schema_converter", _fake_ingest.schema_converter)
sys.modules.setdefault("tokenpak.agent.ingest.api", MagicMock())

from tokenpak.agent.proxy.router import (  # noqa: E402
    ProviderRouter,
    RouteResult,
    estimate_cost,
    get_model_tier,
)


# ---------------------------------------------------------------------------
# ProviderRouter — provider detection from path
# ---------------------------------------------------------------------------


class TestProviderDetectionFromPath:
    def setup_method(self):
        self.router = ProviderRouter()

    def test_anthropic_messages_path(self):
        result = self.router.route("/v1/messages", {})
        assert result.provider == "anthropic"

    def test_openai_chat_completions_path(self):
        result = self.router.route("/v1/chat/completions", {})
        assert result.provider == "openai"

    def test_openai_codex_responses_path(self):
        result = self.router.route("/v1/responses", {})
        assert result.provider == "openai-codex"

    def test_google_generate_content_path(self):
        result = self.router.route("/v1/models/gemini-pro/generateContent", {})
        assert result.provider == "google"


class TestProviderDetectionFromHeaders:
    def setup_method(self):
        self.router = ProviderRouter()

    def test_anthropic_x_api_key_header(self):
        result = self.router.route("/unknown", {"x-api-key": "sk-ant-test"})
        assert result.provider == "anthropic"

    def test_anthropic_version_header(self):
        result = self.router.route("/unknown", {"anthropic-version": "2023-06-01"})
        assert result.provider == "anthropic"

    def test_openai_bearer_token(self):
        result = self.router.route("/v1/chat/completions", {"Authorization": "Bearer sk-openai"})
        assert result.provider == "openai"

    def test_default_provider_is_anthropic(self):
        result = self.router.route("/unknown", {})
        assert result.provider == "anthropic"


class TestProviderDetectionFromBody:
    def setup_method(self):
        self.router = ProviderRouter()

    def _body(self, model: str) -> bytes:
        return json.dumps({"model": model}).encode()

    def test_claude_model_routes_to_anthropic(self):
        result = self.router.route("/", {}, body=self._body("claude-sonnet-4-6"))
        assert result.provider == "anthropic"

    def test_gpt_model_routes_to_openai(self):
        result = self.router.route("/", {}, body=self._body("gpt-4o"))
        assert result.provider == "openai"

    def test_gemini_model_routes_to_google(self):
        result = self.router.route("/", {}, body=self._body("gemini-pro"))
        assert result.provider == "google"

    def test_codex_model_routes_to_openai_codex(self):
        result = self.router.route("/", {}, body=self._body("gpt-5.2-codex"))
        assert result.provider == "openai-codex"

    def test_invalid_json_body_falls_back_to_default(self):
        result = self.router.route("/", {}, body=b"not-json")
        assert result.provider == "anthropic"


class TestRouteResultFullUrl:
    def setup_method(self):
        self.router = ProviderRouter()

    def test_anthropic_full_url_constructed(self):
        result = self.router.route("/v1/messages", {})
        assert "api.anthropic.com" in result.full_url
        assert result.full_url.endswith("/v1/messages")

    def test_full_url_passthrough_for_http_paths(self):
        url = "https://api.anthropic.com/v1/messages"
        result = self.router.route(url, {})
        assert result.full_url == url
        assert result.provider == "anthropic"

    def test_custom_url_override(self):
        router = ProviderRouter(custom_urls={"anthropic": "https://my-proxy.example.com"})
        result = router.route("/v1/messages", {})
        assert "my-proxy.example.com" in result.full_url

    def test_should_intercept_reverse_proxy(self):
        result = self.router.route("/v1/messages", {})
        assert result.should_intercept is True


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_zero_tokens_returns_zero(self):
        cost = estimate_cost("claude-sonnet-4-5", 0, 0)
        assert cost == 0.0

    def test_known_model_cost(self):
        # claude-sonnet-4-5: input $3/M, output $15/M
        cost = estimate_cost("claude-sonnet-4-5", 1_000_000, 0)
        assert abs(cost - 3.0) < 0.01

    def test_output_tokens_cost(self):
        cost = estimate_cost("claude-sonnet-4-5", 0, 1_000_000)
        assert abs(cost - 15.0) < 0.01

    def test_cache_read_discount(self):
        # cache_read gets 90% discount vs regular input
        cost_regular = estimate_cost("claude-sonnet-4-5", 1_000_000, 0)
        cost_cached = estimate_cost("claude-sonnet-4-5", 1_000_000, 0, cache_read_tokens=1_000_000)
        assert cost_cached < cost_regular

    def test_cache_creation_premium(self):
        cost_base = estimate_cost("claude-sonnet-4-5", 1_000_000, 0)
        cost_creation = estimate_cost(
            "claude-sonnet-4-5", 1_000_000, 0, cache_creation_tokens=1_000_000
        )
        assert cost_creation > cost_base

    def test_unknown_model_uses_default_costs(self):
        cost = estimate_cost("some-unknown-model-v99", 1_000_000, 0)
        assert cost > 0.0  # falls back to DEFAULT_COSTS

    def test_opus_model_costs_more_than_haiku(self):
        opus_cost = estimate_cost("claude-opus-4-5", 1_000_000, 1_000_000)
        haiku_cost = estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000)
        assert opus_cost > haiku_cost


# ---------------------------------------------------------------------------
# get_model_tier
# ---------------------------------------------------------------------------


class TestGetModelTier:
    def test_opus_is_premium(self):
        assert get_model_tier("claude-opus-4-5") == "premium"

    def test_sonnet_is_standard(self):
        assert get_model_tier("claude-sonnet-4-5") == "standard"

    def test_haiku_is_economy(self):
        assert get_model_tier("claude-haiku-4-5") == "economy"

    def test_gpt4o_is_premium(self):
        # gpt-4o contains "gpt-4" so matches premium tier
        assert get_model_tier("gpt-4o") == "premium"

    def test_gpt4o_mini_is_economy(self):
        assert get_model_tier("gpt-4o-mini") == "economy"

    def test_gpt4_turbo_is_premium(self):
        assert get_model_tier("gpt-4-turbo") == "premium"

    def test_unknown_model_returns_unknown(self):
        assert get_model_tier("mystery-model-vX") == "unknown"

    def test_case_insensitive(self):
        assert get_model_tier("CLAUDE-HAIKU-4-5") == "economy"
