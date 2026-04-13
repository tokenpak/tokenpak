# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.runtime.cache_spec — CacheSpec, resolve_cache_mode, etc."""

import pytest
from tokenpak.runtime.providers import Provider
from tokenpak.runtime.cache_spec import (
    CacheMode,
    CacheSpec,
    FallbackPolicy,
    PROVIDER_CACHE_MODES,
    default_cache_mode,
    load_cache_spec_from_config,
    resolve_cache_mode,
)


# ---------------------------------------------------------------------------
# CacheMode enum
# ---------------------------------------------------------------------------

class TestCacheModeEnum:
    def test_values(self):
        assert CacheMode.PREFIX_AUTO.value == "prefix_auto"
        assert CacheMode.BLOCK_EXPLICIT.value == "block_explicit"
        assert CacheMode.CACHE_OBJECT.value == "cache_object"
        assert CacheMode.CHECKPOINT.value == "checkpoint"

    def test_count(self):
        assert len(CacheMode) == 4


class TestFallbackPolicyEnum:
    def test_values(self):
        assert FallbackPolicy.STRICT.value == "strict"
        assert FallbackPolicy.BEST_EFFORT.value == "best-effort"
        assert FallbackPolicy.PROVIDER_DEFAULT.value == "provider-default"


# ---------------------------------------------------------------------------
# PROVIDER_CACHE_MODES capability map
# ---------------------------------------------------------------------------

class TestProviderCacheModes:
    def test_anthropic_supports_block_explicit_first(self):
        modes = PROVIDER_CACHE_MODES[Provider.ANTHROPIC]
        assert modes[0] == CacheMode.BLOCK_EXPLICIT
        assert CacheMode.PREFIX_AUTO in modes

    def test_openai_supports_prefix_auto_only(self):
        modes = PROVIDER_CACHE_MODES[Provider.OPENAI]
        assert modes == [CacheMode.PREFIX_AUTO]

    def test_gemini_supports_cache_object_only(self):
        assert PROVIDER_CACHE_MODES[Provider.GEMINI] == [CacheMode.CACHE_OBJECT]

    def test_bedrock_supports_checkpoint_and_block_explicit(self):
        modes = PROVIDER_CACHE_MODES[Provider.BEDROCK]
        assert CacheMode.CHECKPOINT in modes
        assert CacheMode.BLOCK_EXPLICIT in modes
        assert modes[0] == CacheMode.CHECKPOINT  # checkpoint is default

    def test_unknown_has_no_modes(self):
        assert PROVIDER_CACHE_MODES[Provider.UNKNOWN] == []

    def test_all_providers_present(self):
        # Every provider that handles LLM completions must have an entry.
        # Embedding-only providers (VOYAGE, JINA) are intentionally absent.
        required = {
            Provider.ANTHROPIC, Provider.OPENAI, Provider.AZURE_OPENAI,
            Provider.XAI, Provider.GROQ, Provider.FIREWORKS, Provider.TOGETHER,
            Provider.CODEX, Provider.GEMINI, Provider.BEDROCK, Provider.UNKNOWN,
        }
        for p in required:
            assert p in PROVIDER_CACHE_MODES, f"{p} missing from PROVIDER_CACHE_MODES"


# ---------------------------------------------------------------------------
# default_cache_mode
# ---------------------------------------------------------------------------

class TestDefaultCacheMode:
    def test_anthropic_default_is_block_explicit(self):
        assert default_cache_mode(Provider.ANTHROPIC) == CacheMode.BLOCK_EXPLICIT

    def test_openai_default_is_prefix_auto(self):
        assert default_cache_mode(Provider.OPENAI) == CacheMode.PREFIX_AUTO

    def test_gemini_default_is_cache_object(self):
        assert default_cache_mode(Provider.GEMINI) == CacheMode.CACHE_OBJECT

    def test_bedrock_default_is_checkpoint(self):
        assert default_cache_mode(Provider.BEDROCK) == CacheMode.CHECKPOINT

    def test_unknown_returns_none(self):
        assert default_cache_mode(Provider.UNKNOWN) is None


# ---------------------------------------------------------------------------
# CacheSpec dataclass
# ---------------------------------------------------------------------------

class TestCacheSpec:
    def test_defaults(self):
        spec = CacheSpec()
        assert spec.enabled is True
        assert spec.default_mode is None
        assert spec.fallback_policy == FallbackPolicy.BEST_EFFORT
        assert spec.provider_overrides == {}

    def test_explicit_construction(self):
        spec = CacheSpec(
            enabled=False,
            default_mode=CacheMode.PREFIX_AUTO,
            fallback_policy=FallbackPolicy.STRICT,
            provider_overrides={"anthropic": {"mode": "block_explicit"}},
        )
        assert spec.enabled is False
        assert spec.default_mode == CacheMode.PREFIX_AUTO
        assert spec.fallback_policy == FallbackPolicy.STRICT
        assert spec.provider_overrides["anthropic"]["mode"] == "block_explicit"


# ---------------------------------------------------------------------------
# resolve_cache_mode — the main resolution function
# ---------------------------------------------------------------------------

class TestResolveCacheMode:
    def _spec(self, **kwargs):
        return CacheSpec(**kwargs)

    # --- disabled cache ---

    def test_disabled_cache_returns_none(self):
        spec = CacheSpec(enabled=False)
        assert resolve_cache_mode(spec, Provider.ANTHROPIC) is None

    def test_disabled_cache_ignores_request_hint(self):
        spec = CacheSpec(enabled=False)
        assert resolve_cache_mode(spec, Provider.ANTHROPIC, "block_explicit") is None

    # --- request-level hint (priority 1) ---

    def test_request_hint_supported_mode_used(self):
        spec = CacheSpec()
        result = resolve_cache_mode(spec, Provider.ANTHROPIC, "prefix_auto")
        assert result == CacheMode.PREFIX_AUTO

    def test_request_hint_unknown_string_ignored(self):
        spec = CacheSpec()
        # Unknown hint string — should fall through to provider default
        result = resolve_cache_mode(spec, Provider.ANTHROPIC, "nonexistent_mode")
        assert result == CacheMode.BLOCK_EXPLICIT  # provider default

    def test_request_hint_unsupported_mode_best_effort(self):
        spec = CacheSpec(fallback_policy=FallbackPolicy.BEST_EFFORT)
        # OpenAI only supports prefix_auto — request checkpoint → downgrade
        result = resolve_cache_mode(spec, Provider.OPENAI, "checkpoint")
        assert result == CacheMode.PREFIX_AUTO

    def test_request_hint_unsupported_mode_strict_raises(self):
        spec = CacheSpec(fallback_policy=FallbackPolicy.STRICT)
        with pytest.raises(ValueError, match="does not support cache mode"):
            resolve_cache_mode(spec, Provider.OPENAI, "checkpoint")

    def test_request_hint_unsupported_mode_provider_default_returns_none(self):
        spec = CacheSpec(fallback_policy=FallbackPolicy.PROVIDER_DEFAULT)
        result = resolve_cache_mode(spec, Provider.OPENAI, "checkpoint")
        assert result is None

    # --- provider-level override (priority 2) ---

    def test_provider_override_valid_supported_mode(self):
        spec = CacheSpec(
            provider_overrides={"anthropic": {"mode": "prefix_auto"}}
        )
        result = resolve_cache_mode(spec, Provider.ANTHROPIC)
        assert result == CacheMode.PREFIX_AUTO

    def test_provider_override_invalid_mode_string_falls_through(self):
        spec = CacheSpec(
            provider_overrides={"anthropic": {"mode": "nonexistent"}}
        )
        # Invalid override value falls through to provider default
        result = resolve_cache_mode(spec, Provider.ANTHROPIC)
        assert result == CacheMode.BLOCK_EXPLICIT

    def test_provider_override_unsupported_mode_best_effort(self):
        spec = CacheSpec(
            fallback_policy=FallbackPolicy.BEST_EFFORT,
            provider_overrides={"openai": {"mode": "checkpoint"}},
        )
        # OpenAI doesn't support checkpoint — downgrade
        result = resolve_cache_mode(spec, Provider.OPENAI)
        assert result == CacheMode.PREFIX_AUTO

    def test_provider_override_only_applies_to_matching_provider(self):
        spec = CacheSpec(
            provider_overrides={"anthropic": {"mode": "prefix_auto"}}
        )
        # Override is for anthropic; OpenAI should use its provider default
        result = resolve_cache_mode(spec, Provider.OPENAI)
        assert result == CacheMode.PREFIX_AUTO  # openai default

    # --- global default_mode (priority 3) ---

    def test_global_default_mode_used_when_supported(self):
        spec = CacheSpec(default_mode=CacheMode.PREFIX_AUTO)
        result = resolve_cache_mode(spec, Provider.OPENAI)
        assert result == CacheMode.PREFIX_AUTO

    def test_global_default_mode_unsupported_best_effort(self):
        spec = CacheSpec(
            default_mode=CacheMode.CHECKPOINT,
            fallback_policy=FallbackPolicy.BEST_EFFORT,
        )
        # CHECKPOINT not in OpenAI modes → downgrade to PREFIX_AUTO
        result = resolve_cache_mode(spec, Provider.OPENAI)
        assert result == CacheMode.PREFIX_AUTO

    def test_global_default_mode_unsupported_strict_raises(self):
        spec = CacheSpec(
            default_mode=CacheMode.CHECKPOINT,
            fallback_policy=FallbackPolicy.STRICT,
        )
        with pytest.raises(ValueError, match="does not support default cache mode"):
            resolve_cache_mode(spec, Provider.OPENAI)

    def test_global_default_mode_unsupported_provider_default_returns_none(self):
        spec = CacheSpec(
            default_mode=CacheMode.CHECKPOINT,
            fallback_policy=FallbackPolicy.PROVIDER_DEFAULT,
        )
        result = resolve_cache_mode(spec, Provider.OPENAI)
        assert result is None

    # --- provider default (priority 4) ---

    def test_provider_default_when_no_overrides(self):
        spec = CacheSpec()
        assert resolve_cache_mode(spec, Provider.ANTHROPIC) == CacheMode.BLOCK_EXPLICIT
        assert resolve_cache_mode(spec, Provider.OPENAI) == CacheMode.PREFIX_AUTO
        assert resolve_cache_mode(spec, Provider.GEMINI) == CacheMode.CACHE_OBJECT
        assert resolve_cache_mode(spec, Provider.BEDROCK) == CacheMode.CHECKPOINT

    def test_unknown_provider_returns_none(self):
        spec = CacheSpec()
        assert resolve_cache_mode(spec, Provider.UNKNOWN) is None

    # --- request_hint overrides all lower priorities ---

    def test_request_hint_beats_provider_override(self):
        spec = CacheSpec(
            provider_overrides={"anthropic": {"mode": "block_explicit"}}
        )
        result = resolve_cache_mode(spec, Provider.ANTHROPIC, "prefix_auto")
        assert result == CacheMode.PREFIX_AUTO

    def test_request_hint_beats_global_default(self):
        spec = CacheSpec(default_mode=CacheMode.BLOCK_EXPLICIT)
        result = resolve_cache_mode(spec, Provider.ANTHROPIC, "prefix_auto")
        assert result == CacheMode.PREFIX_AUTO


# ---------------------------------------------------------------------------
# load_cache_spec_from_config
# ---------------------------------------------------------------------------

class TestLoadCacheSpecFromConfig:
    def _make_cfg(self, data: dict):
        """Return a cfg_fn that mimics the tokenpak config accessor."""
        def cfg_fn(key, default=None, env_var=None, cast=None):
            val = data.get(key, default)
            if val is None:
                return default
            if cast is not None and not isinstance(val, cast):
                try:
                    return cast(val)
                except (ValueError, TypeError):
                    return default
            return val
        return cfg_fn

    def test_defaults_when_no_config(self):
        cfg = self._make_cfg({})
        spec = load_cache_spec_from_config(cfg)
        assert spec.enabled is True
        assert spec.default_mode is None
        assert spec.fallback_policy == FallbackPolicy.BEST_EFFORT
        assert spec.provider_overrides == {}

    def test_cache_disabled(self):
        cfg = self._make_cfg({"cache.enabled": False})
        spec = load_cache_spec_from_config(cfg)
        assert spec.enabled is False

    def test_fallback_policy_strict(self):
        cfg = self._make_cfg({"cache.fallback_policy": "strict"})
        spec = load_cache_spec_from_config(cfg)
        assert spec.fallback_policy == FallbackPolicy.STRICT

    def test_fallback_policy_provider_default(self):
        cfg = self._make_cfg({"cache.fallback_policy": "provider-default"})
        spec = load_cache_spec_from_config(cfg)
        assert spec.fallback_policy == FallbackPolicy.PROVIDER_DEFAULT

    def test_invalid_fallback_policy_defaults_to_best_effort(self):
        cfg = self._make_cfg({"cache.fallback_policy": "invalid-value"})
        spec = load_cache_spec_from_config(cfg)
        assert spec.fallback_policy == FallbackPolicy.BEST_EFFORT

    def test_default_mode_set(self):
        cfg = self._make_cfg({"cache.default_mode": "prefix_auto"})
        spec = load_cache_spec_from_config(cfg)
        assert spec.default_mode == CacheMode.PREFIX_AUTO

    def test_invalid_default_mode_leaves_none(self):
        cfg = self._make_cfg({"cache.default_mode": "nonexistent_mode"})
        spec = load_cache_spec_from_config(cfg)
        assert spec.default_mode is None

    def test_provider_override_loaded(self):
        cfg = self._make_cfg({"cache.anthropic.mode": "prefix_auto"})
        spec = load_cache_spec_from_config(cfg)
        assert "anthropic" in spec.provider_overrides
        assert spec.provider_overrides["anthropic"]["mode"] == "prefix_auto"

    def test_multiple_provider_overrides(self):
        cfg = self._make_cfg({
            "cache.anthropic.mode": "block_explicit",
            "cache.openai.mode": "prefix_auto",
        })
        spec = load_cache_spec_from_config(cfg)
        assert spec.provider_overrides.get("anthropic", {}).get("mode") == "block_explicit"
        assert spec.provider_overrides.get("openai", {}).get("mode") == "prefix_auto"

    def test_unknown_provider_not_in_overrides(self):
        cfg = self._make_cfg({})
        spec = load_cache_spec_from_config(cfg)
        # UNKNOWN provider should be skipped
        assert "unknown" not in spec.provider_overrides
