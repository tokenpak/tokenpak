# SPDX-License-Identifier: Apache-2.0
"""Tests for the canonical license tier and feature map."""

from __future__ import annotations

from collections import Counter

import pytest

from tokenpak.agent.license import TIER_FEATURES, LicenseTier, required_tier_for
from tokenpak.agent.license.validator import (
    TIER_FEATURES as VALIDATOR_TIER_FEATURES,
)
from tokenpak.agent.license.validator import (
    LicenseTier as ValidatorLicenseTier,
)
from tokenpak.agent.license.validator import (
    required_tier_for as validator_required_tier_for,
)

EXPECTED_TIER_FEATURES = {
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
    LicenseTier.ENTERPRISE: [],
}


def test_package_import_surface() -> None:
    assert ValidatorLicenseTier is LicenseTier
    assert VALIDATOR_TIER_FEATURES is TIER_FEATURES
    assert validator_required_tier_for is required_tier_for
    assert LicenseTier.PRO.value == "pro"
    assert TIER_FEATURES
    assert required_tier_for("compression_basic") == "pro"


def test_ladder_is_ascending_and_totally_ordered() -> None:
    ladder = LicenseTier.ladder()

    assert ladder == (
        LicenseTier.FREE,
        LicenseTier.PRO,
        LicenseTier.TEAM,
        LicenseTier.ENTERPRISE,
    )
    assert LicenseTier.FREE < LicenseTier.PRO < LicenseTier.TEAM < LicenseTier.ENTERPRISE
    assert sorted(reversed(ladder)) == list(ladder)
    assert LicenseTier.ENTERPRISE >= LicenseTier.TEAM


def test_tier_feature_map_is_exact_non_empty_and_string_typed() -> None:
    assert TIER_FEATURES == EXPECTED_TIER_FEATURES
    assert TIER_FEATURES
    assert all(isinstance(tier.value, str) for tier in TIER_FEATURES)
    assert all(
        isinstance(feature, str) for features in TIER_FEATURES.values() for feature in features
    )


def test_each_feature_is_introduced_by_exactly_one_tier() -> None:
    feature_counts = Counter(feature for features in TIER_FEATURES.values() for feature in features)

    assert feature_counts
    assert set(feature_counts.values()) == {1}


@pytest.mark.parametrize(
    ("feature", "expected_tier"),
    [
        ("tokenpak_server", "team"),
        ("seat_management", "team"),
        ("team_analytics", "team"),
        ("compression_advanced", "pro"),
        ("multipak_capture", "pro"),
        ("unknown_thing", None),
    ],
)
def test_required_tier_for(feature: str, expected_tier: str | None) -> None:
    assert required_tier_for(feature) == expected_tier
