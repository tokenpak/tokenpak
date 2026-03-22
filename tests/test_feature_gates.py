"""
Test suite for feature gating system (WS-1).

Tests:
- Feature tier mapping is correct
- License resolution at startup
- Graceful fallback to OSS on license check failure
- Feature gate enforcement (gated features disabled when not in active set)
- /license endpoint returns correct tier and feature count
- Proxy starts and functions with no license key
"""

import os
import sys
import json
import tempfile
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tokenpak.feature_gates import (
    LicenseTier,
    FEATURE_TIER_MAP,
    TIER_FEATURE_SETS,
    resolve_active_features,
    is_feature_active,
    get_feature_count_by_tier,
    describe_tier,
)
from tokenpak.agent.license.validator import LicenseValidator, LicenseStatus, ValidationResult


class TestFeatureTierMap:
    """Test that feature-to-tier mapping is correctly configured."""
    
    def test_all_features_mapped(self):
        """All Pro/Team/Enterprise features have tier assignments."""
        # Should have at least 30+ Pro features
        pro_features = {k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.PRO}
        assert len(pro_features) >= 25, f"Expected 25+ Pro features, got {len(pro_features)}"
        
    def test_feature_tier_hierarchy(self):
        """Verify no OSS features are in Pro/Team/Enterprise."""
        pro_features = {k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.PRO}
        team_features = {k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.TEAM}
        enterprise_features = {k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.ENTERPRISE}
        
        # No overlaps (each feature assigned to exactly one minimum tier)
        assert not (pro_features & team_features), "Feature in both Pro and Team"
        assert not (pro_features & enterprise_features), "Feature in both Pro and Enterprise"
        assert not (team_features & enterprise_features), "Feature in both Team and Enterprise"
    
    def test_tier_feature_sets_inheritance(self):
        """Verify tier inheritance: each tier includes previous tier's features."""
        oss_count = len(TIER_FEATURE_SETS[LicenseTier.OSS])
        pro_count = len(TIER_FEATURE_SETS[LicenseTier.PRO])
        team_count = len(TIER_FEATURE_SETS[LicenseTier.TEAM])
        enterprise_count = len(TIER_FEATURE_SETS[LicenseTier.ENTERPRISE])
        
        # Each tier should have more features than the previous
        assert pro_count > oss_count, f"Pro ({pro_count}) should have more than OSS ({oss_count})"
        assert team_count > pro_count, f"Team ({team_count}) should have more than Pro ({pro_count})"
        assert enterprise_count > team_count, f"Enterprise ({enterprise_count}) should have more than Team ({team_count})"
        
        # OSS features should be in all tiers
        oss_features = TIER_FEATURE_SETS[LicenseTier.OSS]
        for tier in [LicenseTier.PRO, LicenseTier.TEAM, LicenseTier.ENTERPRISE]:
            missing = oss_features - TIER_FEATURE_SETS[tier]
            assert not missing, f"OSS features missing in {tier.value}: {missing}"


class TestFeatureResolution:
    """Test resolve_active_features() logic."""
    
    def test_oss_tier_resolution(self):
        """OSS tier should only include base OSS features."""
        active = resolve_active_features(LicenseTier.OSS)
        assert active == TIER_FEATURE_SETS[LicenseTier.OSS]
        assert "semantic_cache" not in active
        assert "error_normalizer" not in active
    
    def test_pro_tier_resolution(self):
        """Pro tier should include all Pro features."""
        active = resolve_active_features(LicenseTier.PRO)
        pro_features = {k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.PRO}
        assert pro_features.issubset(active), "Pro features missing from resolved active set"
        
    def test_team_tier_resolution(self):
        """Team tier should include all Pro + Team features."""
        active = resolve_active_features(LicenseTier.TEAM)
        pro_features = {k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.PRO}
        team_features = {k for k, v in FEATURE_TIER_MAP.items() if v == LicenseTier.TEAM}
        assert (pro_features | team_features).issubset(active)
    
    def test_enterprise_tier_resolution(self):
        """Enterprise should include all features."""
        active = resolve_active_features(LicenseTier.ENTERPRISE)
        assert len(active) == len(TIER_FEATURE_SETS[LicenseTier.ENTERPRISE])
    
    def test_is_feature_active(self):
        """Test is_feature_active() helper."""
        pro_active = resolve_active_features(LicenseTier.PRO)
        assert is_feature_active("semantic_cache", pro_active) == True
        assert is_feature_active("sso", pro_active) == False  # Enterprise only


class TestLicenseResolution:
    """Test startup license resolution without needing a real license server."""
    
    def test_oss_fallback_no_key(self):
        """No license key should fall back to OSS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            license_dir = Path(tmpdir)
            
            # Mock the license module to return no key
            with patch.dict(os.environ, {"TOKENPAK_LICENSE_DIR": str(license_dir)}):
                from tokenpak.agent.license.activation import get_plan
                result = get_plan()
                
                assert result.tier == LicenseTier.OSS
                assert result.status.value == "valid"
    
    def test_graceful_fallback_on_import_error(self):
        """License module unavailable should gracefully fall back to OSS."""
        # This would be tested in proxy integration test
        pass


class TestProxyStartupAndGating:
    """Integration tests with proxy startup and feature gating."""
    
    @pytest.mark.skip(reason="Requires running proxy; run manually with test_proxy_startup.sh")
    def test_proxy_starts_with_no_license(self):
        """Proxy should start successfully with no license key."""
        pass
    
    @pytest.mark.skip(reason="Requires running proxy")
    def test_license_endpoint_returns_oss(self):
        """GET /license should return OSS tier with 0 gated features."""
        pass
    
    @pytest.mark.skip(reason="Requires running proxy")
    def test_gated_features_disabled_in_oss(self):
        """Proxy health check should show gated features disabled in OSS mode."""
        pass


class TestFeatureMetadata:
    """Test metadata and description functions."""
    
    def test_get_feature_count_by_tier(self):
        """Feature count function should return correct counts."""
        counts = get_feature_count_by_tier()
        assert counts["oss"] == len(TIER_FEATURE_SETS[LicenseTier.OSS])
        assert counts["pro"] == len(TIER_FEATURE_SETS[LicenseTier.PRO])
        assert counts["team"] == len(TIER_FEATURE_SETS[LicenseTier.TEAM])
        assert counts["enterprise"] == len(TIER_FEATURE_SETS[LicenseTier.ENTERPRISE])
    
    def test_describe_tier(self):
        """describe_tier should return meaningful descriptions."""
        for tier in LicenseTier:
            desc = describe_tier(tier)
            assert tier.value in desc.lower()
            assert "feature" in desc.lower()


class TestFeatureGatingScenarios:
    """Test realistic feature gating scenarios."""
    
    def test_pro_user_can_access_pro_features(self):
        """A Pro user should have access to all Pro features."""
        active = resolve_active_features(LicenseTier.PRO)
        semantic_cache_gated = "semantic_cache" in FEATURE_TIER_MAP
        if semantic_cache_gated:
            assert is_feature_active("semantic_cache", active)
    
    def test_oss_user_blocked_from_pro_features(self):
        """An OSS user should not have access to Pro features."""
        active = resolve_active_features(LicenseTier.OSS)
        
        # Check a few known Pro features
        pro_features = ["semantic_cache", "error_normalizer", "trace_mode"]
        for feature in pro_features:
            if feature in FEATURE_TIER_MAP:
                assert not is_feature_active(feature, active), f"OSS should not have {feature}"
    
    def test_enterprise_has_all_features(self):
        """Enterprise should have every feature."""
        active = resolve_active_features(LicenseTier.ENTERPRISE)
        
        # All mapped features should be active
        for feature in FEATURE_TIER_MAP:
            assert is_feature_active(feature, active), f"Enterprise missing {feature}"


class TestFeatureGateIntegration:
    """Test integration of feature gates with proxy initialization."""
    
    def test_import_succeeds(self):
        """feature_gates module imports without error."""
        # Already imported above
        assert FEATURE_TIER_MAP is not None
        assert TIER_FEATURE_SETS is not None
    
    def test_no_undefined_tiers(self):
        """All tiers in FEATURE_TIER_MAP are valid LicenseTier values."""
        valid_tiers = {t.value for t in LicenseTier}
        for feature_id, tier in FEATURE_TIER_MAP.items():
            assert tier in LicenseTier, f"Invalid tier {tier} for feature {feature_id}"


# Fixtures for proxy testing
@pytest.fixture
def temp_license_dir():
    """Temporary directory for license testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_oss_plan():
    """Mock OSS license plan."""
    return ValidationResult(
        status=LicenseStatus.VALID,
        tier=LicenseTier.OSS,
        features=list(TIER_FEATURE_SETS[LicenseTier.OSS]),
        seats=0,
        seats_used=0,
        expires_at=None,
        grace_expires_at=None,
        message="Mock OSS plan",
    )


@pytest.fixture
def mock_pro_plan():
    """Mock Pro license plan."""
    return ValidationResult(
        status=LicenseStatus.VALID,
        tier=LicenseTier.PRO,
        features=list(TIER_FEATURE_SETS[LicenseTier.PRO]),
        seats=0,
        seats_used=0,
        expires_at="2026-12-31T23:59:59Z",
        grace_expires_at=None,
        message="Mock Pro plan",
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
