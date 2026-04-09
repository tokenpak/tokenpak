"""Main provider router orchestrator."""

from typing import Dict, Any, Optional, List
import asyncio
import logging
import json

from .detector import Provider, ProviderDetector
from .registry import AdapterRegistry
from .failover import FailoverHandler
from .costs import CostTracker

logger = logging.getLogger(__name__)


class RoutingConfig:
    """Configuration for routing."""

    def __init__(
        self,
        primary_provider: Optional[Provider] = None,
        fallback_providers: Optional[List[Provider]] = None,
        adapter_configs: Optional[Dict[str, str]] = None,
        cost_tracking: bool = True,
        auto_detect: bool = True,
        max_retries: int = 3,
        timeout: float = 30.0,
    ):
        """
        Initialize routing config.

        Args:
            primary_provider: Primary provider to use
            fallback_providers: Fallback providers in order
            adapter_configs: Custom adapter paths per provider
            cost_tracking: Enable cost tracking
            auto_detect: Auto-detect provider from request
            max_retries: Max retries per adapter
            timeout: Request timeout seconds
        """
        self.primary_provider = primary_provider
        self.fallback_providers = fallback_providers or []
        self.adapter_configs = adapter_configs or {}
        self.cost_tracking = cost_tracking
        self.auto_detect = auto_detect
        self.max_retries = max_retries
        self.timeout = timeout

    def to_dict(self) -> dict:
        """Convert config to dict."""
        return {
            "primary_provider": self.primary_provider.value if self.primary_provider else None,
            "fallback_providers": [p.value for p in self.fallback_providers],
            "adapter_configs": self.adapter_configs,
            "cost_tracking": self.cost_tracking,
            "auto_detect": self.auto_detect,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }


class ProviderRouter:
    """Main router orchestrating provider selection and failover."""

    def __init__(self, config: Optional[RoutingConfig] = None):
        """
        Initialize router.

        Args:
            config: RoutingConfig instance
        """
        self.config = config or RoutingConfig()
        self.detector = ProviderDetector()
        self.registry = AdapterRegistry(
            custom_adapters={
                Provider(k): v
                for k, v in self.config.adapter_configs.items()
            }
            if self.config.adapter_configs
            else None
        )
        self.cost_tracker = CostTracker() if self.config.cost_tracking else None
        self._adapters: Dict[Provider, Any] = {}

    def initialize_adapters(self, adapter_instances: Dict[Provider, Any]) -> None:
        """
        Initialize with adapter instances.

        Args:
            adapter_instances: Dict mapping Provider -> adapter instance
        """
        self._adapters = adapter_instances.copy()
        logger.info(f"Initialized {len(self._adapters)} adapters")

    def _get_provider_list(self, provider: Optional[Provider] = None) -> List[Provider]:
        """
        Get ordered list of providers to try.

        Args:
            provider: Specific provider to try first

        Returns:
            List of providers in priority order
        """
        providers = []

        if provider:
            providers.append(provider)

        if self.config.primary_provider and self.config.primary_provider not in providers:
            providers.append(self.config.primary_provider)

        providers.extend(self.config.fallback_providers)

        # Deduplicate while preserving order
        seen = set()
        result = []
        for p in providers:
            if p not in seen:
                result.append(p)
                seen.add(p)

        return result

    def _detect_provider(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> Optional[Provider]:
        """Detect provider from request context."""
        if not self.config.auto_detect:
            return None

        try:
            provider, reason = self.detector.detect(
                api_key=api_key, model=model, headers=headers
            )
            if provider:
                logger.info(f"Auto-detected provider: {provider} ({reason})")
            return provider
        except Exception as e:
            logger.warning(f"Provider detection failed: {e}")
            return None

    async def route_request(
        self,
        request_func,
        provider: Optional[Provider] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        headers: Optional[dict] = None,
        *args,
        **kwargs,
    ) -> Any:
        """
        Route request to appropriate provider with failover.

        Args:
            request_func: Async function to execute (adapter, *args, **kwargs)
            provider: Specific provider to use
            api_key: API key (for detection)
            model: Model name (for detection)
            headers: Request headers (for detection)
            *args: Additional args for request_func
            **kwargs: Additional kwargs for request_func

        Returns:
            Request result

        Raises:
            RuntimeError: If all providers fail
        """
        # Detect provider if not specified
        if not provider and self.config.auto_detect:
            provider = self._detect_provider(api_key=api_key, model=model, headers=headers)

        # Get provider list
        providers = self._get_provider_list(provider)

        if not providers:
            raise ValueError("No providers configured")

        # Create failover handler
        failover = FailoverHandler(
            adapters=providers,
            max_retries=self.config.max_retries,
            timeout=self.config.timeout,
        )

        # Execute with failover
        try:
            result = await failover.execute_with_failover(
                request_func, self._adapters, *args, **kwargs
            )

            if self.cost_tracker:
                # Extract cost info if available in result
                cost_data = kwargs.get("_cost_data", {})
                self.cost_tracker.track_request(
                    provider=providers[0].value,
                    model=model,
                    status="success",
                    **cost_data,
                )

            return result

        except Exception as e:
            if self.cost_tracker:
                self.cost_tracker.track_request(
                    provider=providers[0].value if providers else "unknown",
                    model=model,
                    status="error",
                    metadata={"error": str(e)},
                )
            raise

    def route_request_sync(
        self,
        request_func,
        provider: Optional[Provider] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        headers: Optional[dict] = None,
        *args,
        **kwargs,
    ) -> Any:
        """
        Route request (synchronous version).

        Args:
            request_func: Sync function to execute
            provider: Specific provider to use
            api_key: API key (for detection)
            model: Model name (for detection)
            headers: Request headers (for detection)
            *args: Additional args
            **kwargs: Additional kwargs

        Returns:
            Request result
        """
        # Detect provider if not specified
        if not provider and self.config.auto_detect:
            provider = self._detect_provider(api_key=api_key, model=model, headers=headers)

        # Get provider list
        providers = self._get_provider_list(provider)

        if not providers:
            raise ValueError("No providers configured")

        # Create failover handler
        failover = FailoverHandler(
            adapters=providers,
            max_retries=self.config.max_retries,
            timeout=self.config.timeout,
        )

        # Execute with failover
        try:
            result = failover.execute_with_failover_sync(
                request_func, self._adapters, *args, **kwargs
            )

            if self.cost_tracker:
                cost_data = kwargs.get("_cost_data", {})
                self.cost_tracker.track_request(
                    provider=providers[0].value,
                    model=model,
                    status="success",
                    **cost_data,
                )

            return result

        except Exception as e:
            if self.cost_tracker:
                self.cost_tracker.track_request(
                    provider=providers[0].value if providers else "unknown",
                    model=model,
                    status="error",
                    metadata={"error": str(e)},
                )
            raise

    def get_cost_summary(self) -> Optional[dict]:
        """Get cost tracking summary."""
        if not self.cost_tracker:
            return None
        return {
            "total_cost": self.cost_tracker.get_total_cost(),
            "by_provider": {
                k: v.to_dict() for k, v in self.cost_tracker.get_all_summaries().items()
            },
        }

    def get_config(self) -> dict:
        """Get current routing configuration."""
        return self.config.to_dict()
