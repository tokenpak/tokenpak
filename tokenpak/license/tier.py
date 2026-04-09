"""
tokenpak.license.tier — License tier enum and feature catalogue.

Tier ordering is monotonic: ENTERPRISE > TEAM > PRO > OSS.
Using IntEnum so `tier >= LicenseTier.PRO` works for comparison.
"""
from __future__ import annotations

from enum import IntEnum


class LicenseTier(IntEnum):
    OSS = 0
    PRO = 1
    TEAM = 2
    ENTERPRISE = 3

    @classmethod
    def from_str(cls, s: str) -> "LicenseTier":
        mapping = {
            "oss": cls.OSS,
            "pro": cls.PRO,
            "team": cls.TEAM,
            "enterprise": cls.ENTERPRISE,
        }
        try:
            return mapping[s.lower()]
        except KeyError:
            return cls.OSS


# Which features are unlocked at each tier (cumulative — higher tier inherits lower).
TIER_FEATURES: dict[LicenseTier, list[str]] = {
    LicenseTier.OSS: [
        "compression_basic",
        "model_routing_local",
        "cli",
    ],
    LicenseTier.PRO: [
        "compression_basic",
        "compression_advanced",
        "model_routing_local",
        "model_routing_intelligent",
        "cli",
        "replay_store",
        "ab_testing",
        "debug_mode",
        "budget_alerts",
    ],
    LicenseTier.TEAM: [
        "compression_basic",
        "compression_advanced",
        "model_routing_local",
        "model_routing_intelligent",
        "cli",
        "replay_store",
        "ab_testing",
        "debug_mode",
        "budget_alerts",
        "tokenpak_server",
        "seat_management",
        "team_analytics",
    ],
    LicenseTier.ENTERPRISE: [
        "compression_basic",
        "compression_advanced",
        "model_routing_local",
        "model_routing_intelligent",
        "cli",
        "replay_store",
        "ab_testing",
        "debug_mode",
        "budget_alerts",
        "tokenpak_server",
        "seat_management",
        "team_analytics",
        "self_hosted_intelligence",
        "sso",
        "audit_log",
        "sla",
    ],
}
