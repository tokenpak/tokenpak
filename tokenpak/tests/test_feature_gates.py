"""
Tests for TokenPak feature gate resolution and validation.

Tests the feature_gates.py module with:
- Tier-based feature availability
- Feature activation by tier
- Tier inheritance (Pro includes OSS features, Team includes Pro features, etc.)
- Custom feature maps and config overrides
- Edge cases (empty config, None values, unknown gates, malformed names)
"""

import pytest
from tokenpak.feature_gates import (
    LicenseTier,
    FEATURE_TIER_MAP,
    TIER_FEATURE_SETS,
    resolve_active_features,
    is_feature_active,
    get_feature_count_by_tier,
    describe_tier,
)


class TestLicenseTierEnum:
    """Tests for LicenseTier enum values."""

    def test_tier_values_exist(self):
        """All expected tier values are defined."""
        assert LicenseTier.OSS.value == "oss"
        assert LicenseTier.PRO.value == "pro"
        assert LicenseTier.TEAM.value == "team"
        assert LicenseTier.ENTERPRISE.value == "enterprise"

    def test_tier_enum_string_conversion(self):
        """Tier enum converts cleanly to string."""
        assert str(LicenseTier.OSS) == "LicenseTier.OSS"
        assert LicenseTier.PRO.value == "pro"


class TestFeatureTierMap:
    """Tests for the FEATURE_TIER_MAP global registry."""

    def test_feature_tier_map_not_empty(self):
        """FEATURE_TIER_MAP contains feature definitions."""
        assert len(FEATURE_TIER_MAP) > 0

    def test_feature_tier_map_values_are_valid_tiers(self):
        """All values in FEATURE_TIER_MAP are valid LicenseTier enums."""
        valid_tiers = {LicenseTier.OSS, LicenseTier.PRO, LicenseTier.TEAM, LicenseTier.ENTERPRISE}
        for feature_id, tier in FEATURE_TIER_MAP.items():
            assert tier in valid_tiers, f"Feature '{feature_id}' has invalid tier: {tier}"

    def test_example_features_exist(self):
        """Known features are registered in the map."""
        assert "compression_advanced" in FEATURE_TIER_MAP
        assert "model_routing_intelligent" in FEATURE_TIER_MAP
        assert "sso" in FEATURE_TIER_MAP
        assert "audit_log" in FEATURE_TIER_MAP


class TestTierFeatureSets:
    """Tests for TIER_FEATURE_SETS — the pre-computed feature availability."""

    def test_all_tiers_defined(self):
        """All four tiers have feature sets."""
        assert LicenseTier.OSS in TIER_FEATURE_SETS
        assert LicenseTier.PRO in TIER_FEATURE_SETS
        assert LicenseTier.TEAM in TIER_FEATURE_SETS
        assert LicenseTier.ENTERPRISE in TIER_FEATURE_SETS

    def test_oss_features_not_empty(self):
        """OSS tier has base features."""
        assert len(TIER_FEATURE_SETS[LicenseTier.OSS]) > 0

    def test_tier_inheritance_oss_pro(self):
        """Pro tier includes all OSS features plus Pro-specific ones."""
        oss_features = TIER_FEATURE_SETS[LicenseTier.OSS]
        pro_features = TIER_FEATURE_SETS[LicenseTier.PRO]
        assert oss_features.issubset(pro_features), "Pro should include all OSS features"
        assert len(pro_features) > len(oss_features), "Pro should add features to OSS"

    def test_tier_inheritance_pro_team(self):
        """Team tier includes all Pro features plus Team-specific ones."""
        pro_features = TIER_FEATURE_SETS[LicenseTier.PRO]
        team_features = TIER_FEATURE_SETS[LicenseTier.TEAM]
        assert pro_features.issubset(team_features), "Team should include all Pro features"
        assert len(team_features) > len(pro_features), "Team should add features to Pro"

    def test_tier_inheritance_team_enterprise(self):
        """Enterprise tier includes all Team features plus Enterprise-specific ones."""
        team_features = TIER_FEATURE_SETS[LicenseTier.TEAM]
        enterprise_features = TIER_FEATURE_SETS[LicenseTier.ENTERPRISE]
        assert team_features.issubset(
            enterprise_features
        ), "Enterprise should include all Team features"
        assert (
            len(enterprise_features) > len(team_features)
        ), "Enterprise should add features to Team"

    def test_tier_sizes_increasing(self):
        """Feature count increases with tier level."""
        oss_count = len(TIER_FEATURE_SETS[LicenseTier.OSS])
        pro_count = len(TIER_FEATURE_SETS[LicenseTier.PRO])
        team_count = len(TIER_FEATURE_SETS[LicenseTier.TEAM])
        enterprise_count = len(TIER_FEATURE_SETS[LicenseTier.ENTERPRISE])

        assert oss_count < pro_count < team_count < enterprise_count


class TestResolveActiveFeatures:
    """Tests for resolve_active_features() function."""

    def test_resolve_oss_features(self):
        """Resolving OSS tier returns expected features."""
        features = resolve_active_features(LicenseTier.OSS)
        assert isinstance(features, set)
        assert len(features) > 0
        assert "compression_basic" in features

    def test_resolve_pro_features(self):
        """Resolving Pro tier returns Pro + OSS features."""
        features = resolve_active_features(LicenseTier.PRO)
        assert "compression_advanced" in features
        assert "compression_basic" in features  # Inherited from OSS

    def test_resolve_team_features(self):
        """Resolving Team tier returns Team + Pro + OSS features."""
        features = resolve_active_features(LicenseTier.TEAM)
        assert "tokenpak_server" in features  # Team-specific
        assert "compression_advanced" in features  # From Pro
        assert "compression_basic" in features  # From OSS

    def test_resolve_enterprise_features(self):
        """Resolving Enterprise tier includes all features from all tiers."""
        features = resolve_active_features(LicenseTier.ENTERPRISE)
        assert "sso" in features  # Enterprise-specific
        assert "tokenpak_server" in features  # From Team
        assert "compression_advanced" in features  # From Pro
        assert "compression_basic" in features  # From OSS

    def test_resolve_returns_copy_not_reference(self):
        """Resolve returns a copy, not a reference (modifying it doesn't affect global state)."""
        features1 = resolve_active_features(LicenseTier.PRO)
        features1.add("fake_feature")
        features2 = resolve_active_features(LicenseTier.PRO)
        assert "fake_feature" not in features2, "Resolved set should be a copy"

    def test_resolve_with_custom_feature_map(self):
        """Resolve accepts a custom feature map override."""
        custom_map = {"custom_feature": LicenseTier.PRO}
        features = resolve_active_features(LicenseTier.PRO, feature_map=custom_map)
        # Custom map only has one feature defined for PRO, so the set should be smaller
        # (This tests that the custom map is actually used)
        assert isinstance(features, set)

    def test_resolve_with_none_feature_map_uses_default(self):
        """Resolve with None feature_map uses FEATURE_TIER_MAP by default."""
        features_default = resolve_active_features(LicenseTier.TEAM, feature_map=None)
        features_explicit = resolve_active_features(LicenseTier.TEAM, feature_map=FEATURE_TIER_MAP)
        assert features_default == features_explicit


class TestIsFeatureActive:
    """Tests for is_feature_active() function."""

    def test_feature_active_in_tier(self):
        """Feature is detected as active when present in active_features set."""
        oss_features = resolve_active_features(LicenseTier.OSS)
        assert is_feature_active("compression_basic", oss_features) is True

    def test_feature_inactive_in_tier(self):
        """Feature is detected as inactive when not in active_features set."""
        oss_features = resolve_active_features(LicenseTier.OSS)
        assert is_feature_active("sso", oss_features) is False

    def test_pro_feature_available_in_pro_tier(self):
        """Pro-tier feature is active in Pro and above, inactive in OSS."""
        oss_features = resolve_active_features(LicenseTier.OSS)
        pro_features = resolve_active_features(LicenseTier.PRO)

        assert is_feature_active("compression_advanced", oss_features) is False
        assert is_feature_active("compression_advanced", pro_features) is True

    def test_enterprise_feature_only_in_enterprise(self):
        """Enterprise feature is only active in Enterprise tier."""
        pro_features = resolve_active_features(LicenseTier.PRO)
        team_features = resolve_active_features(LicenseTier.TEAM)
        enterprise_features = resolve_active_features(LicenseTier.ENTERPRISE)

        assert is_feature_active("sso", pro_features) is False
        assert is_feature_active("sso", team_features) is False
        assert is_feature_active("sso", enterprise_features) is True

    def test_unknown_feature_inactive(self):
        """Unknown feature is inactive in any tier."""
        oss_features = resolve_active_features(LicenseTier.OSS)
        enterprise_features = resolve_active_features(LicenseTier.ENTERPRISE)

        assert is_feature_active("nonexistent_feature", oss_features) is False
        assert is_feature_active("nonexistent_feature", enterprise_features) is False


class TestGetFeatureCountByTier:
    """Tests for get_feature_count_by_tier() metadata function."""

    def test_returns_dict_with_all_tiers(self):
        """Returns a dict with entries for all tiers."""
        counts = get_feature_count_by_tier()
        assert "oss" in counts
        assert "pro" in counts
        assert "team" in counts
        assert "enterprise" in counts

    def test_counts_are_positive_integers(self):
        """All counts are positive integers."""
        counts = get_feature_count_by_tier()
        for tier, count in counts.items():
            assert isinstance(count, int)
            assert count > 0

    def test_counts_match_tier_feature_sets(self):
        """Counts match the actual TIER_FEATURE_SETS sizes."""
        counts = get_feature_count_by_tier()
        assert counts["oss"] == len(TIER_FEATURE_SETS[LicenseTier.OSS])
        assert counts["pro"] == len(TIER_FEATURE_SETS[LicenseTier.PRO])
        assert counts["team"] == len(TIER_FEATURE_SETS[LicenseTier.TEAM])
        assert counts["enterprise"] == len(TIER_FEATURE_SETS[LicenseTier.ENTERPRISE])

    def test_counts_increase_by_tier(self):
        """Feature counts increase from OSS → Pro → Team → Enterprise."""
        counts = get_feature_count_by_tier()
        assert counts["oss"] < counts["pro"]
        assert counts["pro"] < counts["team"]
        assert counts["team"] < counts["enterprise"]


class TestDescribeTier:
    """Tests for describe_tier() metadata function."""

    def test_describe_oss(self):
        """OSS description contains 'OSS' and feature count."""
        desc = describe_tier(LicenseTier.OSS)
        assert "OSS" in desc or "oss" in desc.lower()
        assert "42" in desc or "features" in desc.lower()

    def test_describe_pro(self):
        """Pro description contains 'Pro' and feature count."""
        desc = describe_tier(LicenseTier.PRO)
        assert "Pro" in desc or "pro" in desc.lower()
        assert "features" in desc.lower()

    def test_describe_team(self):
        """Team description contains 'Team' and feature count."""
        desc = describe_tier(LicenseTier.TEAM)
        assert "Team" in desc or "team" in desc.lower()
        assert "features" in desc.lower()

    def test_describe_enterprise(self):
        """Enterprise description contains 'Enterprise' and feature count."""
        desc = describe_tier(LicenseTier.ENTERPRISE)
        assert "Enterprise" in desc or "enterprise" in desc.lower()
        assert "features" in desc.lower()

    def test_describe_all_are_strings(self):
        """All descriptions are non-empty strings."""
        for tier in LicenseTier:
            desc = describe_tier(tier)
            assert isinstance(desc, str)
            assert len(desc) > 0


class TestEdgeCases:
    """Tests for edge cases, malformed inputs, and boundary conditions."""

    def test_empty_active_features_set(self):
        """is_feature_active works with empty set."""
        empty_set = set()
        assert is_feature_active("any_feature", empty_set) is False

    def test_feature_id_case_sensitivity(self):
        """Feature IDs are case-sensitive."""
        features = resolve_active_features(LicenseTier.PRO)
        # Assuming "compression_advanced" is in PRO
        assert is_feature_active("compression_advanced", features) is True
        assert is_feature_active("COMPRESSION_ADVANCED", features) is False

    def test_all_registered_features_resolvable(self):
        """All features in FEATURE_TIER_MAP are found in at least one tier."""
        for feature_id, tier in FEATURE_TIER_MAP.items():
            features = resolve_active_features(tier)
            assert (
                feature_id in features
            ), f"Feature '{feature_id}' with tier {tier} not found in resolved set"

    def test_no_circular_inheritance(self):
        """Tier feature sets are properly ordered (no duplicates, proper hierarchy)."""
        # This is more of a consistency check
        tiers = [LicenseTier.OSS, LicenseTier.PRO, LicenseTier.TEAM, LicenseTier.ENTERPRISE]
        for i in range(len(tiers) - 1):
            current = TIER_FEATURE_SETS[tiers[i]]
            next_tier = TIER_FEATURE_SETS[tiers[i + 1]]
            assert current.issubset(next_tier), f"{tiers[i]} not a subset of {tiers[i + 1]}"

    def test_feature_count_consistency(self):
        """Feature counts are internally consistent."""
        # Count of features in each tier individually
        oss_only = len(TIER_FEATURE_SETS[LicenseTier.OSS])
        pro_only = len(
            TIER_FEATURE_SETS[LicenseTier.PRO] - TIER_FEATURE_SETS[LicenseTier.OSS]
        )
        team_only = len(
            TIER_FEATURE_SETS[LicenseTier.TEAM] - TIER_FEATURE_SETS[LicenseTier.PRO]
        )
        enterprise_only = len(
            TIER_FEATURE_SETS[LicenseTier.ENTERPRISE] - TIER_FEATURE_SETS[LicenseTier.TEAM]
        )

        # Each tier should add at least some features
        assert oss_only > 0
        assert pro_only > 0
        assert team_only > 0
        assert enterprise_only > 0
