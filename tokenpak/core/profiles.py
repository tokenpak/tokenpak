"""TokenPak compression profiles.

Predefined configurations for different use cases.
"""

__all__ = (
    "PROFILES",
    "apply_profile",
    "get_profile",
    "profile_to_yaml",
)

from typing import Any, Dict, TypedDict

import yaml


class ProfileFeature(TypedDict):
    """Toggle settings for one profile feature."""

    enabled: bool


class Profile(TypedDict):
    """Schema shared by all built-in compression profiles."""

    name: str
    description: str
    features: dict[str, ProfileFeature]


PROFILES: dict[str, Profile] = {
    "minimal": {
        "name": "minimal",
        "description": "Compression only (safest, ~5% savings)",
        "features": {
            "compression": {"enabled": True},
            "semantic_cache": {"enabled": False},
            "prefix_registry": {"enabled": False},
            "query_rewriter": {"enabled": False},
            "error_normalizer": {"enabled": False},
            "fidelity_tiers": {"enabled": False},
            "tokenizer_cache": {"enabled": False},
            "request_coalescing": {"enabled": False},
            "response_dedup": {"enabled": False},
            "header_optimization": {"enabled": False},
            "cost_model": {"enabled": False},
            "adaptive_routing": {"enabled": False},
            "intent_classifier": {"enabled": False},
            "latency_predictor": {"enabled": False},
            "sampling_engine": {"enabled": False},
            "fallback_policy": {"enabled": False},
        },
    },
    "balanced": {
        "name": "balanced",
        "description": "Compression + smart caching + routing (~30% savings)",
        "features": {
            "compression": {"enabled": True},
            "semantic_cache": {"enabled": True},
            "prefix_registry": {"enabled": True},
            "query_rewriter": {"enabled": True},
            "error_normalizer": {"enabled": True},
            "fidelity_tiers": {"enabled": True},
            "tokenizer_cache": {"enabled": False},
            "request_coalescing": {"enabled": False},
            "response_dedup": {"enabled": False},
            "header_optimization": {"enabled": False},
            "cost_model": {"enabled": False},
            "adaptive_routing": {"enabled": False},
            "intent_classifier": {"enabled": False},
            "latency_predictor": {"enabled": False},
            "sampling_engine": {"enabled": False},
            "fallback_policy": {"enabled": False},
        },
    },
    "aggressive": {
        "name": "aggressive",
        "description": "All optimizations enabled (maximum savings, ~40%+)",
        "features": {
            "compression": {"enabled": True},
            "semantic_cache": {"enabled": True},
            "prefix_registry": {"enabled": True},
            "query_rewriter": {"enabled": True},
            "error_normalizer": {"enabled": True},
            "fidelity_tiers": {"enabled": True},
            "tokenizer_cache": {"enabled": True},
            "request_coalescing": {"enabled": True},
            "response_dedup": {"enabled": True},
            "header_optimization": {"enabled": True},
            "cost_model": {"enabled": True},
            "adaptive_routing": {"enabled": True},
            "intent_classifier": {"enabled": True},
            "latency_predictor": {"enabled": True},
            "sampling_engine": {"enabled": True},
            "fallback_policy": {"enabled": True},
        },
    },
}


def get_profile(name: str) -> Profile:
    """Get a profile by name.

    Args:
        name: Profile name (minimal, balanced, or aggressive)

    Returns:
        Profile configuration dict

    Raises:
        ValueError: If profile name not found
    """
    if name not in PROFILES:
        available = ", ".join(PROFILES.keys())
        raise ValueError(f"Unknown profile: {name}. Available: {available}")
    return PROFILES[name]


def apply_profile(name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a profile to a config dict.

    Args:
        name: Profile name
        config: Existing config dict

    Returns:
        Updated config with profile features applied
    """
    profile = get_profile(name)
    if "modules" not in config:
        config["modules"] = {}
    config["modules"].update(profile["features"])
    config["profile"] = name
    return config


def profile_to_yaml(name: str, config_base: Dict[str, Any]) -> str:
    """Convert a profile to YAML format.

    Args:
        name: Profile name
        config_base: Base config dict with proxy settings

    Returns:
        YAML string
    """
    config = apply_profile(name, config_base.copy())
    rendered = yaml.dump(config, default_flow_style=False, sort_keys=False)
    if not isinstance(rendered, str):
        raise TypeError("yaml.dump returned a non-text result")
    return rendered
