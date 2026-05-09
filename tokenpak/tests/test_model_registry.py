# SPDX-License-Identifier: Apache-2.0
"""Tests for the dynamic model registry (tokenpak.models)."""

from __future__ import annotations

import threading

from tokenpak.models import (
    detect_provider,
    get_all_tiers,
    get_cheaper_alternative,
    get_default_routes,
    get_model_costs,
    get_pricing,
    get_rates,
    get_shadow_target,
    get_tier,
    known_models,
    translate_model,
)

# ---------------------------------------------------------------------------
# Known models: exact match from seed catalog
# ---------------------------------------------------------------------------


class TestKnownModels:
    def test_opus_4_6_rates(self):
        rates = get_rates("claude-opus-4-6")
        assert rates["input"] == 15.0
        assert rates["output"] == 75.0
        assert rates["cached"] == 1.5

    def test_sonnet_4_6_rates(self):
        rates = get_rates("claude-sonnet-4-6")
        assert rates["input"] == 3.0
        assert rates["output"] == 15.0
        assert rates["cached"] == 0.3

    def test_haiku_4_5_rates(self):
        rates = get_rates("claude-haiku-4-5")
        assert rates["input"] == 0.8
        assert rates["output"] == 4.0
        assert rates["cached"] == 0.08

    def test_gpt_4o_rates(self):
        rates = get_rates("gpt-4o")
        assert rates["input"] == 2.5
        assert rates["output"] == 10.0

    def test_default_no_model(self):
        rates = get_rates()
        assert rates["input"] == 3.0
        assert rates["output"] == 15.0

    def test_tier_opus_4_6(self):
        assert get_tier("claude-opus-4-6") == 4

    def test_tier_sonnet(self):
        assert get_tier("claude-sonnet-4-6") == 2

    def test_tier_haiku(self):
        assert get_tier("claude-haiku-4-5") == 1


# ---------------------------------------------------------------------------
# Unknown models: family-based inference (THE KEY TEST)
# ---------------------------------------------------------------------------


class TestUnknownModels:
    """These tests verify that completely new models resolve correctly
    through family-pattern matching — the core capability that makes
    the system dynamic.
    """

    def test_opus_4_7_rates(self):
        """A future Opus release should get Opus-family pricing."""
        rates = get_rates("claude-opus-4-7")
        assert rates["input"] == 15.0
        assert rates["output"] == 75.0
        assert rates["cached"] == 1.5

    def test_opus_4_7_tier(self):
        assert get_tier("claude-opus-4-7") == 4

    def test_opus_4_7_bedrock(self):
        result = translate_model("claude-opus-4-7", "bedrock")
        assert result == "anthropic.claude-opus-4-7-v1:0"

    def test_opus_4_7_vertex(self):
        result = translate_model("claude-opus-4-7", "vertex")
        assert result == "claude-opus-4-7@latest"

    def test_sonnet_5_0_rates(self):
        """A future Sonnet release should get Sonnet-family pricing."""
        rates = get_rates("claude-sonnet-5-0")
        assert rates["input"] == 3.0
        assert rates["output"] == 15.0

    def test_sonnet_5_0_tier(self):
        assert get_tier("claude-sonnet-5-0") == 2

    def test_haiku_5_0_rates(self):
        rates = get_rates("claude-haiku-5-0")
        assert rates["input"] == 0.8
        assert rates["output"] == 4.0
        assert rates["cached"] == 0.08

    def test_haiku_5_0_tier(self):
        assert get_tier("claude-haiku-5-0") == 1

    def test_gpt_5_tier(self):
        """A new gpt-5 model should resolve to frontier tier."""
        assert get_tier("gpt-5-turbo") == 4

    def test_completely_unknown_model(self):
        """A model from an unknown family defaults to sonnet-class pricing."""
        rates = get_rates("some-random-model-v2")
        assert rates["input"] == 3.0
        assert rates["output"] == 15.0

    def test_completely_unknown_tier(self):
        assert get_tier("some-random-model-v2") == 2


# ---------------------------------------------------------------------------
# Resolution chain
# ---------------------------------------------------------------------------


class TestResolution:
    def test_alias_resolution(self):
        """Aliases (like 'claude-3-5-sonnet-latest') resolve to the canonical model."""
        info = get_pricing("claude-3-5-sonnet-latest")
        assert info is not None
        assert info.provider == "anthropic"
        assert info.tier == 2

    def test_date_suffix_stripping(self):
        """Model IDs with date suffixes resolve to the base model."""
        rates = get_rates("claude-opus-4-6-20260515")
        assert rates["input"] == 15.0

    def test_prefix_match(self):
        """Models sharing a prefix with a known model resolve via prefix matching."""
        info = get_pricing("claude-opus-4-6-extended")
        assert info is not None
        assert info.input_per_mtok == 15.0

    def test_empty_string(self):
        assert get_pricing("") is None
        assert get_pricing(None) is None

    def test_none_model_rates(self):
        rates = get_rates(None)
        assert rates["input"] == 3.0


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


class TestProviderDetection:
    def test_anthropic(self):
        assert detect_provider("claude-opus-4-7") == "anthropic"

    def test_openai(self):
        assert detect_provider("gpt-4o") == "openai"

    def test_google(self):
        assert detect_provider("gemini-2-flash") == "google"

    def test_ollama(self):
        assert detect_provider("llama-3-70b") == "ollama"

    def test_unknown(self):
        assert detect_provider("some-random-model") == "unknown"


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


class TestTranslation:
    def test_known_bedrock(self):
        result = translate_model("claude-sonnet-4-6", "bedrock")
        assert result == "anthropic.claude-sonnet-4-6-20260101-v1:0"

    def test_known_vertex(self):
        result = translate_model("claude-sonnet-4-6", "vertex")
        assert result == "claude-sonnet-4-6@20260101"

    def test_unknown_model_bedrock(self):
        """Family template generates a bedrock translation for unknown models."""
        result = translate_model("claude-sonnet-5-0", "bedrock")
        assert result == "anthropic.claude-sonnet-5-0-v1:0"

    def test_no_translation(self):
        """Pass-through when no translation exists."""
        result = translate_model("gpt-4o", "bedrock")
        assert result == "gpt-4o"  # no bedrock translation for OpenAI models


# ---------------------------------------------------------------------------
# Shadow targets
# ---------------------------------------------------------------------------


class TestShadowTargets:
    def test_haiku(self):
        url, model = get_shadow_target("haiku")
        assert url == "https://api.anthropic.com/v1/messages"
        assert model == "claude-haiku-4-5"

    def test_sonnet(self):
        url, model = get_shadow_target("anthropic-sonnet")
        assert url == "https://api.anthropic.com/v1/messages"
        assert model == "claude-sonnet-4-6"

    def test_unknown(self):
        url, model = get_shadow_target("gemini-flash")
        assert url == ""
        assert model == ""


# ---------------------------------------------------------------------------
# Cheaper alternatives
# ---------------------------------------------------------------------------


class TestCheaperAlternative:
    def test_opus_has_cheaper(self):
        result = get_cheaper_alternative("claude-opus-4-5")
        assert result is not None
        model, savings = result
        assert savings > 0

    def test_haiku_no_cheaper(self):
        assert get_cheaper_alternative("claude-haiku-4-5") is None

    def test_unknown_opus_has_cheaper(self):
        """Even an unknown opus model should find a cheaper alternative."""
        result = get_cheaper_alternative("claude-opus-4-7")
        assert result is not None


# ---------------------------------------------------------------------------
# Registry introspection
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_known_models_not_empty(self):
        models = known_models()
        assert len(models) > 20

    def test_default_routes(self):
        routes = get_default_routes()
        assert routes["claude-opus-4-6"] == "anthropic"
        assert routes["gpt-4o"] == "openai"

    def test_all_tiers(self):
        tiers = get_all_tiers()
        assert tiers["claude-opus-4-6"] == 4
        # Provider-prefixed version
        assert tiers["anthropic/claude-opus-4-6"] == 4

    def test_model_costs(self):
        costs = get_model_costs("claude-sonnet-4-6")
        assert costs == {"input": 3.0, "output": 15.0}


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_resolve(self):
        """Registry is safe under concurrent reads."""
        results = []
        errors = []

        def resolve_model(model_id):
            try:
                info = get_pricing(model_id)
                results.append(info)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=resolve_model, args=(f"claude-opus-{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10


# ---------------------------------------------------------------------------
# Regression: verify existing model data matches old hardcoded values
# ---------------------------------------------------------------------------


class TestRegressionValues:
    """Ensure the registry returns the same values that were previously
    hardcoded in the various inline dicts.
    """

    def test_opus_4_6_matches_old_proxy(self):
        costs = get_model_costs("claude-opus-4-6")
        assert costs["input"] == 15.0
        assert costs["output"] == 75.0

    def test_sonnet_4_6_matches_old_proxy(self):
        costs = get_model_costs("claude-sonnet-4-6")
        assert costs["input"] == 3.0
        assert costs["output"] == 15.0

    def test_haiku_4_5_matches_old_companion(self):
        rates = get_rates("claude-haiku-4-5")
        assert rates["input"] == 0.8
        assert rates["output"] == 4.0
        assert rates["cached"] == 0.08

    def test_gpt_4o_matches_old_catalog(self):
        costs = get_model_costs("gpt-4o")
        assert costs["input"] == 2.5
        assert costs["output"] == 10.0

    def test_o3_matches_old_companion(self):
        costs = get_model_costs("o3")
        assert costs["input"] == 10.0
        assert costs["output"] == 40.0
