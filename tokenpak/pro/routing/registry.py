"""Adapter registry and loader for multi-provider support."""

import importlib
from typing import Any, Dict, Optional, Type

from .detector import Provider


class AdapterRegistry:
    """Manages and loads adapters for different providers."""

    # Default adapter module paths
    DEFAULT_ADAPTERS = {
        Provider.ANTHROPIC: "tokenpak.adapters.anthropic.AnthropicAdapter",
        Provider.OPENAI: "tokenpak.adapters.openai.OpenAIAdapter",
        Provider.GOOGLE: "tokenpak.adapters.google.GoogleAdapter",
        Provider.BEDROCK: "tokenpak.adapters.bedrock.BedrockAdapter",
        Provider.LITELLM: "tokenpak.adapters.litellm.LiteLLMAdapter",
    }

    def __init__(self, custom_adapters: Optional[Dict[Provider, str]] = None):
        """
        Initialize registry.

        Args:
            custom_adapters: Optional custom adapter paths (provider -> module path)
        """
        self.adapter_configs = self.DEFAULT_ADAPTERS.copy()
        if custom_adapters:
            self.adapter_configs.update(custom_adapters)

        self._loaded_adapters: Dict[Provider, Any] = {}
        self._adapter_classes: Dict[Provider, Type] = {}
        self._explicitly_registered: set = set()  # Track explicitly registered providers

    def register_adapter(self, provider: Provider, module_path: str) -> None:
        """
        Register a custom adapter.

        Args:
            provider: Provider enum
            module_path: Full module path (e.g., "package.module.ClassName")
        """
        self.adapter_configs[provider] = module_path

    def register(self, provider: str, adapter_class: Type) -> None:
        """
        Public API: register an adapter class directly.

        Args:
            provider: Provider name (string)
            adapter_class: The adapter class to register
        """
        # Convert string provider to Provider enum
        try:
            prov_enum = Provider(provider)
        except ValueError:
            prov_enum = Provider[provider.upper()]

        # Store the class for later instantiation
        self._adapter_classes[prov_enum] = adapter_class
        self._explicitly_registered.add(prov_enum)

    def load_adapter_class(self, provider: Provider) -> Type:
        """
        Load adapter class for provider.

        Args:
            provider: Provider enum

        Returns:
            The adapter class

        Raises:
            ValueError: If adapter not found or import fails
        """
        if provider in self._adapter_classes:
            return self._adapter_classes[provider]

        if provider not in self.adapter_configs:
            raise ValueError(f"No adapter configured for {provider}")

        module_path = self.adapter_configs[provider]

        try:
            # Split module path into module and class name
            parts = module_path.rsplit(".", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid module path: {module_path}")

            module_name, class_name = parts
            module = importlib.import_module(module_name)
            adapter_class = getattr(module, class_name)

            self._adapter_classes[provider] = adapter_class
            return adapter_class

        except ImportError as e:
            raise ValueError(f"Failed to import adapter for {provider}: {e}")
        except AttributeError as e:
            raise ValueError(f"Class not found in {module_path}: {e}")

    def create_adapter(self, provider: Provider, config: Optional[Dict[str, Any]] = None) -> Any:
        """
        Create and cache an adapter instance.

        Args:
            provider: Provider enum
            config: Optional adapter configuration

        Returns:
            Initialized adapter instance

        Raises:
            ValueError: If adapter class cannot be loaded
        """
        if provider in self._loaded_adapters:
            return self._loaded_adapters[provider]

        adapter_class = self.load_adapter_class(provider)

        try:
            adapter = adapter_class(config or {})
            self._loaded_adapters[provider] = adapter
            return adapter
        except Exception as e:
            raise ValueError(f"Failed to instantiate {provider} adapter: {e}")

    def get_adapter(self, provider: Provider) -> Any:
        """
        Get cached adapter instance.

        Args:
            provider: Provider enum

        Returns:
            Cached adapter or None if not loaded
        """
        return self._loaded_adapters.get(provider)

    def has_adapter(self, provider: Provider) -> bool:
        """Check if adapter is registered."""
        return provider in self.adapter_configs

    def list_providers(self) -> list:
        """Get list of available providers."""
        return list(self.adapter_configs.keys())

    def get_all_providers(self) -> list:
        """
        Public API: get list of explicitly registered providers.

        Returns list of Provider enums (only those explicitly registered via register()).
        """
        return list(self._explicitly_registered)

    def clear_cache(self) -> None:
        """Clear cached adapter instances."""
        self._loaded_adapters.clear()

    def clear_all(self) -> None:
        """Clear all caches and loaded adapters."""
        self._loaded_adapters.clear()
        self._adapter_classes.clear()
