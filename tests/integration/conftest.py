"""Shared fixtures and configuration for integration tests."""

import os
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="session")
def test_port() -> int:
    """Fixed port for test proxy instance."""
    return 8767


@pytest.fixture
def mock_api_key() -> str:
    """Mock API key for testing."""
    return "sk-test-123456789"


@pytest.fixture
def mock_anthropic_response() -> Dict[str, Any]:
    """Standard mocked Anthropic API response."""
    return {
        "id": "msg_test_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "This is a test response from Claude."}],
        "model": "claude-3-sonnet-20240229",
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 42, "output_tokens": 18},
    }


@pytest.fixture
def mock_openai_response() -> Dict[str, Any]:
    """Standard mocked OpenAI API response."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1704067200,
        "model": "gpt-4",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "This is a test response from GPT-4."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 35, "completion_tokens": 15, "total_tokens": 50},
    }


@pytest.fixture
def mock_http_client():
    """Mock HTTP client for testing without real network calls."""
    client = MagicMock()
    client.request = MagicMock()
    client.post = MagicMock()
    client.get = MagicMock()
    client.close = MagicMock()
    return client


@pytest.fixture
def cache_storage(tmp_path):
    """Temporary cache storage for tests."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return str(cache_dir)


@pytest.fixture
def metrics_storage(tmp_path):
    """Temporary metrics storage for tests."""
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    return str(metrics_dir)


@pytest.fixture
def tokenpak_config(cache_storage, metrics_storage):
    """Base TokenPak configuration for tests."""
    return {
        "proxy": {
            "host": "127.0.0.1",
            "port": 8767,
            "timeout": 5,
        },
        "cache": {
            "enabled": True,
            "path": cache_storage,
            "ttl": 3600,
        },
        "metrics": {
            "enabled": True,
            "path": metrics_storage,
        },
        "budget": {
            "mode": "none",  # Disable budget limits for testing
        },
    }


@pytest.fixture
def adapter_env(mock_api_key):
    """Environment setup for adapter testing."""
    env = os.environ.copy()
    env.update(
        {
            "ANTHROPIC_API_KEY": mock_api_key,
            "OPENAI_API_KEY": mock_api_key,
            "TOKENPAK_BASE_URL": "http://127.0.0.1:8767",
            "TOKENPAK_SKIP_GATE": "1",  # Skip validation gate for faster tests
        }
    )
    return env


@pytest.fixture(autouse=True)
def reset_mock_state():
    """Reset mock state between tests."""
    yield
    # Cleanup if needed
    pass
