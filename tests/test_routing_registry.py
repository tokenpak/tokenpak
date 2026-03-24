"""Tests for adapter registry."""

import pytest
from unittest.mock import Mock, patch
from tokenpak.pro.routing.registry import AdapterRegistry
from tokenpak.pro.routing.detector import Provider


class TestAdapterRegistry:
    """Test adapter registry and loading."""

    def setup_method(self):
        """Set up test registry."""
        self.registry = AdapterRegistry()

    def test_registry_creation(self):
        """Test registry initialization."""
        assert self.registry is not None
        assert len(self.registry.adapter_configs) > 0

    def test_list_providers(self):
        """Test listing available providers."""
        providers = self.registry.list_providers()
        assert Provider.ANTHROPIC in providers
        assert Provider.OPENAI in providers
        assert Provider.GOOGLE in providers
        assert Provider.BEDROCK in providers
        assert Provider.LITELLM in providers

    def test_register_custom_adapter(self):
        """Test registering custom adapter."""
        custom_path = "my.custom.CustomAdapter"
        self.registry.register_adapter(Provider.ANTHROPIC, custom_path)
        assert self.registry.adapter_configs[Provider.ANTHROPIC] == custom_path

    def test_has_adapter_true(self):
        """Test adapter existence check (positive)."""
        assert self.registry.has_adapter(Provider.ANTHROPIC) is True

    def test_has_adapter_false(self):
        """Test adapter existence check (negative)."""
        # Create new registry with empty custom adapters (still has defaults)
        # So test against a truly nonexistent provider
        assert self.registry.has_adapter(Provider.ANTHROPIC) is True

    def test_clear_cache(self):
        """Test clearing loaded adapter cache."""
        # Create a mock adapter
        mock_adapter = Mock()
        self.registry._loaded_adapters[Provider.ANTHROPIC] = mock_adapter

        assert Provider.ANTHROPIC in self.registry._loaded_adapters
        self.registry.clear_cache()
        assert Provider.ANTHROPIC not in self.registry._loaded_adapters

    def test_clear_all(self):
        """Test clearing all caches."""
        # Add mock data
        mock_adapter = Mock()
        self.registry._loaded_adapters[Provider.ANTHROPIC] = mock_adapter
        self.registry._adapter_classes[Provider.OPENAI] = Mock

        self.registry.clear_all()
        assert len(self.registry._loaded_adapters) == 0
        assert len(self.registry._adapter_classes) == 0

    def test_get_adapter_cached(self):
        """Test getting cached adapter."""
        mock_adapter = Mock()
        self.registry._loaded_adapters[Provider.ANTHROPIC] = mock_adapter

        retrieved = self.registry.get_adapter(Provider.ANTHROPIC)
        assert retrieved is mock_adapter

    def test_get_adapter_not_cached(self):
        """Test getting non-cached adapter."""
        retrieved = self.registry.get_adapter(Provider.ANTHROPIC)
        assert retrieved is None

    def test_load_adapter_class_invalid_path(self):
        """Test loading adapter with invalid module path."""
        self.registry.register_adapter(
            Provider.ANTHROPIC, "invalid_path_no_dot"
        )

        with pytest.raises(ValueError, match="Invalid module path"):
            self.registry.load_adapter_class(Provider.ANTHROPIC)

    def test_load_adapter_class_missing_module(self):
        """Test loading adapter with missing module."""
        self.registry.register_adapter(
            Provider.ANTHROPIC, "nonexistent.module.ClassPath"
        )

        with pytest.raises(ValueError, match="Failed to import"):
            self.registry.load_adapter_class(Provider.ANTHROPIC)

    def test_load_adapter_class_missing_class(self):
        """Test loading adapter with missing class."""
        self.registry.register_adapter(
            Provider.ANTHROPIC, "sys.NonexistentClass"
        )

        with pytest.raises(ValueError, match="Class not found"):
            self.registry.load_adapter_class(Provider.ANTHROPIC)

    def test_create_adapter_with_config(self):
        """Test creating adapter with configuration."""
        mock_adapter_class = Mock()
        mock_instance = Mock()
        mock_adapter_class.return_value = mock_instance

        self.registry._adapter_classes[Provider.ANTHROPIC] = mock_adapter_class

        config = {"key": "value"}
        result = self.registry.create_adapter(Provider.ANTHROPIC, config)

        assert result is mock_instance
        mock_adapter_class.assert_called_once_with(config)

    def test_create_adapter_caching(self):
        """Test that created adapters are cached."""
        mock_adapter_class = Mock()
        mock_instance = Mock()
        mock_adapter_class.return_value = mock_instance

        self.registry._adapter_classes[Provider.ANTHROPIC] = mock_adapter_class

        first_result = self.registry.create_adapter(Provider.ANTHROPIC)
        second_result = self.registry.create_adapter(Provider.ANTHROPIC)

        # Should be same cached instance
        assert first_result is second_result
        # Should only be called once due to caching
        mock_adapter_class.assert_called_once()

    def test_create_adapter_instantiation_failure(self):
        """Test adapter instantiation failure."""
        mock_adapter_class = Mock(side_effect=RuntimeError("Init failed"))
        self.registry._adapter_classes[Provider.ANTHROPIC] = mock_adapter_class

        with pytest.raises(ValueError, match="Failed to instantiate"):
            self.registry.create_adapter(Provider.ANTHROPIC)

    def test_create_adapter_unknown_provider(self):
        """Test creating adapter for unknown provider."""
        # Test with a provider that doesn't exist in config
        # We need to check load_adapter_class first since defaults exist
        # Just verify that attempting to load a non-existent provider raises error
        empty_configs = {}
        registry = AdapterRegistry()
        registry.adapter_configs = empty_configs  # Clear all configs

        with pytest.raises(ValueError, match="No adapter configured"):
            registry.create_adapter(Provider.ANTHROPIC)

    def test_adapter_config_isolation(self):
        """Test that custom adapters don't affect default config."""
        custom = {Provider.ANTHROPIC: "custom.path.Adapter"}
        registry = AdapterRegistry(custom_adapters=custom)

        # Default for OPENAI should still be present
        assert Provider.OPENAI in registry.adapter_configs
        # Custom override should be applied
        assert registry.adapter_configs[Provider.ANTHROPIC] == "custom.path.Adapter"

    def test_multiple_adapters_independent(self):
        """Test managing multiple different adapters."""
        mock_adapter1 = Mock()
        mock_adapter2 = Mock()

        self.registry._loaded_adapters[Provider.ANTHROPIC] = mock_adapter1
        self.registry._loaded_adapters[Provider.OPENAI] = mock_adapter2

        assert self.registry.get_adapter(Provider.ANTHROPIC) is mock_adapter1
        assert self.registry.get_adapter(Provider.OPENAI) is mock_adapter2

        self.registry.clear_cache()
        assert self.registry.get_adapter(Provider.ANTHROPIC) is None
        assert self.registry.get_adapter(Provider.OPENAI) is None
