"""
TokenPak License Validator — tier validation, seat counting, grace period.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from .keys import verify_license

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────


class LicenseTier(str, Enum):
    OSS = "oss"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class LicenseStatus(str, Enum):
    VALID = "valid"
    EXPIRED = "expired"
    INVALID = "invalid"  # bad signature / malformed
    GRACE = "grace"  # past expiry but within grace window
    OFFLINE = "offline"  # can't reach server, using cached
    SEAT_LIMIT = "seat_limit"  # team seats exhausted


# ─────────────────────────────────────────────
# Feature catalogue per tier
# ─────────────────────────────────────────────

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
        "tokenpak_server",
        "seat_management",
        "team_analytics",
        "self_hosted_intelligence",
        "sso",
        "audit_log",
        "sla",
    ],
}

GRACE_PERIOD_DAYS = 7

# ─────────────────────────────────────────────
# Validation result
# ─────────────────────────────────────────────


@dataclass
class ValidationResult:
    status: LicenseStatus
    tier: LicenseTier
    features: list[str]
    seats: int  # 0 = unlimited
    seats_used: int
    expires_at: Optional[str]
    grace_expires_at: Optional[str]
    message: str

    @property
    def is_usable(self) -> bool:
        return self.status in (LicenseStatus.VALID, LicenseStatus.GRACE, LicenseStatus.OFFLINE)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "tier": self.tier.value,
            "features": self.features,
            "seats": self.seats,
            "seats_used": self.seats_used,
            "expires_at": self.expires_at,
            "grace_expires_at": self.grace_expires_at,
            "message": self.message,
        }


# ─────────────────────────────────────────────
# Seat counter (in-memory + optional persisted)
# ─────────────────────────────────────────────


@dataclass
class SeatRegistry:
    """Track active seat claims for Team tier."""

    _seats: dict[str, float] = field(default_factory=dict)  # agent_id → last_seen timestamp
    _ttl_seconds: int = 3600  # seat lease expires after 1h of inactivity

    def claim(self, agent_id: str) -> None:
        self._seats[agent_id] = time.time()

    def release(self, agent_id: str) -> None:
        self._seats.pop(agent_id, None)

    @property
    def active_count(self) -> int:
        now = time.time()
        return sum(1 for ts in self._seats.values() if now - ts < self._ttl_seconds)

    def active_ids(self) -> list[str]:
        now = time.time()
        return [aid for aid, ts in self._seats.items() if now - ts < self._ttl_seconds]


# ─────────────────────────────────────────────
# Main validator
# ─────────────────────────────────────────────


class LicenseValidator:
    """
    Validates TokenPak license tokens.

    Usage:
        validator = LicenseValidator(public_pem=PUBLIC_KEY_BYTES)
        result = validator.validate(token)
        if result.is_usable:
            ...
    """

    def __init__(
        self,
        public_pem: Optional[bytes] = None,
        public_pem_path: Optional[Path] = None,
        seat_registry: Optional[SeatRegistry] = None,
    ):
        if public_pem:
            self._public_pem = public_pem
        elif public_pem_path:
            self._public_pem = Path(public_pem_path).read_bytes()
        else:
            # Try env variable
            env_key = os.environ.get("TOKENPAK_PUBLIC_KEY", "")
            if env_key:
                self._public_pem = env_key.encode()
            else:
                self._public_pem = None  # type: ignore

        self._seat_registry = seat_registry or SeatRegistry()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def validate(self, token: str, agent_id: Optional[str] = None) -> ValidationResult:
        """
        Full license validation:
        1. Verify RSA signature
        2. Validate tier
        3. Check expiry + grace period
        4. Seat counting (Team tier)
        """
        # Step 1 — signature
        if not self._public_pem:
            return self._oss_fallback("No public key configured — defaulting to OSS")

        try:
            payload = verify_license(token, self._public_pem)
        except ValueError as exc:
            return ValidationResult(
                status=LicenseStatus.INVALID,
                tier=LicenseTier.OSS,
                features=TIER_FEATURES[LicenseTier.OSS],
                seats=0,
                seats_used=0,
                expires_at=None,
                grace_expires_at=None,
                message=f"Invalid license: {exc}",
            )

        # Step 2 — tier
        try:
            tier = LicenseTier(payload.tier)
        except ValueError:
            return ValidationResult(
                status=LicenseStatus.INVALID,
                tier=LicenseTier.OSS,
                features=TIER_FEATURES[LicenseTier.OSS],
                seats=0,
                seats_used=0,
                expires_at=None,
                grace_expires_at=None,
                message=f"Unknown tier: {payload.tier!r}",
            )

        # Step 3 — expiry + grace
        now = datetime.now(timezone.utc)
        status = LicenseStatus.VALID
        grace_expires_at = None

        if payload.expires_at:
            try:
                expiry = datetime.fromisoformat(payload.expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                grace_end = expiry + timedelta(days=GRACE_PERIOD_DAYS)
                grace_expires_at = grace_end.isoformat()
                if now > grace_end:
                    status = LicenseStatus.EXPIRED
                elif now > expiry:
                    status = LicenseStatus.GRACE
            except ValueError:
                logger.warning("Could not parse expires_at: %s", payload.expires_at)

        # Step 4 — seat counting (Team only)
        seats_used = 0
        if tier == LicenseTier.TEAM and payload.seats > 0:
            if agent_id:
                self._seat_registry.claim(agent_id)
            seats_used = self._seat_registry.active_count
            if seats_used > payload.seats:
                status = LicenseStatus.SEAT_LIMIT

        # Merge payload features with tier baseline
        tier_features = list(TIER_FEATURES[tier])
        for extra in payload.features:
            if extra not in tier_features:
                tier_features.append(extra)

        return ValidationResult(
            status=status,
            tier=tier,
            features=tier_features,
            seats=payload.seats,
            seats_used=seats_used,
            expires_at=payload.expires_at,
            grace_expires_at=grace_expires_at,
            message=f"License {status.value} — tier={tier.value}",
        )

    def has_feature(self, token: str, feature: str) -> bool:
        result = self.validate(token)
        return result.is_usable and feature in result.features

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────

    @staticmethod
    def _oss_fallback(message: str) -> ValidationResult:
        return ValidationResult(
            status=LicenseStatus.VALID,
            tier=LicenseTier.OSS,
            features=TIER_FEATURES[LicenseTier.OSS],
            seats=0,
            seats_used=0,
            expires_at=None,
            grace_expires_at=None,
            message=message,
        )
