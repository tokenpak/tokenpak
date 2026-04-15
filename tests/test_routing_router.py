"""Tests for provider router."""


import pytest
pytest.importorskip("tokenpak.pro.routing.detector", reason="module not available in current build")
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from tokenpak.pro.routing.router import ProviderRouter, RoutingConfig
from tokenpak.pro.routing.detector import Provider


class TestRoutingConfig:
    """Test routing configuration."""

    def test_config_creation_defaults(self):
        """Test default config creation."""
        config = RoutingConfig()
        assert config.primary_provider is None
        assert config.fallback_providers == []
        assert config.cost_tracking is True
        assert config.auto_detect is True
        assert config.max_retries == 3
        assert config.timeout == 30.0

    def test_config_creation_custom(self):
        """Test custom config creation."""
        fallback = [Provider.OPENAI, Provider.GOOGLE]
        config = RoutingConfig(
            primary_provider=Provider.ANTHROPIC,
            fallback_providers=fallback,
            cost_tracking=False,
            max_retries=5,
            timeout=60.0,
        )

        assert config.primary_provider == Provider.ANTHROPIC
        assert config.fallback_providers == fallback
        assert config.cost_tracking is False
        assert config.max_retries == 5
        assert config.timeout == 60.0

    def test_config_to_dict(self):
        """Test converting config to dict."""
        config = RoutingConfig(
            primary_provider=Provider.ANTHROPIC,
            fallback_providers=[Provider.OPENAI],
        )

        data = config.to_dict()
        assert data["primary_provider"] == "anthropic"
        assert data["fallback_providers"] == ["openai"]


class TestProviderRouter:
    """Test main provider router."""

    def setup_method(self):
        """Set up router."""
        config = RoutingConfig(
            primary_provider=Provider.ANTHROPIC,
            fallback_providers=[Provider.OPENAI, Provider.GOOGLE],
        )
        self.router = ProviderRouter(config)

    def test_router_creation(self):
        """Test router initialization."""
        assert self.router is not None
        assert self.router.config is not None
        assert self.router.detector is not None
        assert self.router.registry is not None

    def test_router_cost_tracking_enabled(self):
        """Test cost tracker is created when enabled."""
        config = RoutingConfig(cost_tracking=True)
        router = ProviderRouter(config)
        assert router.cost_tracker is not None

    def test_router_cost_tracking_disabled(self):
        """Test cost tracker is None when disabled."""
        config = RoutingConfig(cost_tracking=False)
        router = ProviderRouter(config)
        assert router.cost_tracker is None

    def test_initialize_adapters(self):
        """Test initializing adapters."""
        mock_adapter1 = Mock()
        mock_adapter2 = Mock()
        adapters = {
            Provider.ANTHROPIC: mock_adapter1,
            Provider.OPENAI: mock_adapter2,
        }

        self.router.initialize_adapters(adapters)

        assert self.router._adapters == adapters

    def test_get_provider_list_with_specific(self):
        """Test getting provider list with specific provider."""
        providers = self.router._get_provider_list(Provider.GOOGLE)

        # Should start with specified, then primary, then fallbacks
        assert providers[0] == Provider.GOOGLE

    def test_get_provider_list_no_specific(self):
        """Test getting provider list without specific provider."""
        providers = self.router._get_provider_list()

        # Should be primary first, then fallbacks
        assert providers[0] == Provider.ANTHROPIC
        assert Provider.OPENAI in providers
        assert Provider.GOOGLE in providers

    def test_get_provider_list_deduplication(self):
        """Test that provider list is deduplicated."""
        config = RoutingConfig(
            primary_provider=Provider.ANTHROPIC,
            fallback_providers=[Provider.ANTHROPIC, Provider.OPENAI],
        )
        router = ProviderRouter(config)

        providers = router._get_provider_list(Provider.ANTHROPIC)

        # Should not have duplicates
        assert providers.count(Provider.ANTHROPIC) == 1

    def test_detect_provider_success(self):
        """Test provider detection."""
        provider = self.router._detect_provider(
            api_key="sk-ant-test_key_123"
        )
        assert provider == Provider.ANTHROPIC

    def test_detect_provider_disabled(self):
        """Test detection disabled."""
        config = RoutingConfig(auto_detect=False)
        router = ProviderRouter(config)

        provider = router._detect_provider(
            api_key="sk-ant-test_key_123"
        )
        assert provider is None

    @pytest.mark.asyncio
    async def test_route_request_success(self):
        """Test routing request successfully."""
        mock_adapter = Mock()
        self.router._adapters = {Provider.ANTHROPIC: mock_adapter}

        async def mock_request(adapter):
            return {"result": "success"}

        result = await self.router.route_request(
            mock_request,
            provider=Provider.ANTHROPIC,
        )

        assert result["result"] == "success"

    @pytest.mark.asyncio
    async def test_route_request_with_detection(self):
        """Test routing request with auto-detection."""
        mock_adapter = Mock()
        self.router._adapters = {Provider.ANTHROPIC: mock_adapter}

        async def mock_request(adapter):
            return {"result": "success"}

        result = await self.router.route_request(
            mock_request,
            api_key="sk-ant-test_key_123",
        )

        assert result["result"] == "success"

    @pytest.mark.asyncio
    async def test_route_request_no_providers(self):
        """Test routing with no configured providers."""
        config = RoutingConfig(
            primary_provider=None,
            fallback_providers=[],
        )
        router = ProviderRouter(config)

        async def mock_request(adapter):
            return {"result": "success"}

        with pytest.raises(ValueError, match="No providers configured"):
            await router.route_request(mock_request)

    def test_route_request_sync_success(self):
        """Test synchronous routing."""
        mock_adapter = Mock()
        self.router._adapters = {Provider.ANTHROPIC: mock_adapter}

        def mock_request(adapter):
            return {"result": "success"}

        result = self.router.route_request_sync(
            mock_request,
            provider=Provider.ANTHROPIC,
        )

        assert result["result"] == "success"

    def test_get_cost_summary(self):
        """Test getting cost summary."""
        # Track some costs
        self.router.cost_tracker.track_request(
            provider="anthropic",
            input_cost=0.01,
            output_cost=0.005,
        )

        summary = self.router.get_cost_summary()

        assert summary is not None
        assert summary["total_cost"] == 0.015
        assert "anthropic" in summary["by_provider"]

    def test_get_cost_summary_no_tracker(self):
        """Test getting cost summary when tracking disabled."""
        config = RoutingConfig(cost_tracking=False)
        router = ProviderRouter(config)

        summary = router.get_cost_summary()
        assert summary is None

    def test_get_config(self):
        """Test getting router configuration."""
        config_dict = self.router.get_config()

        assert config_dict["primary_provider"] == "anthropic"
        assert "openai" in config_dict["fallback_providers"]
        assert config_dict["cost_tracking"] is True

    def test_router_with_custom_adapters(self):
        """Test router with custom adapter configs."""
        custom_configs = {
            "anthropic": "custom.anthropic.CustomAdapter",
            "openai": "custom.openai.CustomAdapter",
        }
        config = RoutingConfig(adapter_configs=custom_configs)
        router = ProviderRouter(config)

        assert "anthropic" in router.registry.adapter_configs
        assert "openai" in router.registry.adapter_configs

    @pytest.mark.asyncio
    async def test_route_request_with_cost_tracking(self):
        """Test that costs are tracked on success."""
        mock_adapter = Mock()
        self.router._adapters = {Provider.ANTHROPIC: mock_adapter}

        async def mock_request(adapter):
            return {"result": "success"}

        await self.router.route_request(
            mock_request,
            provider=Provider.ANTHROPIC,
            model="claude-3",
        )

        summary = self.router.get_cost_summary()
        assert summary is not None

    @pytest.mark.asyncio
    async def test_route_request_error_tracking(self):
        """Test that errors are tracked."""
        mock_adapter = Mock()
        self.router._adapters = {Provider.ANTHROPIC: mock_adapter}

        async def mock_request(adapter):
            raise RuntimeError("Request failed")

        with pytest.raises(RuntimeError):
            await self.router.route_request(
                mock_request,
                provider=Provider.ANTHROPIC,
                model="claude-3",
            )

        summary = self.router.get_cost_summary()
        # Should have tracked the error
        assert summary is not None

    def test_multiple_routers_isolated(self):
        """Test that multiple routers don't share state."""
        config1 = RoutingConfig(primary_provider=Provider.ANTHROPIC)
        config2 = RoutingConfig(primary_provider=Provider.OPENAI)

        router1 = ProviderRouter(config1)
        router2 = ProviderRouter(config2)

        router1.cost_tracker.track_request(
            provider="anthropic", input_cost=0.01
        )

        summary1 = router1.get_cost_summary()
        summary2 = router2.get_cost_summary()

        assert summary1["total_cost"] == 0.01
        assert summary2["total_cost"] == 0.0

    @pytest.mark.asyncio
    async def test_route_request_with_metadata(self):
        """Test routing with metadata in kwargs."""
        mock_adapter = Mock()
        self.router._adapters = {Provider.ANTHROPIC: mock_adapter}

        async def mock_request(adapter):
            return {"result": "success", "request_id": "req-123"}

        result = await self.router.route_request(
            mock_request,
            provider=Provider.ANTHROPIC,
        )

        assert result["result"] == "success"
