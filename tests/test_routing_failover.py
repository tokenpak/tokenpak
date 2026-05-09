"""Tests for failover handler."""


import pytest

pytest.importorskip("tokenpak.pro.routing.failover", reason="module not available in current build")
import asyncio
from unittest.mock import Mock

import pytest
from tokenpak.pro.routing.detector import Provider
from tokenpak.pro.routing.failover import FailoverHandler


class TestFailoverHandler:
    """Test failover handling and retry logic."""

    def setup_method(self):
        """Set up test failover handler."""
        self.providers = [Provider.ANTHROPIC, Provider.OPENAI, Provider.GOOGLE]
        self.handler = FailoverHandler(
            adapters=self.providers,
            max_retries=3,
            timeout=10.0,
            backoff_factor=1.5,
        )

    def test_handler_creation(self):
        """Test handler initialization."""
        assert self.handler is not None
        assert self.handler.adapters == self.providers
        assert self.handler.max_retries == 3
        assert self.handler.timeout == 10.0
        assert self.handler.backoff_factor == 1.5

    def test_reset_retries(self):
        """Test resetting retry counts."""
        self.handler._retry_counts[Provider.ANTHROPIC] = 2
        self.handler._retry_counts[Provider.OPENAI] = 1

        self.handler.reset_retries()

        assert len(self.handler._retry_counts) == 0

    def test_get_retry_count_initial(self):
        """Test initial retry count."""
        count = self.handler.get_retry_count(Provider.ANTHROPIC)
        assert count == 0

    def test_increment_retry(self):
        """Test incrementing retry count."""
        self.handler.increment_retry(Provider.ANTHROPIC)
        assert self.handler.get_retry_count(Provider.ANTHROPIC) == 1

        self.handler.increment_retry(Provider.ANTHROPIC)
        assert self.handler.get_retry_count(Provider.ANTHROPIC) == 2

    def test_should_retry_true(self):
        """Test should_retry returns true when under limit."""
        self.handler.increment_retry(Provider.ANTHROPIC)
        assert self.handler.should_retry(Provider.ANTHROPIC) is True

    def test_should_retry_false(self):
        """Test should_retry returns false at limit."""
        for _ in range(3):
            self.handler.increment_retry(Provider.ANTHROPIC)
        assert self.handler.should_retry(Provider.ANTHROPIC) is False

    def test_get_backoff_delay_none(self):
        """Test backoff with factor 1.0."""
        handler = FailoverHandler(
            self.providers, max_retries=3, backoff_factor=1.0
        )
        delay = handler.get_backoff_delay(Provider.ANTHROPIC)
        assert delay == 0.0

    def test_get_backoff_delay_exponential(self):
        """Test exponential backoff calculation."""
        handler = FailoverHandler(
            self.providers, max_retries=3, backoff_factor=2.0
        )

        handler.increment_retry(Provider.ANTHROPIC)
        delay1 = handler.get_backoff_delay(Provider.ANTHROPIC)
        assert delay1 == 2.0  # 2^1

        handler.increment_retry(Provider.ANTHROPIC)
        delay2 = handler.get_backoff_delay(Provider.ANTHROPIC)
        assert delay2 == 4.0  # 2^2

        handler.increment_retry(Provider.ANTHROPIC)
        delay3 = handler.get_backoff_delay(Provider.ANTHROPIC)
        assert delay3 == 8.0  # 2^3

    @pytest.mark.asyncio
    async def test_failover_success_first_provider(self):
        """Test successful request on first provider."""
        mock_adapter = Mock()
        adapters = {Provider.ANTHROPIC: mock_adapter}

        async def mock_request(adapter):
            return {"status": "success", "data": "test"}

        result = await self.handler.execute_with_failover(
            mock_request, adapters
        )

        assert result["status"] == "success"
        assert result["data"] == "test"

    @pytest.mark.asyncio
    async def test_failover_success_second_provider(self):
        """Test successful request on second provider after first fails."""
        mock_adapter1 = Mock()
        mock_adapter2 = Mock()
        adapters = {
            Provider.ANTHROPIC: mock_adapter1,
            Provider.OPENAI: mock_adapter2,
        }

        call_count = 0

        async def mock_request(adapter):
            nonlocal call_count
            call_count += 1
            if adapter is mock_adapter1:
                raise RuntimeError("First provider failed")
            return {"status": "success", "data": "fallback"}

        result = await self.handler.execute_with_failover(
            mock_request, adapters
        )

        assert result["status"] == "success"
        assert call_count >= 2  # At least 2 calls

    @pytest.mark.asyncio
    async def test_failover_all_fail(self):
        """Test when all providers fail."""
        mock_adapter1 = Mock()
        mock_adapter2 = Mock()
        adapters = {
            Provider.ANTHROPIC: mock_adapter1,
            Provider.OPENAI: mock_adapter2,
        }

        async def mock_request(adapter):
            raise RuntimeError("Always fails")

        with pytest.raises(RuntimeError, match="All adapters failed"):
            await self.handler.execute_with_failover(mock_request, adapters)

    @pytest.mark.asyncio
    async def test_failover_timeout(self):
        """Test timeout handling in failover."""
        handler = FailoverHandler(
            [Provider.ANTHROPIC], max_retries=1, timeout=0.1
        )

        mock_adapter = Mock()
        adapters = {Provider.ANTHROPIC: mock_adapter}

        async def slow_request(adapter):
            await asyncio.sleep(1.0)  # Longer than timeout
            return {"status": "success"}

        with pytest.raises(RuntimeError):
            await handler.execute_with_failover(slow_request, adapters)

    def test_sync_failover_success(self):
        """Test synchronous failover success."""
        mock_adapter = Mock()
        adapters = {Provider.ANTHROPIC: mock_adapter}

        def mock_request(adapter):
            return {"status": "success", "data": "test"}

        result = self.handler.execute_with_failover_sync(
            mock_request, adapters
        )

        assert result["status"] == "success"

    def test_sync_failover_with_retry(self):
        """Test synchronous failover with retry."""
        mock_adapter1 = Mock()
        mock_adapter2 = Mock()
        adapters = {
            Provider.ANTHROPIC: mock_adapter1,
            Provider.OPENAI: mock_adapter2,
        }

        call_count = 0

        def mock_request(adapter):
            nonlocal call_count
            call_count += 1
            if adapter is mock_adapter1:
                raise RuntimeError("First provider failed")
            return {"status": "success"}

        result = self.handler.execute_with_failover_sync(
            mock_request, adapters
        )

        assert result["status"] == "success"
        assert call_count >= 2

    def test_sync_failover_all_fail(self):
        """Test synchronous failover when all fail."""
        mock_adapter = Mock()
        adapters = {Provider.ANTHROPIC: mock_adapter}

        def mock_request(adapter):
            raise RuntimeError("Always fails")

        with pytest.raises(RuntimeError, match="All adapters failed"):
            self.handler.execute_with_failover_sync(
                mock_request, adapters
            )

    @pytest.mark.asyncio
    async def test_failover_skip_missing_adapter(self):
        """Test that missing adapters are skipped."""
        mock_adapter = Mock()
        adapters = {Provider.OPENAI: mock_adapter}  # Only OPENAI, not ANTHROPIC

        async def mock_request(adapter):
            assert adapter is mock_adapter  # Should skip ANTHROPIC
            return {"status": "success"}

        result = await self.handler.execute_with_failover(
            mock_request, adapters
        )

        assert result["status"] == "success"

    def test_max_retries_respected(self):
        """Test that max retries limit is respected."""
        handler = FailoverHandler(
            [Provider.ANTHROPIC], max_retries=2
        )

        mock_adapter = Mock()
        adapters = {Provider.ANTHROPIC: mock_adapter}

        call_count = 0

        def mock_request(adapter):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Fail")

        with pytest.raises(RuntimeError):
            handler.execute_with_failover_sync(mock_request, adapters)

        # Should retry up to max_retries times
        assert call_count == 2

    def test_handlers_isolated(self):
        """Test that multiple handlers don't share state."""
        handler1 = FailoverHandler([Provider.ANTHROPIC])
        handler2 = FailoverHandler([Provider.OPENAI])

        handler1.increment_retry(Provider.ANTHROPIC)
        assert handler1.get_retry_count(Provider.ANTHROPIC) == 1
        assert handler2.get_retry_count(Provider.ANTHROPIC) == 0
