"""TokenPak compression profiles.

Predefined configurations for different use cases.
"""

from typing import Any, Dict

import yaml

PROFILES = {
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


def get_profile(name: str) -> Dict[str, Any]:
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
    profile = get_profile(name)
    config = apply_profile(name, config_base.copy())
    return yaml.dump(config, default_flow_style=False, sort_keys=False)
