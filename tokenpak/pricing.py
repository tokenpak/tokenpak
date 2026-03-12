"""Pricing tables for common LLM models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelPrice:
    """Price info for a single model."""

    model: str
    provider: str
    input_cost_per_mtok: float  # Cost per million input tokens
    output_cost_per_mtok: float  # Cost per million output tokens
    cache_read_cost_per_mtok: float  # Cost per million cache-read tokens


# Anthropic models
ANTHROPIC_PRICING = {
    "claude-opus-4-6": ModelPrice(
        model="claude-opus-4-6",
        provider="anthropic",
        input_cost_per_mtok=15.0,
        output_cost_per_mtok=75.0,
        cache_read_cost_per_mtok=1.50,
    ),
    "claude-sonnet-4-6": ModelPrice(
        model="claude-sonnet-4-6",
        provider="anthropic",
        input_cost_per_mtok=3.0,
        output_cost_per_mtok=15.0,
        cache_read_cost_per_mtok=0.30,
    ),
    "claude-haiku-4-5": ModelPrice(
        model="claude-haiku-4-5",
        provider="anthropic",
        input_cost_per_mtok=0.80,
        output_cost_per_mtok=4.0,
        cache_read_cost_per_mtok=0.08,
    ),
}

# OpenAI models
OPENAI_PRICING = {
    "gpt-4-turbo": ModelPrice(
        model="gpt-4-turbo",
        provider="openai",
        input_cost_per_mtok=10.0,
        output_cost_per_mtok=30.0,
        cache_read_cost_per_mtok=2.50,
    ),
    "gpt-4": ModelPrice(
        model="gpt-4",
        provider="openai",
        input_cost_per_mtok=30.0,
        output_cost_per_mtok=60.0,
        cache_read_cost_per_mtok=7.50,
    ),
}

# Combined pricing table
PRICING_TABLE = {**ANTHROPIC_PRICING, **OPENAI_PRICING}


def get_price(model: str) -> Optional[ModelPrice]:
    """Get pricing for a model, or None if unknown."""
    return PRICING_TABLE.get(model.lower())


def calculate_request_cost(
    model: str,
    input_tokens: int,
    cache_read_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    """
    Calculate actual cost of a request with cache reads.
    
    Args:
        model: Model name
        input_tokens: Number of input tokens (billed)
        cache_read_tokens: Number of cache-read tokens (at discount)
        output_tokens: Number of output tokens (billed)
    
    Returns:
        Total cost in dollars
    """
    price = get_price(model)
    if not price:
        return 0.0

    cost = 0.0
    cost += (input_tokens / 1_000_000) * price.input_cost_per_mtok
    cost += (cache_read_tokens / 1_000_000) * price.cache_read_cost_per_mtok
    cost += (output_tokens / 1_000_000) * price.output_cost_per_mtok
    return cost


def calculate_request_cost_baseline(
    model: str,
    total_input_tokens: int,
    output_tokens: int = 0,
) -> float:
    """
    Calculate what the request would cost WITHOUT cache (baseline).
    
    Args:
        model: Model name
        total_input_tokens: Total number of input tokens (no discount)
        output_tokens: Number of output tokens
    
    Returns:
        Total cost in dollars
    """
    price = get_price(model)
    if not price:
        return 0.0

    cost = 0.0
    cost += (total_input_tokens / 1_000_000) * price.input_cost_per_mtok
    cost += (output_tokens / 1_000_000) * price.output_cost_per_mtok
    return cost
