"""
TokenPak Feature Gates Registry

Maps all 50+ gated features to their minimum tier requirements.
This is the source of truth for feature availability across OSS, Pro, Team, Enterprise tiers.

Tier Summary:
- OSS/Free (42 features): Basic compression, routing, CLI, core infrastructure
- Pro (30+ features): Advanced compression, intelligent routing, replay, A/B testing
- Team (8+ features): Collaboration (seats, analytics, server mode)
- Enterprise (12+ features): Governance (SSO, audit, air-gap, SLA)

Features are checked ONCE at startup and cached in ACTIVE_FEATURES dict.
Zero per-request latency impact.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Set, Optional

# ─────────────────────────────────────────────────────────────────────
# License Tier Enum (must match validator.py)
# ─────────────────────────────────────────────────────────────────────


class LicenseTier(str, Enum):
    """Licensing tier levels."""

    OSS = "oss"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


# ─────────────────────────────────────────────────────────────────────
# Core Feature Tier Map
# Maps feature_id → minimum required tier
# ─────────────────────────────────────────────────────────────────────

FEATURE_TIER_MAP: Dict[str, LicenseTier] = {
    # ─────────────────────────────────────────────────────────────────
    # Free / OSS (42 features) — always available, no gating needed
    # ─────────────────────────────────────────────────────────────────
    # These are informational only; proxy doesn't gate them
    # "compression_basic": LicenseTier.OSS,
    # "model_routing_local": LicenseTier.OSS,
    # "cli": LicenseTier.OSS,
    # "proxy_server": LicenseTier.OSS,
    # "request_parsing": LicenseTier.OSS,
    # "response_formatting": LicenseTier.OSS,
    # ... 36 more OSS features not gated
    # ─────────────────────────────────────────────────────────────────
    # PRO Features (30 features + inherits from OSS)
    # ─────────────────────────────────────────────────────────────────
    "compression_advanced": LicenseTier.PRO,
    "compression_dict": LicenseTier.PRO,  # TOKENPAK_COMPRESSION_DICT
    "model_routing_intelligent": LicenseTier.PRO,  # TOKENPAK_ROUTER_ENABLED (smart routing)
    "semantic_cache": LicenseTier.PRO,  # TOKENPAK_SEMANTIC_CACHE
    "prefix_registry": LicenseTier.PRO,  # TOKENPAK_PREFIX_REGISTRY
    "replay_store": LicenseTier.PRO,  # Replay storage for A/B testing
    "ab_testing": LicenseTier.PRO,  # A/B testing framework
    "debug_mode": LicenseTier.PRO,  # Enhanced debugging output
    "error_normalizer": LicenseTier.PRO,  # TOKENPAK_ERROR_NORMALIZER
    "budget_controller": LicenseTier.PRO,  # TOKENPAK_BUDGET_CONTROLLER
    "request_logger": LicenseTier.PRO,  # TOKENPAK_REQUEST_LOGGER
    "salience_router": LicenseTier.PRO,  # TOKENPAK_SALIENCE_ROUTER
    "retrieval_watchdog": LicenseTier.PRO,  # TOKENPAK_RETRIEVAL_WATCHDOG
    "failure_memory": LicenseTier.PRO,  # TOKENPAK_FAILURE_MEMORY
    "fidelity_tiers": LicenseTier.PRO,  # TOKENPAK_FIDELITY_TIERS (request quality levels)
    "session_capsules": LicenseTier.PRO,  # TOKENPAK_SESSION_CAPSULES
    "precondition_gates": LicenseTier.PRO,  # TOKENPAK_PRECONDITION_GATES
    "query_rewriter": LicenseTier.PRO,  # TOKENPAK_QUERY_REWRITER
    "stability_scorer": LicenseTier.PRO,  # TOKENPAK_STABILITY_SCORER
    "trace_mode": LicenseTier.PRO,  # TOKENPAK_TRACE (pipeline tracing)
    "cache_registry": LicenseTier.PRO,  # TOKENPAK_CACHE_REGISTRY (unified cache)
    "term_resolver": LicenseTier.PRO,  # TOKENPAK_TERM_RESOLVER_ENABLED (term→card resolution)
    "skeleton_extraction": LicenseTier.PRO,  # TOKENPAK_SKELETON_ENABLED (code skeletonization)
    "shadow_reader": LicenseTier.PRO,  # TOKENPAK_SHADOW_ENABLED (parallel read paths)
    "capsule_builder": LicenseTier.PRO,  # TOKENPAK_CAPSULE_BUILDER
    "vault_injection": LicenseTier.PRO,  # Vault context injection (INJECT_BUDGET > 0)
    "bm25_retrieval": LicenseTier.PRO,  # BM25 vault search backend
    "adaptive_injection": LicenseTier.PRO,  # Smart vault block selection
    "compression_cache": LicenseTier.PRO,  # Compression result caching
    "model_telemetry": LicenseTier.PRO,  # Per-model usage tracking
    "performance_metrics": LicenseTier.PRO,  # Response time + latency metrics
    # ─────────────────────────────────────────────────────────────────
    # TEAM Features (8+ features + inherits from Pro + OSS)
    # ─────────────────────────────────────────────────────────────────
    "tokenpak_server": LicenseTier.TEAM,  # Multi-machine server mode
    "seat_management": LicenseTier.TEAM,  # Seat allocation + enforcement
    "team_analytics": LicenseTier.TEAM,  # Shared team analytics dashboard
    "multi_user_auth": LicenseTier.TEAM,  # User authentication + RBAC
    "shared_config": LicenseTier.TEAM,  # Team-shared configuration
    "api_tokens": LicenseTier.TEAM,  # API token management for team
    "usage_attribution": LicenseTier.TEAM,  # Track usage per user
    "team_audit_log": LicenseTier.TEAM,  # Basic team audit trail
    # ─────────────────────────────────────────────────────────────────
    # ENTERPRISE Features (12+ features + inherits from Team + Pro + OSS)
    # ─────────────────────────────────────────────────────────────────
    "self_hosted_intelligence": LicenseTier.ENTERPRISE,  # Run custom inference servers
    "sso": LicenseTier.ENTERPRISE,  # Single sign-on (OIDC/SAML)
    "audit_log": LicenseTier.ENTERPRISE,  # Full compliance audit trail
    "sla_support": LicenseTier.ENTERPRISE,  # SLA guarantees + priority support
    "offline_activation": LicenseTier.ENTERPRISE,  # Air-gapped license activation (90d JWT)
    "multi_machine": LicenseTier.ENTERPRISE,  # Unlimited machines on one license
    "device_fingerprint": LicenseTier.ENTERPRISE,  # Device registration + management
    "encryption_at_rest": LicenseTier.ENTERPRISE,  # Encrypted local caches
    "compliance_export": LicenseTier.ENTERPRISE,  # HIPAA/SOC2 compliance export
    "custom_retention": LicenseTier.ENTERPRISE,  # Custom log retention policies
    "concurrent_limits": LicenseTier.ENTERPRISE,  # Enforce concurrent request limits
    "rate_limit_override": LicenseTier.ENTERPRISE,  # Custom rate limiting rules
}

# ─────────────────────────────────────────────────────────────────────
# Tier Inheritance — features available in each tier
# ─────────────────────────────────────────────────────────────────────

TIER_FEATURE_SETS: Dict[LicenseTier, Set[str]] = {
    LicenseTier.OSS: {
        # 42 base features (not gated, always available)
        "compression_basic",
        "model_routing_local",
        "cli",
        "proxy_server",
        "request_parsing",
        "response_formatting",
        "gzip_support",
        "json_parsing",
        "error_handling",
        "rate_limiting_basic",
        "connection_pooling_basic",
        "timeout_handling",
        "retry_logic",
        "health_checks",
        "metrics_basic",
        "logging_basic",
        "config_loading",
        "env_override",
        "file_validation",
        "schema_validation",
        "version_check",
        "dependency_resolution",
        "vendor_classification",
        "model_detection",
        "api_dispatch",
        "request_id_tracking",
        "request_deduplication",
        "caching_basic",
        "memory_pooling",
        "buffer_management",
        "stdio_capture",
        "profiling_basic",
        "documentation_generation",
        "schema_documentation",
        "api_documentation",
        "test_fixtures",
        "benchmark_framework",
        "monitoring_basic",
        "alerts_basic",
        "version_management",
        "changelog_tracking",
    },
}

# Pro inherits OSS + adds Pro features
_pro_features = TIER_FEATURE_SETS[LicenseTier.OSS].copy()
_pro_features.update({k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.PRO})
TIER_FEATURE_SETS[LicenseTier.PRO] = _pro_features

# Team inherits Pro + adds Team features
_team_features = TIER_FEATURE_SETS[LicenseTier.PRO].copy()
_team_features.update({k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.TEAM})
TIER_FEATURE_SETS[LicenseTier.TEAM] = _team_features

# Enterprise inherits Team + adds Enterprise features
_enterprise_features = TIER_FEATURE_SETS[LicenseTier.TEAM].copy()
_enterprise_features.update({k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.ENTERPRISE})
TIER_FEATURE_SETS[LicenseTier.ENTERPRISE] = _enterprise_features

# ─────────────────────────────────────────────────────────────────────
# Feature Gate Resolution
# ─────────────────────────────────────────────────────────────────────


def resolve_active_features(
    current_tier: LicenseTier,
    feature_map: Optional[Dict[str, LicenseTier]] = None,
) -> Set[str]:
    """
    Resolve which features are active for a given tier.

    Args:
        current_tier: The current license tier
        feature_map: Optional custom feature map (defaults to FEATURE_TIER_MAP)

    Returns:
        Set of feature IDs that should be active in this tier
    """
    if feature_map is None:
        feature_map = FEATURE_TIER_MAP

    # Return the pre-computed tier feature set
    return TIER_FEATURE_SETS.get(current_tier, set()).copy()


def is_feature_active(feature_id: str, active_features: Set[str]) -> bool:
    """
    Check if a specific feature is active in the current session.

    Args:
        feature_id: The feature to check
        active_features: The set of active features (from resolve_active_features)

    Returns:
        True if the feature is active, False otherwise
    """
    return feature_id in active_features


# ─────────────────────────────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────────────────────────────


def get_feature_count_by_tier() -> Dict[str, int]:
    """Return the number of features available in each tier."""
    return {tier.value: len(TIER_FEATURE_SETS[tier]) for tier in LicenseTier}


def describe_tier(tier: LicenseTier) -> str:
    """Get a human-readable description of a tier."""
    descriptions = {
        LicenseTier.OSS: f"OSS/Free — {len(TIER_FEATURE_SETS[LicenseTier.OSS])} base features",
        LicenseTier.PRO: f"Pro — {len(TIER_FEATURE_SETS[LicenseTier.PRO])} features (advanced compression, routing, debugging)",
        LicenseTier.TEAM: f"Team — {len(TIER_FEATURE_SETS[LicenseTier.TEAM])} features (collaboration, seat management)",
        LicenseTier.ENTERPRISE: f"Enterprise — {len(TIER_FEATURE_SETS[LicenseTier.ENTERPRISE])} features (SSO, audit, air-gap)",
    }
    return descriptions.get(tier, f"Unknown tier: {tier}")


if __name__ == "__main__":
    # Display feature mapping on module import for debugging
    for tier in LicenseTier:
        count = len(TIER_FEATURE_SETS[tier])
        print(f"✓ {describe_tier(tier)}")
