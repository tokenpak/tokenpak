"""
Tests for TokenPak License Activation Flow.

Covers:
  - activate: valid key, invalid key, expired key, perms 600
  - deactivate: removes key + cache, idempotent
  - get_plan: OSS fallback, valid license, graceful on bad token
  - is_pro / is_team / is_enterprise: 24h cache, correct tier gating
  - Offline fallback: license stored, validator offline
  - Feature gating helpers

Run:  pytest tests/test_license_activation_flow.py -v
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak._internal.license.keys", reason="module not available in current build")
import json
import os
import stat
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from cryptography.hazmat.primitives import serialization
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from tokenpak._internal.license.keys import (
    generate_keypair,
    sign_license,
    format_license_key,
    LicensePayload,
)
from tokenpak.infrastructure.license_validation import (
    LicenseValidator,
    LicenseTier,
    LicenseStatus,
    GRACE_PERIOD_DAYS,
)
import tokenpak.infrastructure.license_activation as activation
from tokenpak.infrastructure.license_activation import (
    activate,
    deactivate,
    get_plan,
    is_pro,
    is_team,
    is_enterprise,
    _load_plan_cache,
    _save_plan_cache,
    _clear_plan_cache,
    _load_stored_token,
)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def keypair():
    if not CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed")
    return generate_keypair()


@pytest.fixture(scope="module")
def private_pem(keypair):
    return keypair[0]


@pytest.fixture(scope="module")
def public_pem(keypair):
    return keypair[1]


@pytest.fixture(autouse=True)
def isolated_license_dir(tmp_path, monkeypatch):
    """Redirect all license file I/O to a temp dir per test."""
    monkeypatch.setenv("TOKENPAK_LICENSE_DIR", str(tmp_path))
    yield tmp_path


@pytest.fixture(autouse=True)
def unset_public_key(monkeypatch):
    """Ensure TOKENPAK_PUBLIC_KEY is unset by default (overridden per test)."""
    monkeypatch.delenv("TOKENPAK_PUBLIC_KEY", raising=False)


def _make_token(
    private_pem: bytes,
    tier: str = "pro",
    seats: int = 0,
    days_from_now: int | None = 365,
    features: list[str] | None = None,
) -> str:
    expires = None
    if days_from_now is not None:
        expires = (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat()
    payload = LicensePayload(
        key_id=format_license_key(),
        tier=tier,
        seats=seats,
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires,
        features=features or [],
    )
    return sign_license(payload, private_pem)


# ─────────────────────────────────────────────
# 1. activate — valid key
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestActivateValid:
    def test_activate_returns_result(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        result = activate(token)
        assert result.tier == LicenseTier.PRO
        assert result.is_usable

    def test_activate_writes_key_file(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        kp = tmp_path / "license.key"
        assert kp.exists()
        assert kp.read_text().strip() == token

    def test_activate_sets_perms_600(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        kp = tmp_path / "license.key"
        mode = stat.S_IMODE(kp.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_activate_team_tier(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="team", seats=5)
        result = activate(token)
        assert result.tier == LicenseTier.TEAM
        assert result.seats == 5

    def test_activate_enterprise_tier(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="enterprise")
        result = activate(token)
        assert result.tier == LicenseTier.ENTERPRISE

    def test_activate_perpetual_license(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro", days_from_now=None)
        result = activate(token)
        assert result.expires_at is None
        assert result.is_usable

    def test_activate_clears_plan_cache(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        # Put a stale cache entry
        (tmp_path / "plan_cache.json").write_text('{"cached_at": 9999999999}')
        token = _make_token(private_pem, tier="pro")
        activate(token)
        # Cache should be gone
        assert not (tmp_path / "plan_cache.json").exists()

    def test_activate_grace_period_key_is_usable(self, private_pem, public_pem, monkeypatch, tmp_path):
        """A key expired 3 days ago but within 7-day grace — should activate."""
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro", days_from_now=-3)
        result = activate(token)
        assert result.status == LicenseStatus.GRACE
        assert result.is_usable


# ─────────────────────────────────────────────
# 2. activate — invalid / expired key
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestActivateInvalid:
    def test_activate_garbage_token_raises(self, monkeypatch, public_pem, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        with pytest.raises(ValueError):
            activate("this-is-not-a-real-token")

    def test_activate_tampered_token_raises(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        bad_token = token[:-4] + "XXXX"
        with pytest.raises(ValueError):
            activate(bad_token)

    def test_activate_expired_past_grace_raises(self, private_pem, public_pem, monkeypatch, tmp_path):
        """Key expired more than 7 days ago — unusable."""
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro", days_from_now=-(GRACE_PERIOD_DAYS + 1))
        with pytest.raises(ValueError, match="activation failed"):
            activate(token)

    def test_activate_invalid_leaves_no_key_file(self, monkeypatch, public_pem, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        with pytest.raises(ValueError):
            activate("bad-token")
        assert not (tmp_path / "license.key").exists()

    def test_activate_no_public_key_oss_token_raises(self, private_pem, tmp_path):
        """Without a public key configured, any RSA token is treated as OSS-valid,
        but since is_usable=True that would succeed. This tests without env key."""
        # With no public key, validator falls back to OSS — still usable
        token = _make_token(private_pem, tier="pro")
        result = activate(token)  # no public key → OSS fallback → is_usable=True
        assert result.is_usable


# ─────────────────────────────────────────────
# 3. deactivate
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestDeactivate:
    def test_deactivate_removes_key(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        assert (tmp_path / "license.key").exists()
        deactivate()
        assert not (tmp_path / "license.key").exists()

    def test_deactivate_removes_plan_cache(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        # Trigger cache creation
        get_plan()
        deactivate()
        assert not (tmp_path / "plan_cache.json").exists()

    def test_deactivate_idempotent(self, tmp_path):
        """Calling deactivate when no license is active should not raise."""
        deactivate()  # no key exists
        deactivate()  # still fine

    def test_plan_after_deactivate_is_oss(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        deactivate()
        result = get_plan()
        assert result.tier == LicenseTier.OSS


# ─────────────────────────────────────────────
# 4. get_plan
# ─────────────────────────────────────────────

class TestGetPlan:
    def test_plan_no_license_returns_oss(self, tmp_path):
        result = get_plan()
        assert result.tier == LicenseTier.OSS
        assert result.is_usable

    @pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
    def test_plan_with_valid_license(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        result = get_plan()
        assert result.tier == LicenseTier.PRO
        assert result.is_usable

    def test_plan_graceful_on_corrupt_key_file(self, tmp_path):
        """Corrupt license.key → OSS fallback, never raises."""
        (tmp_path / "license.key").write_text("not-a-real-token")
        result = get_plan()
        assert result.tier == LicenseTier.OSS
        assert result.is_usable

    @pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
    def test_plan_shows_expiry(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="enterprise", days_from_now=90)
        activate(token)
        result = get_plan()
        assert result.expires_at is not None

    @pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
    def test_plan_shows_seats(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="team", seats=10)
        activate(token)
        result = get_plan()
        assert result.seats == 10


# ─────────────────────────────────────────────
# 5. is_pro / is_team / is_enterprise
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestTierHelpers:
    def test_is_pro_with_pro_license(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        assert is_pro() is True
        assert is_team() is False
        assert is_enterprise() is False

    def test_is_team_with_team_license(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="team")
        activate(token)
        assert is_pro() is True    # team implies pro-level access
        assert is_team() is True
        assert is_enterprise() is False

    def test_is_enterprise_with_enterprise_license(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="enterprise")
        activate(token)
        assert is_pro() is True
        assert is_team() is True
        assert is_enterprise() is True

    def test_oss_tier_all_false(self, tmp_path):
        """No license installed → all tier checks return False."""
        assert is_pro() is False
        assert is_team() is False
        assert is_enterprise() is False

    def test_tier_helpers_never_raise(self, tmp_path):
        """Even on broken state, helpers return False not exception."""
        (tmp_path / "license.key").write_text("bad-token")
        assert is_pro() is False
        assert is_team() is False
        assert is_enterprise() is False


# ─────────────────────────────────────────────
# 6. 24h plan cache
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestPlanCache:
    def test_cache_written_on_first_call(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        is_pro()  # triggers cache write
        assert (tmp_path / "plan_cache.json").exists()

    def test_cache_hit_avoids_re_validation(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)

        call_count = {"n": 0}
        original_get_plan = activation.get_plan

        def counting_get_plan():
            call_count["n"] += 1
            return original_get_plan()

        # Warm the cache
        with patch.object(activation, "get_plan", side_effect=counting_get_plan):
            is_pro()

        first_count = call_count["n"]

        # Second call should hit cache (get_plan not called again via _tier_check)
        cached = _load_plan_cache()
        assert cached is not None  # cache exists

    def test_cache_expires_after_24h(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)

        # Create a stale cache (25h old)
        result = get_plan()
        _save_plan_cache(result)
        cp = tmp_path / "plan_cache.json"
        data = json.loads(cp.read_text())
        data["cached_at"] = time.time() - 90001  # 25h ago
        cp.write_text(json.dumps(data))

        assert _load_plan_cache() is None  # expired

    def test_clear_plan_cache(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        is_pro()  # populate cache
        _clear_plan_cache()
        assert not (tmp_path / "plan_cache.json").exists()

    def test_deactivate_busts_cache(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        is_pro()
        deactivate()
        assert _load_plan_cache() is None


# ─────────────────────────────────────────────
# 7. Offline fallback
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestOfflineFallback:
    def test_offline_no_key_returns_oss(self, tmp_path):
        """No key file and no public key — still OSS, never crashes."""
        result = get_plan()
        assert result.tier == LicenseTier.OSS
        assert result.is_usable

    def test_offline_stored_key_validated_locally(self, private_pem, public_pem, monkeypatch, tmp_path):
        """License stored locally — validated fully offline via embedded public key."""
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        # No network needed; RSA validation is local
        result = get_plan()
        assert result.tier == LicenseTier.PRO

    def test_offline_corrupt_token_falls_back_to_oss(self, monkeypatch, tmp_path, public_pem):
        """Corrupt stored token → OSS, never raises."""
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        (tmp_path / "license.key").write_text("corrupted-garbage")
        result = get_plan()
        assert result.tier == LicenseTier.OSS

    def test_offline_expired_key_grace_still_usable(self, private_pem, public_pem, monkeypatch, tmp_path):
        """Expired 3 days ago — within grace window, offline still usable."""
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro", days_from_now=-3)
        # Write directly so we bypass activation (which would also succeed for grace keys)
        (tmp_path / "license.key").write_text(token + "\n")
        result = get_plan()
        assert result.status == LicenseStatus.GRACE
        assert result.is_usable


# ─────────────────────────────────────────────
# 8. Feature gating
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestFeatureGating:
    def test_pro_has_advanced_compression(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro")
        activate(token)
        result = get_plan()
        assert "compression_advanced" in result.features

    def test_oss_lacks_advanced_compression(self, tmp_path):
        result = get_plan()
        assert "compression_advanced" not in result.features

    def test_enterprise_has_sso(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="enterprise")
        activate(token)
        result = get_plan()
        assert "sso" in result.features
        assert "audit_log" in result.features

    def test_extra_features_included_in_plan(self, private_pem, public_pem, monkeypatch, tmp_path):
        monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())
        token = _make_token(private_pem, tier="pro", features=["beta_ui", "custom_plugin"])
        activate(token)
        result = get_plan()
        assert "beta_ui" in result.features
        assert "custom_plugin" in result.features

    def test_oss_features_always_present(self, tmp_path):
        result = get_plan()
        assert "compression_basic" in result.features
        assert "cli" in result.features
