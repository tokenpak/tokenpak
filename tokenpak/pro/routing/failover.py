"""Failover handler for multi-adapter request retrying."""

from typing import Callable, Any, Optional, List
import asyncio
import logging
from .detector import Provider

logger = logging.getLogger(__name__)


class FailoverHandler:
    """Manages failover between adapters on request failure."""

    def __init__(
        self,
        adapters: List[Provider],
        max_retries: int = 3,
        timeout: float = 30.0,
        backoff_factor: float = 1.5,
    ):
        """
        Initialize failover handler.

        Args:
            adapters: List of providers to try in order
            max_retries: Maximum number of retry attempts (per adapter)
            timeout: Request timeout in seconds
            backoff_factor: Exponential backoff multiplier (1.0 = no backoff)
        """
        self.adapters = adapters
        self.max_retries = max_retries
        self.timeout = timeout
        self.backoff_factor = backoff_factor
        self._retry_counts: dict = {}

    def reset_retries(self) -> None:
        """Reset retry counters."""
        self._retry_counts.clear()

    def get_retry_count(self, provider: Provider) -> int:
        """Get current retry count for a provider."""
        return self._retry_counts.get(provider, 0)

    def increment_retry(self, provider: Provider) -> None:
        """Increment retry count for provider."""
        self._retry_counts[provider] = self._retry_counts.get(provider, 0) + 1

    def should_retry(self, provider: Provider) -> bool:
        """Check if provider should be retried."""
        return self.get_retry_count(provider) < self.max_retries

    def get_backoff_delay(self, provider: Provider) -> float:
        """Calculate backoff delay for provider."""
        retry_count = self.get_retry_count(provider)
        return (self.backoff_factor**retry_count) if self.backoff_factor > 1.0 else 0.0

    async def execute_with_failover(
        self,
        request_func: Callable,
        adapter_map: dict,
        *args,
        **kwargs,
    ) -> Any:
        """
        Execute request with failover across adapters.

        Args:
            request_func: Async function to execute (takes adapter as first arg)
            adapter_map: Dict mapping Provider -> adapter instance
            *args: Additional positional args for request_func
            **kwargs: Additional keyword args for request_func

        Returns:
            Result from successful execution

        Raises:
            RuntimeError: If all adapters fail
        """
        last_error = None

        for provider in self.adapters:
            if provider not in adapter_map:
                logger.warning(f"Adapter {provider} not available, skipping")
                continue

            adapter = adapter_map[provider]
            retry_count = 0

            while retry_count < self.max_retries:
                try:
                    # Execute with timeout
                    result = await asyncio.wait_for(
                        request_func(adapter, *args, **kwargs),
                        timeout=self.timeout,
                    )
                    logger.info(f"Request succeeded with {provider}")
                    return result

                except asyncio.TimeoutError as e:
                    last_error = e
                    logger.warning(
                        f"{provider} timeout (attempt {retry_count + 1}/{self.max_retries})"
                    )
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"{provider} failed: {e} (attempt {retry_count + 1}/{self.max_retries})"
                    )

                retry_count += 1
                if retry_count < self.max_retries:
                    delay = self.get_backoff_delay(provider)
                    if delay > 0:
                        await asyncio.sleep(delay)

            logger.error(f"Provider {provider} exhausted retries")

        raise RuntimeError(f"All adapters failed. Last error: {last_error}")

    def execute_with_failover_sync(
        self,
        request_func: Callable,
        adapter_map: dict,
        *args,
        **kwargs,
    ) -> Any:
        """
        Execute request with failover (synchronous version).

        Args:
            request_func: Sync function to execute (takes adapter as first arg)
            adapter_map: Dict mapping Provider -> adapter instance
            *args: Additional positional args for request_func
            **kwargs: Additional keyword args for request_func

        Returns:
            Result from successful execution

        Raises:
            RuntimeError: If all adapters fail
        """
        last_error = None

        for provider in self.adapters:
            if provider not in adapter_map:
                logger.warning(f"Adapter {provider} not available, skipping")
                continue

            adapter = adapter_map[provider]
            retry_count = 0

            while retry_count < self.max_retries:
                try:
                    result = request_func(adapter, *args, **kwargs)
                    logger.info(f"Request succeeded with {provider}")
                    return result

                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"{provider} failed: {e} (attempt {retry_count + 1}/{self.max_retries})"
                    )

                retry_count += 1
                if retry_count < self.max_retries:
                    delay = self.get_backoff_delay(provider)
                    if delay > 0:
                        import time

                        time.sleep(delay)

            logger.error(f"Provider {provider} exhausted retries")

        raise RuntimeError(f"All adapters failed. Last error: {last_error}")
