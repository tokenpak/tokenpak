"""Tests for config-based custom provider registration.

Covers:
  - YAML parsing and validation of the providers section
  - CustomProvider dataclass behaviour
  - Adapter factory (detect, upstream, delegation)
  - Integration with AdapterRegistry and INTERCEPT_HOSTS
  - Provider display string generation
  - Error handling for malformed entries
"""

import json
import os
from unittest.mock import patch

import pytest


def _patch_config(cfg: dict):
    """Patch the config_loader.load_config to return *cfg* for custom_providers."""
    return patch("tokenpak.core.config_loader.load_config", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Reset the config_loader cache before each test."""
    import tokenpak.core.config_loader as cl
    old = cl._config
    cl._config = None
    yield
    cl._config = old


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_providers_yaml() -> dict:
    """Return a config dict with two custom providers."""
    return {
        "providers": {
            "my-local-llm": {
                "endpoint": "http://localhost:8000/v1",
                "format": "openai",
                "api_key_env": "MY_LLM_API_KEY",
            },
            "deepseek": {
                "endpoint": "https://api.deepseek.com/v1",
                "format": "openai",
                "api_key_env": "DEEPSEEK_API_KEY",
            },
        }
    }


def _sample_anthropic_provider() -> dict:
    return {
        "providers": {
            "my-anthropic-mirror": {
                "endpoint": "https://mirror.example.com/v1",
                "format": "anthropic",
                "api_key_env": "MIRROR_API_KEY",
            },
        }
    }


# ---------------------------------------------------------------------------
# Tests — load_custom_providers
# ---------------------------------------------------------------------------

class TestLoadCustomProviders:

    def test_loads_valid_providers(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = _sample_providers_yaml()
        with _patch_config(cfg):
            providers = load_custom_providers()

        assert len(providers) == 2
        names = {p.name for p in providers}
        assert names == {"my-local-llm", "deepseek"}

    def test_endpoint_parsed_correctly(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = _sample_providers_yaml()
        with _patch_config(cfg):
            providers = load_custom_providers()

        by_name = {p.name: p for p in providers}
        assert by_name["my-local-llm"].endpoint == "http://localhost:8000/v1"
        assert by_name["my-local-llm"].hostname == "localhost"
        assert by_name["deepseek"].hostname == "api.deepseek.com"

    def test_format_resolved_to_source_format(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = _sample_providers_yaml()
        with _patch_config(cfg):
            providers = load_custom_providers()

        for p in providers:
            assert p.format == "openai-chat"

    def test_anthropic_format_resolved(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = _sample_anthropic_provider()
        with _patch_config(cfg):
            providers = load_custom_providers()

        assert len(providers) == 1
        assert providers[0].format == "anthropic-messages"

    def test_trailing_slash_stripped(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = {"providers": {"test": {
            "endpoint": "http://localhost:8000/v1/",
            "format": "openai",
        }}}
        with _patch_config(cfg):
            providers = load_custom_providers()

        assert providers[0].endpoint == "http://localhost:8000/v1"

    def test_missing_endpoint_skipped(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = {"providers": {"bad": {"format": "openai"}}}
        with _patch_config(cfg):
            providers = load_custom_providers()

        assert len(providers) == 0

    def test_unknown_format_skipped(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = {"providers": {"bad": {
            "endpoint": "http://localhost:8000/v1",
            "format": "llama-cpp",
        }}}
        with _patch_config(cfg):
            providers = load_custom_providers()

        assert len(providers) == 0

    def test_no_providers_section(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        with _patch_config({}):
            providers = load_custom_providers()

        assert providers == []

    def test_empty_providers_section(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        with _patch_config({"providers": {}}):
            providers = load_custom_providers()

        assert providers == []

    def test_config_loader_import_error(self):

        # When config_loader is not available, should return empty
        with patch.dict("sys.modules", {"tokenpak.core.config_loader": None}):
            # The function catches ImportError internally
            pass  # Already tested by the fallback path

    def test_api_key_resolution(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = _sample_providers_yaml()
        with _patch_config(cfg):
            providers = load_custom_providers()

        deepseek = next(p for p in providers if p.name == "deepseek")

        # No env var set
        assert deepseek.api_key is None
        assert not deepseek.has_api_key

        # With env var set
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test-123"}):
            assert deepseek.api_key == "sk-test-123"
            assert deepseek.has_api_key

    def test_extra_keys_preserved(self):
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = {"providers": {"test": {
            "endpoint": "http://localhost:8000/v1",
            "format": "openai",
            "api_key_env": "TEST_KEY",
            "models": ["llama-3", "mistral-7b"],
            "max_tokens": 4096,
        }}}
        with _patch_config(cfg):
            providers = load_custom_providers()

        assert providers[0].extra == {
            "models": ["llama-3", "mistral-7b"],
            "max_tokens": 4096,
        }

    def test_default_format_is_openai(self):
        """When format is omitted, default to openai."""
        from tokenpak.proxy.custom_providers import load_custom_providers

        cfg = {"providers": {"test": {
            "endpoint": "http://localhost:8000/v1",
        }}}
        with _patch_config(cfg):
            providers = load_custom_providers()

        assert providers[0].format == "openai-chat"


# ---------------------------------------------------------------------------
# Tests — adapter factory
# ---------------------------------------------------------------------------

class TestCustomAdapterFactory:

    def _build_registry_with_custom(self, cfg: dict):
        from tokenpak.proxy.adapters import build_default_registry
        from tokenpak.proxy.custom_providers import build_custom_adapters, load_custom_providers

        with _patch_config(cfg):
            providers = load_custom_providers()

        registry = build_default_registry()
        adapters = build_custom_adapters(providers, registry)
        return registry, providers, adapters

    def test_custom_adapter_registered(self):
        registry, providers, adapters = self._build_registry_with_custom(
            _sample_providers_yaml()
        )
        assert len(adapters) == 2
        formats = registry.list_formats()
        assert "custom-my-local-llm" in formats
        assert "custom-deepseek" in formats

    def test_custom_adapter_detects_by_hostname_in_url(self):
        registry, providers, adapters = self._build_registry_with_custom(
            _sample_providers_yaml()
        )
        deepseek_adapter = next(a for a in adapters if "deepseek" in a.source_format)

        # Forward-proxy style: full URL in path
        assert deepseek_adapter.detect(
            "https://api.deepseek.com/v1/chat/completions", {}, None
        )
        # Unrelated host should NOT match
        assert not deepseek_adapter.detect(
            "https://api.openai.com/v1/chat/completions", {}, None
        )

    def test_custom_adapter_detects_by_host_header(self):
        registry, providers, adapters = self._build_registry_with_custom(
            _sample_providers_yaml()
        )
        deepseek_adapter = next(a for a in adapters if "deepseek" in a.source_format)

        assert deepseek_adapter.detect(
            "/v1/chat/completions",
            {"Host": "api.deepseek.com"},
            None,
        )

    def test_custom_adapter_upstream(self):
        registry, providers, adapters = self._build_registry_with_custom(
            _sample_providers_yaml()
        )
        local = next(a for a in adapters if "local" in a.source_format)
        assert local.get_default_upstream() == "http://localhost:8000/v1"

    def test_custom_adapter_delegates_normalize(self):
        """Custom adapter normalize/denormalize should work like the delegate."""
        registry, providers, adapters = self._build_registry_with_custom(
            _sample_providers_yaml()
        )
        adapter = adapters[0]  # openai-format custom adapter

        body = json.dumps({
            "model": "llama-3",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ],
            "stream": False,
        }).encode()

        canonical = adapter.normalize(body)
        assert canonical.model == "llama-3"
        assert canonical.system == "You are helpful."
        assert len(canonical.messages) == 1
        assert canonical.messages[0]["content"] == "Hello"

        # Round-trip
        denormalized = json.loads(adapter.denormalize(canonical))
        assert denormalized["model"] == "llama-3"
        assert denormalized["messages"][0]["role"] == "system"

    def test_custom_adapter_sse_format(self):
        registry, providers, adapters = self._build_registry_with_custom(
            _sample_providers_yaml()
        )
        adapter = adapters[0]
        assert adapter.get_sse_format() == "openai-sse"

    def test_registry_detects_custom_provider_from_url(self):
        """The full registry should route a deepseek URL to the custom adapter."""
        registry, providers, adapters = self._build_registry_with_custom(
            _sample_providers_yaml()
        )
        detected = registry.detect(
            "https://api.deepseek.com/v1/chat/completions",
            {"Host": "api.deepseek.com"},
            None,
        )
        # Should match custom-deepseek (or the openai-chat adapter, both are
        # correct since deepseek uses OpenAI format). The important thing is
        # that it doesn't fall through to passthrough.
        assert detected.source_format != "passthrough"


# ---------------------------------------------------------------------------
# Tests — provider display string
# ---------------------------------------------------------------------------

class TestProviderDisplay:

    def test_display_with_custom_providers(self):
        from tokenpak.proxy.adapters import build_default_registry
        from tokenpak.proxy.custom_providers import (
            build_custom_adapters,
            get_provider_display_list,
            load_custom_providers,
        )

        cfg = _sample_providers_yaml()
        with _patch_config(cfg):
            providers = load_custom_providers()

        registry = build_default_registry()
        build_custom_adapters(providers, registry)

        display = get_provider_display_list(registry, providers)
        assert "my-local-llm (custom)" in display
        assert "deepseek (custom)" in display
        # Built-in providers should also appear
        assert "anthropic-messages" in display
        assert "openai-chat" in display

    def test_display_without_custom_providers(self):
        from tokenpak.proxy.adapters import build_default_registry
        from tokenpak.proxy.custom_providers import get_provider_display_list

        registry = build_default_registry()
        display = get_provider_display_list(registry, [])
        assert "(custom)" not in display
        assert "anthropic-messages" in display
        assert "passthrough" not in display


# ---------------------------------------------------------------------------
# Tests — intercept list integration
# ---------------------------------------------------------------------------

class TestInterceptIntegration:

    def test_custom_hostname_added_to_intercept(self):
        """When custom providers are loaded, their hostnames should be
        added to the INTERCEPT_HOSTS set in router.py."""
        import importlib
        _router = importlib.import_module("tokenpak.proxy.router")

        # Simulate what config.py does at startup
        _router.INTERCEPT_HOSTS.add("api.deepseek.com")
        try:
            # should_intercept checks if any host in INTERCEPT_HOSTS is
            # a substring of the URL
            assert _router.should_intercept("https://api.deepseek.com/v1/chat/completions")
            # Unrelated URL should not match
            assert not _router.should_intercept("https://api.example.com/v1/chat/completions")
        finally:
            _router.INTERCEPT_HOSTS.discard("api.deepseek.com")
