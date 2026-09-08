# SPDX-License-Identifier: Apache-2.0
"""Dynamic model registry — single source of truth for model metadata.

All model knowledge (pricing, tiers, provider routing, translations) lives
here.  Every consumer imports from this module instead of maintaining inline
dicts.  Unknown models are handled gracefully via family-based inference.

Usage::

    from tokenpak.models import get_rates, get_tier, translate_model

    get_rates("claude-opus-4-7")
    # → {"input": 5.0, "output": 25.0, "cached": 0.50}

    get_tier("claude-opus-4-7")
    # → 4

    translate_model("claude-opus-4-7", "bedrock")
    # → "anthropic.claude-opus-4-7-v1:0"
"""

from __future__ import annotations

from ._pricing import PricingContext, RateBand
from ._quote import PricingQuote, PricingQuoteUnavailable, quote_workload
from ._registry import ModelInfo, ModelRegistry, get_registry

__all__ = [
    "ModelInfo",
    "ModelRegistry",
    "PricingContext",
    "RateBand",
    "PricingQuote",
    "PricingQuoteUnavailable",
    "quote_workload",
    "get_registry",
    "get_rates",
    "get_tier",
    "get_model_costs",
    "get_pricing",
    "translate_model",
    "detect_provider",
    "get_cheaper_alternative",
    "get_shadow_target",
    "get_default_routes",
    "get_all_tiers",
    "known_models",
    "start_discovery",
    "stop_discovery",
]


def get_rates(
    model: str | None = None, *, context: PricingContext | None = None
) -> dict[str, float]:
    """Return ``{"input": X, "output": Y, "cached": Z}`` for a model.

    Scalar calls never raise and return sonnet-class defaults for unknown
    models. Context-aware calls reject ambiguous band definitions.
    """
    if not model:
        return {"input": 3.0, "output": 15.0, "cached": 0.30}
    info = get_registry().resolve(model)
    band = info.rate_band_for(context)
    input_rate = band.input_per_mtok if band is not None else info.input_per_mtok
    output_rate = band.output_per_mtok if band is not None else info.output_per_mtok
    cache_read_rate = band.cache_read_per_mtok if band is not None else info.cache_read_per_mtok
    default_cache_rate = input_rate if band is not None else input_rate * 0.1
    return {
        "input": input_rate,
        "output": output_rate,
        "cached": cache_read_rate if cache_read_rate is not None else default_cache_rate,
    }


def get_tier(model: str) -> int:
    """Return cost tier (1=budget, 2=mid, 3=premium, 4=frontier).

    Infers from family if unknown.
    """
    return get_registry().resolve(model).tier


def get_model_costs(model: str, *, context: PricingContext | None = None) -> dict[str, float]:
    """Return ``{"input": X, "output": Y}`` — simplified form for fast-path callers."""
    info = get_registry().resolve(model)
    band = info.rate_band_for(context)
    if band is not None:
        return {"input": band.input_per_mtok, "output": band.output_per_mtok}
    return {"input": info.input_per_mtok, "output": info.output_per_mtok}


def get_pricing(model: str) -> ModelInfo | None:
    """Return full ModelInfo, or None if completely unresolvable.

    In practice this always returns a ModelInfo (family inference + defaults),
    but returns None for empty string.
    """
    if not model:
        return None
    return get_registry().resolve(model)


def translate_model(model_id: str, provider: str) -> str:
    """Translate Anthropic model ID to provider-specific ID (bedrock/vertex).

    Pass-through if no translation exists.
    """
    return get_registry().translate_model(model_id, provider)


def detect_provider(model: str) -> str:
    """Detect provider from model name using prefix matching."""
    return get_registry().detect_provider(model)


def get_cheaper_alternative(model: str) -> tuple[str, float] | None:
    """Return ``(cheaper_model_id, savings_fraction)`` or None."""
    return get_registry().get_cheaper_alternative(model)


def get_shadow_target(shadow_provider: str) -> tuple[str, str]:
    """Map shadow provider string to ``(upstream_url, model_name)``.

    Returns ``("", "")`` if unknown.
    """
    return get_registry().get_shadow_target(shadow_provider)


def get_default_routes() -> dict[str, str]:
    """Return ``{model_id: provider}`` for all known models."""
    return get_registry().get_default_routes()


def get_all_tiers() -> dict[str, int]:
    """Return ``{model_id: tier}`` for all known models (incl. provider-prefixed)."""
    return get_registry().get_all_tiers()


def known_models() -> list[str]:
    """Return all registered model IDs."""
    return [m.model_id for m in get_registry().all_models()]


def start_discovery() -> None:
    """Start background model discovery (polls provider APIs)."""
    from ._discovery import start_discovery as _start

    _start()


def stop_discovery() -> None:
    """Stop background model discovery."""
    from ._discovery import stop_discovery as _stop

    _stop()
