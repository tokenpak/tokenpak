"""Main provider router orchestrator."""

import logging
from typing import Any, Dict, List, Optional

from .costs import CostTracker
from .detector import Provider, ProviderDetector
from .failover import FailoverHandler
from .registry import AdapterRegistry

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

    def __init__(
        self,
        config: Optional[RoutingConfig] = None,
        *,
        registry: Optional[AdapterRegistry] = None,
        detector: Optional[ProviderDetector] = None,
        cost_tracker: Optional[CostTracker] = None,
        failover_base_delay: float = 1.0,
        failover_attempts: int = 3,
        **kwargs,
    ):
        """
        Initialize router.

        Args:
            config: RoutingConfig instance
            registry: Custom AdapterRegistry (optional)
            detector: Custom ProviderDetector (optional)
            cost_tracker: Custom CostTracker (optional)
            failover_base_delay: Base delay for failover retries
            failover_attempts: Max failover attempts
            **kwargs: Additional config kwargs (for future compatibility)
        """
        self.config = config or RoutingConfig()
        self.detector = detector or ProviderDetector()
        self.registry = registry or AdapterRegistry(
            custom_adapters={Provider(k): v for k, v in self.config.adapter_configs.items()}
            if self.config.adapter_configs
            else None
        )
        self.cost_tracker = cost_tracker
        if cost_tracker is None and self.config.cost_tracking:
            self.cost_tracker = CostTracker()

        self._failover_base_delay = failover_base_delay
        self._failover_attempts = failover_attempts
        self._adapters: Dict[Provider, Any] = {}

    @property
    def costs(self) -> "Optional[CostTracker]":
        """Alias for cost_tracker for compatibility."""
        return self.cost_tracker

    @property
    def failover(self) -> "Optional[FailoverHandler]":
        """Failover handler (None when not configured via FailoverHandler directly)."""
        return None

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
            provider, reason = self.detector.detect(api_key=api_key, model=model, headers=headers)
            if provider:
                logger.info(f"Auto-detected provider: {provider} ({reason})")
            return provider
        except Exception as e:
            logger.warning(f"Provider detection failed: {e}")
            return None

    def detect_provider(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Public API: detect provider from request context.

        Returns provider as string (e.g., "anthropic", "openai") or None.
        """
        provider = self._detect_provider(api_key=api_key, model=model, headers=headers)
        return provider.value if provider else None

    def route(
        self,
        request_func,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        headers: Optional[dict] = None,
        fallback_providers: Optional[List[str]] = None,
        *args,
        **kwargs,
    ) -> Any:
        """
        Public API: route request synchronously with failover.

        Args:
            request_func: Sync function to call with (provider, *args, **kwargs)
            provider: Specific provider to use
            api_key: API key for detection
            model: Model name for detection
            headers: Request headers for detection
            fallback_providers: List of provider names to try in order
            *args: Additional args for request_func
            **kwargs: Additional kwargs for request_func

        Returns:
            Result from request_func

        Raises:
            RuntimeError: If provider detection fails or all providers fail
        """
        # Detect provider if not specified
        detected_provider = None
        if not provider and self.config.auto_detect:
            detected_provider = self._detect_provider(api_key=api_key, model=model, headers=headers)
            if detected_provider:
                provider = detected_provider.value

        # Build provider list
        providers_to_try: List[str] = []
        if provider:
            providers_to_try.append(provider)
        if fallback_providers:
            for p in fallback_providers:
                if p not in providers_to_try:
                    providers_to_try.append(p)

        # If still no providers, try to use registered providers
        if not providers_to_try:
            registered = self.registry.get_all_providers()
            if registered:
                # Convert Provider enums to strings and sort
                providers_to_try = sorted(
                    [p.value if isinstance(p, Provider) else str(p) for p in registered]
                )
            else:
                raise RuntimeError("Provider detection failed: no providers specified or detected")

        # Try each provider with failover
        last_error = None
        for attempt, prov in enumerate(providers_to_try):
            try:
                result = request_func(prov, *args, **kwargs)
                if self.cost_tracker:
                    self.cost_tracker.track_request(
                        provider=prov,
                        model=model,
                        status="success",
                    )
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"Provider {prov} failed: {e}")
                if self.cost_tracker:
                    self.cost_tracker.track_request(
                        provider=prov,
                        model=model,
                        status="error",
                        metadata={"error": str(e)},
                    )
                if attempt < len(providers_to_try) - 1:
                    # More providers to try, continue
                    continue

        # All providers failed
        if last_error:
            raise last_error
        raise RuntimeError(f"All providers failed: {providers_to_try}")

    def track(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """
        Public API: track costs for a request.

        Args:
            provider: Provider name
            model: Model name
            input_tokens: Input token count
            output_tokens: Output token count
            cost: Total cost in USD
        """
        if not self.cost_tracker:
            logger.warning("Cost tracking disabled")
            return

        self.cost_tracker.track_request(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_cost=cost,  # map public `cost` → CostTracker.request_cost
        )

    def cost_summary(self) -> Optional[dict]:
        """
        Public API: get cost tracking summary.

        Returns dict with per-provider summaries or None if tracking disabled.
        """
        if not self.cost_tracker:
            return None

        summaries = self.cost_tracker.get_all_summaries()
        result = {}
        for provider, summary in summaries.items():
            prov_str = provider.value if isinstance(provider, Provider) else provider
            if hasattr(summary, "to_dict"):
                summary_dict = summary.to_dict()
                # Rename 'total_cost' to 'total_cost_usd' for consistency
                if "total_cost" in summary_dict:
                    summary_dict["total_cost_usd"] = summary_dict.pop("total_cost")
                result[prov_str] = summary_dict
            else:
                result[prov_str] = summary
        return result

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
