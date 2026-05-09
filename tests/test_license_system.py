"""
Tests for TokenPak License System — keys, validator, store, endpoint.

Run:  pytest tests/test_license_system.py -v
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.license.keys", reason="module not available in current build")
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

# ─────────────────────────────────────────────
# Try importing cryptography — skip key tests if not available
# ─────────────────────────────────────────────

try:
    from cryptography.hazmat.primitives import serialization
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from tokenpak._internal.license.keys import (
    LicensePayload,
    format_license_key,
    generate_keypair,
    sign_license,
    verify_license,
)
from tokenpak.infrastructure.license_store import LicenseStore
from tokenpak.infrastructure.license_validation import (
    GRACE_PERIOD_DAYS,
    LicenseStatus,
    LicenseTier,
    LicenseValidator,
    SeatRegistry,
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


def _make_payload(
    tier: str = "pro",
    seats: int = 0,
    days_from_now: int | None = 365,
    features: list[str] | None = None,
) -> LicensePayload:
    expires = None
    if days_from_now is not None:
        expires = (
            datetime.now(timezone.utc) + timedelta(days=days_from_now)
        ).isoformat()
    return LicensePayload(
        key_id=format_license_key(),
        tier=tier,
        seats=seats,
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires,
        features=features or [],
    )


# ─────────────────────────────────────────────
# 1. Key format
# ─────────────────────────────────────────────

class TestKeyFormat:
    def test_format_matches_pattern(self):
        key = format_license_key()
        parts = key.split("-")
        assert parts[0] == "TPAK"
        assert len(parts) == 4
        for segment in parts[1:]:
            assert len(segment) == 4
            assert segment.isalnum()

    def test_unique_per_call(self):
        keys = {format_license_key() for _ in range(100)}
        assert len(keys) == 100  # all unique


# ─────────────────────────────────────────────
# 2. RSA signing & verification
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestRSACrypto:
    def test_sign_and_verify(self, private_pem, public_pem):
        payload = _make_payload("pro")
        token = sign_license(payload, private_pem)
        result = verify_license(token, public_pem)
        assert result.tier == "pro"
        assert result.key_id == payload.key_id

    def test_tampered_signature_rejected(self, private_pem, public_pem):
        payload = _make_payload("enterprise")
        token = sign_license(payload, private_pem)
        # flip a mid-signature char (not the last — base64 padding makes last char's
        # lower 4 bits irrelevant when there are 2 padding '=' bytes)
        parts = token.split(".")
        mid = len(parts[1]) // 2
        flipped = "A" if parts[1][mid] != "A" else "B"
        bad_sig = parts[1][:mid] + flipped + parts[1][mid + 1:]
        bad_token = parts[0] + "." + bad_sig
        with pytest.raises(ValueError, match="signature invalid"):
            verify_license(bad_token, public_pem)

    def test_tampered_payload_rejected(self, private_pem, public_pem):
        import base64
        payload = _make_payload("pro")
        token = sign_license(payload, private_pem)
        # Modify payload to claim enterprise
        parts = token.split(".")
        raw = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        raw["tier"] = "enterprise"
        bad_payload = base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()
        bad_token = bad_payload + "." + parts[1]
        with pytest.raises(ValueError):
            verify_license(bad_token, public_pem)

    def test_malformed_token_rejected(self, public_pem):
        with pytest.raises(ValueError, match="Malformed"):
            verify_license("not-a-real-token", public_pem)

    def test_perpetual_license(self, private_pem, public_pem):
        payload = _make_payload("oss", days_from_now=None)
        assert payload.expires_at is None
        token = sign_license(payload, private_pem)
        result = verify_license(token, public_pem)
        assert result.expires_at is None


# ─────────────────────────────────────────────
# 3. Tier validation
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestTierValidation:
    def _validator(self, public_pem):
        return LicenseValidator(public_pem=public_pem)

    def test_pro_features_present(self, private_pem, public_pem):
        payload = _make_payload("pro")
        token = sign_license(payload, private_pem)
        v = self._validator(public_pem)
        result = v.validate(token)
        assert result.status == LicenseStatus.VALID
        assert result.tier == LicenseTier.PRO
        assert "compression_advanced" in result.features
        assert "tokenpak_server" not in result.features

    def test_enterprise_has_all_features(self, private_pem, public_pem):
        payload = _make_payload("enterprise")
        token = sign_license(payload, private_pem)
        v = self._validator(public_pem)
        result = v.validate(token)
        assert "sso" in result.features
        assert "self_hosted_intelligence" in result.features

    def test_oss_limited_features(self, private_pem, public_pem):
        payload = _make_payload("oss")
        token = sign_license(payload, private_pem)
        v = self._validator(public_pem)
        result = v.validate(token)
        assert "compression_basic" in result.features
        assert "compression_advanced" not in result.features

    def test_unknown_tier_returns_invalid(self, private_pem, public_pem):
        payload = _make_payload("ultra_mega_tier")
        token = sign_license(payload, private_pem)
        v = self._validator(public_pem)
        result = v.validate(token)
        assert result.status == LicenseStatus.INVALID

    def test_extra_features_merged(self, private_pem, public_pem):
        payload = _make_payload("pro", features=["custom_plugin"])
        token = sign_license(payload, private_pem)
        v = self._validator(public_pem)
        result = v.validate(token)
        assert "custom_plugin" in result.features


# ─────────────────────────────────────────────
# 4. Expiry + grace period
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestExpiryGrace:
    def _validator(self, public_pem):
        return LicenseValidator(public_pem=public_pem)

    def test_valid_not_expired(self, private_pem, public_pem):
        payload = _make_payload("pro", days_from_now=30)
        token = sign_license(payload, private_pem)
        result = self._validator(public_pem).validate(token)
        assert result.status == LicenseStatus.VALID

    def test_expired_past_grace(self, private_pem, public_pem):
        # Expired 8 days ago, grace is 7 days
        payload = _make_payload("pro", days_from_now=-(GRACE_PERIOD_DAYS + 1))
        token = sign_license(payload, private_pem)
        result = self._validator(public_pem).validate(token)
        assert result.status == LicenseStatus.EXPIRED

    def test_grace_period_active(self, private_pem, public_pem):
        # Expired 3 days ago — within 7-day grace window
        payload = _make_payload("pro", days_from_now=-3)
        token = sign_license(payload, private_pem)
        result = self._validator(public_pem).validate(token)
        assert result.status == LicenseStatus.GRACE
        assert result.is_usable

    def test_perpetual_license_never_expires(self, private_pem, public_pem):
        payload = _make_payload("pro", days_from_now=None)
        token = sign_license(payload, private_pem)
        result = self._validator(public_pem).validate(token)
        assert result.status == LicenseStatus.VALID
        assert result.expires_at is None


# ─────────────────────────────────────────────
# 5. Seat counting (Team tier)
# ─────────────────────────────────────────────

@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography not installed")
class TestSeatCounting:
    def test_within_seat_limit(self, private_pem, public_pem):
        payload = _make_payload("team", seats=5)
        token = sign_license(payload, private_pem)
        seat_reg = SeatRegistry()
        v = LicenseValidator(public_pem=public_pem, seat_registry=seat_reg)
        # Claim 3 of 5 seats
        for i in range(3):
            result = v.validate(token, agent_id=f"agent-{i}")
        assert result.status == LicenseStatus.VALID
        assert result.seats_used == 3
        assert result.seats == 5

    def test_seat_limit_exceeded(self, private_pem, public_pem):
        payload = _make_payload("team", seats=2)
        token = sign_license(payload, private_pem)
        seat_reg = SeatRegistry()
        v = LicenseValidator(public_pem=public_pem, seat_registry=seat_reg)
        for i in range(3):
            result = v.validate(token, agent_id=f"agent-{i}")
        assert result.status == LicenseStatus.SEAT_LIMIT

    def test_unlimited_seats_zero(self, private_pem, public_pem):
        # seats=0 means unlimited — should never trigger SEAT_LIMIT
        payload = _make_payload("team", seats=0)
        token = sign_license(payload, private_pem)
        v = LicenseValidator(public_pem=public_pem)
        result = v.validate(token, agent_id="agent-x")
        assert result.status == LicenseStatus.VALID

    def test_seat_registry_ttl(self):
        reg = SeatRegistry(_ttl_seconds=1)
        reg.claim("a1")
        assert reg.active_count == 1
        time.sleep(1.1)
        assert reg.active_count == 0

    def test_seat_release(self):
        reg = SeatRegistry()
        reg.claim("a1")
        reg.claim("a2")
        assert reg.active_count == 2
        reg.release("a1")
        assert reg.active_count == 1


# ─────────────────────────────────────────────
# 6. No-key fallback (OSS)
# ─────────────────────────────────────────────

class TestNoKeyFallback:
    def test_no_public_key_defaults_oss(self):
        v = LicenseValidator(public_pem=None)
        result = v.validate("any-token")
        assert result.status == LicenseStatus.VALID
        assert result.tier == LicenseTier.OSS
        assert result.is_usable


# ─────────────────────────────────────────────
# 7. License store
# ─────────────────────────────────────────────

class TestLicenseStore:
    def test_save_and_load(self, tmp_path):
        store = LicenseStore(store_dir=tmp_path)
        store.save(token="t123", tier="pro", expires_at="2027-01-01T00:00:00+00:00")
        cached = store.load()
        assert cached is not None
        assert cached.token == "t123"
        assert cached.tier == "pro"

    def test_within_grace_fresh(self, tmp_path):
        store = LicenseStore(store_dir=tmp_path)
        store.save(token="t", tier="pro")
        assert store.is_within_grace() is True

    def test_expired_grace(self, tmp_path):
        store = LicenseStore(store_dir=tmp_path)
        store.save(token="t", tier="pro")
        # Manually push last_validated back 8 days
        cached = store.load()
        cached.last_validated = time.time() - (GRACE_PERIOD_DAYS + 1) * 86400
        store._write(cached)
        assert store.is_within_grace() is False

    def test_clear(self, tmp_path):
        store = LicenseStore(store_dir=tmp_path)
        store.save(token="t", tier="oss")
        store.clear()
        assert store.load() is None

    def test_grace_status_format(self, tmp_path):
        store = LicenseStore(store_dir=tmp_path)
        store.save(token="t", tier="team", expires_at="2027-01-01T00:00:00+00:00")
        status = store.grace_status()
        assert status["has_cache"] is True
        assert status["within_grace"] is True
        assert "grace_expires_at" in status

    def test_no_cache_returns_no_cache(self, tmp_path):
        store = LicenseStore(store_dir=tmp_path)
        status = store.grace_status()
        assert status["has_cache"] is False
