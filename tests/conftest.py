"""tests/conftest.py — Shared fixtures for tokenpak tests

Provides:
- Sample config (valid and invalid)
- Mock provider setup
- Test API keys
- Common test data generators
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# Test Data Generators
# ============================================================================

def make_entry(
    model: str = "claude-sonnet-4-6",
    tokens: int = 1000,
    cost: float = 0.05,
    timestamp: str | None = None,
    agent: str = "test-agent",
    provider: str = "anthropic",
    cache_tokens: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    """Create a test entry with sensible defaults."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    
    entry = {
        "model": model,
        "tokens": tokens,
        "cost": cost,
        "timestamp": timestamp,
        "agent": agent,
        "provider": provider,
    }
    
    if cache_tokens > 0 or extra:
        entry["extra"] = {"cache_tokens": cache_tokens, **extra}
    
    return entry


def make_entries(
    count: int = 5,
    model: str = "claude-sonnet-4-6",
    tokens: int = 1000,
    cost_base: float = 0.05,
) -> list[dict[str, Any]]:
    """Create multiple test entries with incrementing costs."""
    entries = []
    for i in range(count):
        entries.append(
            make_entry(
                model=model,
                tokens=tokens + (i * 100),
                cost=cost_base + (i * 0.01),
                timestamp=f"2026-01-01T{i:02d}:00:00Z",
                agent=f"agent-{i}",
            )
        )
    return entries


# ============================================================================
# Config Fixtures
# ============================================================================

@pytest.fixture
def temp_config_dir() -> Generator[Path, None, None]:
    """Create and clean up a temporary config directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def valid_config_file(temp_config_dir: Path) -> Path:
    """Create a valid config file."""
    config = {
        "version": "1.0",
        "providers": {
            "anthropic": {
                "api_key": "sk-test-anthropic-key",
                "base_url": "https://api.anthropic.com",
            },
            "openai": {
                "api_key": "sk-test-openai-key",
                "base_url": "https://api.openai.com",
            },
        },
        "rate_limits": {
            "anthropic": {"requests_per_minute": 60},
            "openai": {"requests_per_minute": 100},
        },
    }
    
    config_file = temp_config_dir / "config.json"
    with open(config_file, "w") as f:
        json.dump(config, f)
    
    return config_file


@pytest.fixture
def invalid_config_file(temp_config_dir: Path) -> Path:
    """Create an invalid config file (bad JSON)."""
    config_file = temp_config_dir / "invalid.json"
    with open(config_file, "w") as f:
        f.write("{invalid json")
    
    return config_file


@pytest.fixture
def missing_config_file(temp_config_dir: Path) -> Path:
    """Return path to a non-existent config file."""
    return temp_config_dir / "does-not-exist.json"


# ============================================================================
# Entry Store Fixtures
# ============================================================================

@pytest.fixture
def test_entries_dir() -> Generator[Path, None, None]:
    """Create a temporary entries directory with sample JSONL data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        entries_dir = Path(tmpdir) / "entries"
        entries_dir.mkdir(parents=True)
        
        # Write sample entries for 2026-03-01
        entries_file = entries_dir / "2026-03-01.jsonl"
        entries = make_entries(5)
        with open(entries_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        
        yield entries_dir


@pytest.fixture
def mock_entry_store(test_entries_dir: Path) -> Any:
    """Return an EntryStore pointing to test data."""
    from tokenpak.agent.query.api import EntryStore
    return EntryStore(entries_dir=test_entries_dir)


# ============================================================================
# Provider Fixtures
# ============================================================================

@pytest.fixture
def mock_anthropic_provider() -> MagicMock:
    """Create a mock Anthropic provider."""
    provider = MagicMock()
    provider.name = "anthropic"
    provider.available = True
    provider.count_tokens.return_value = 1000
    provider.get_model_cost.return_value = 0.05
    return provider


@pytest.fixture
def mock_openai_provider() -> MagicMock:
    """Create a mock OpenAI provider."""
    provider = MagicMock()
    provider.name = "openai"
    provider.available = True
    provider.count_tokens.return_value = 1200
    provider.get_model_cost.return_value = 0.06
    return provider


# ============================================================================
# Sample Data Fixtures
# ============================================================================

@pytest.fixture
def sample_entry() -> dict[str, Any]:
    """Single test entry."""
    return make_entry(
        model="claude-sonnet-4-6",
        tokens=1000,
        cost=0.05,
        agent="test-agent",
    )


@pytest.fixture
def sample_entries() -> list[dict[str, Any]]:
    """Multiple test entries."""
    return make_entries(5)


@pytest.fixture
def sample_entries_multi_model() -> list[dict[str, Any]]:
    """Entries from multiple models for testing routing/aggregation."""
    return [
        make_entry(model="claude-sonnet-4-6", tokens=1000, cost=0.05),
        make_entry(model="claude-opus-4-5", tokens=1000, cost=0.15),
        make_entry(model="gpt-4-turbo", tokens=1000, cost=0.08),
        make_entry(model="gpt-3.5-turbo", tokens=1000, cost=0.01),
        make_entry(model="claude-sonnet-4-6", tokens=1000, cost=0.05),
    ]


@pytest.fixture
def sample_entries_with_cache() -> list[dict[str, Any]]:
    """Entries with cache token data."""
    return [
        make_entry(tokens=1000, cost=0.05, cache_tokens=100),
        make_entry(tokens=1000, cost=0.04, cache_tokens=200),
        make_entry(tokens=1000, cost=0.05, cache_tokens=50),
    ]


@pytest.fixture
def sample_entries_with_compression() -> list[dict[str, Any]]:
    """Entries with compression data."""
    return [
        make_entry(
            tokens=1000,
            cost=0.05,
            compressed_tokens=200,
            compression_ratio=1.25,
        ),
        make_entry(
            tokens=1000,
            cost=0.05,
            compressed_tokens=150,
            compression_ratio=1.15,
        ),
    ]


# ============================================================================
# Rate Limiting Fixtures
# ============================================================================

@pytest.fixture
def rate_limit_config() -> dict[str, Any]:
    """Configuration for rate limit testing."""
    return {
        "providers": {
            "anthropic": {
                "requests_per_minute": 10,
                "tokens_per_minute": 50000,
            },
            "openai": {
                "requests_per_minute": 20,
                "tokens_per_minute": 100000,
            },
        }
    }


# ============================================================================
# API Fixtures
# ============================================================================

@pytest.fixture
def test_api_client() -> Any:
    """Create a test FastAPI client for query API."""
    from fastapi.testclient import TestClient
    from tokenpak.agent.query.api import create_query_app
    
    app = create_query_app()
    return TestClient(app)


@pytest.fixture
def test_ingest_client() -> Any:
    """Create a test FastAPI client for ingest API."""
    from fastapi.testclient import TestClient
    from tokenpak.agent.ingest.api import create_ingest_app
    
    app = create_ingest_app()
    return TestClient(app)
