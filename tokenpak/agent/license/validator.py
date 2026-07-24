# SPDX-License-Identifier: Apache-2.0
"""Canonical mapping from paid features to their minimum license tier."""

from __future__ import annotations

from enum import Enum
from functools import total_ordering


@total_ordering
class LicenseTier(Enum):
    """License tiers ordered from least to most capable."""

    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"

    @classmethod
    def ladder(cls) -> tuple["LicenseTier", ...]:
        """Return license tiers in ascending capability order."""
        return (cls.FREE, cls.PRO, cls.TEAM, cls.ENTERPRISE)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, LicenseTier):
            return NotImplemented
        return self.ladder().index(self) < self.ladder().index(other)


TIER_FEATURES: dict[LicenseTier, list[str]] = {
    LicenseTier.FREE: [],
    LicenseTier.PRO: [
        "ab_testing",
        "cli",
        "compression_advanced",
        "compression_basic",
        "debug_mode",
        "model_routing_intelligent",
        "model_routing_local",
        "multipak_capture",
        "replay_store",
    ],
    LicenseTier.TEAM: [
        "tokenpak_server",
        "seat_management",
        "team_analytics",
    ],
    LicenseTier.ENTERPRISE: [
        "audit_log",
        "sla",
    ],
}


def required_tier_for(feature: str) -> str | None:
    """Return the lowest tier that introduces a feature, or ``None``."""
    for tier in LicenseTier.ladder():
        if feature in TIER_FEATURES[tier]:
            return tier.value
    return None
