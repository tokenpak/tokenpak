"""
TokenPak License Activation — activate/deactivate/plan helpers.

Storage: ~/.tokenpak/license.key  (mode 600)
Cache:   ~/.tokenpak/plan_cache.json  (24h TTL for is_pro/is_team/is_enterprise)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .validator import (
    LicenseStatus,
    LicenseTier,
    LicenseValidator,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────

_DEFAULT_DIR = Path.home() / ".tokenpak"
_LICENSE_KEY_NAME = "license.key"
_PLAN_CACHE_NAME = "plan_cache.json"
_PLAN_CACHE_TTL = 86400  # 24 hours in seconds


def _license_dir() -> Path:
    """Return (and create) the tokenpak license directory."""
    d = Path(os.environ.get("TOKENPAK_LICENSE_DIR", str(_DEFAULT_DIR)))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key_path() -> Path:
    return _license_dir() / _LICENSE_KEY_NAME


def _cache_path() -> Path:
    return _license_dir() / _PLAN_CACHE_NAME


# ─────────────────────────────────────────────
# Public key loader
# ─────────────────────────────────────────────


def _load_public_key() -> Optional[bytes]:
    """Load the RSA public key from env var or None."""
    env = os.environ.get("TOKENPAK_PUBLIC_KEY", "").strip()
    if env:
        return env.encode()
    return None


# ─────────────────────────────────────────────
# Activate
# ─────────────────────────────────────────────


def activate(token: str) -> ValidationResult:
    """
    Validate a license token and persist it at ~/.tokenpak/license.key (chmod 600).

    Returns a ValidationResult on success.
    Raises ValueError if the token is invalid, expired beyond grace, or otherwise unusable.
    Never silently swallows an unusable license — caller gets a clear error.
    """
    token = token.strip()
    public_pem = _load_public_key()
    validator = LicenseValidator(public_pem=public_pem)

    try:
        result = validator.validate(token)
    except Exception as exc:
        raise ValueError(f"License validation error: {exc}") from exc

    if not result.is_usable:
        raise ValueError(
            f"License activation failed: {result.message} " f"(status={result.status.value})"
        )

    # Write the token
    kp = _key_path()
    kp.write_text(token + "\n")
    kp.chmod(0o600)

    # Bust the plan cache so next is_pro() reflects the new key
    _clear_plan_cache()

    logger.info("License activated: tier=%s expires=%s", result.tier.value, result.expires_at)
    return result


# ─────────────────────────────────────────────
# Deactivate
# ─────────────────────────────────────────────


def deactivate() -> None:
    """
    Remove the stored license key and plan cache, reverting to OSS.
    Idempotent — safe to call when already deactivated.
    """
    kp = _key_path()
    if kp.exists():
        kp.unlink()
        logger.info("License key removed")

    _clear_plan_cache()
    logger.info("Reverted to OSS tier")


# ─────────────────────────────────────────────
# Plan
# ─────────────────────────────────────────────


def get_plan() -> ValidationResult:
    """
    Return the current license plan/status.
    Always returns a ValidationResult — falls back to OSS on any error.
    Never raises.
    """
    token = _load_stored_token()
    if not token:
        return LicenseValidator._oss_fallback("No license installed — OSS (free)")

    public_pem = _load_public_key()
    validator = LicenseValidator(public_pem=public_pem)
    try:
        return validator.validate(token)
    except Exception as exc:
        logger.warning("License check failed, falling back to OSS: %s", exc)
        return LicenseValidator._oss_fallback(f"License check failed — defaulting to OSS ({exc})")


# ─────────────────────────────────────────────
# Tier helpers — with 24h cache
# ─────────────────────────────────────────────


def is_pro() -> bool:
    """True if current license is Pro, Team, or Enterprise (24h cache). Safe — never raises."""
    return _tier_check(lambda t: t in (LicenseTier.PRO, LicenseTier.TEAM, LicenseTier.ENTERPRISE))


def is_team() -> bool:
    """True if current license is Team or Enterprise (24h cache). Safe — never raises."""
    return _tier_check(lambda t: t in (LicenseTier.TEAM, LicenseTier.ENTERPRISE))


def is_enterprise() -> bool:
    """True if current license is Enterprise (24h cache). Safe — never raises."""
    return _tier_check(lambda t: t == LicenseTier.ENTERPRISE)


def _tier_check(predicate: Callable[[LicenseTier], bool]) -> bool:
    """
    Run a tier predicate against the cached or live plan.
    Always returns False on any error (graceful degradation).
    """
    try:
        result = _load_plan_cache()
        if result is None:
            result = get_plan()
            _save_plan_cache(result)
        return result.is_usable and predicate(result.tier)
    except Exception as exc:
        logger.debug("Tier check failed, defaulting False: %s", exc)
        return False


# ─────────────────────────────────────────────
# Token I/O
# ─────────────────────────────────────────────


def _load_stored_token() -> Optional[str]:
    """Read persisted token or None."""
    kp = _key_path()
    if not kp.exists():
        return None
    try:
        return kp.read_text().strip()
    except Exception as exc:
        logger.warning("Could not read license.key: %s", exc)
        return None


# ─────────────────────────────────────────────
# Plan cache (24h)
# ─────────────────────────────────────────────


def _load_plan_cache() -> Optional[ValidationResult]:
    """Return cached ValidationResult if within 24h, else None."""
    cp = _cache_path()
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text())
        if time.time() - float(data.get("cached_at", 0)) >= _PLAN_CACHE_TTL:
            return None
        return ValidationResult(
            status=LicenseStatus(data["status"]),
            tier=LicenseTier(data["tier"]),
            features=data["features"],
            seats=int(data["seats"]),
            seats_used=int(data["seats_used"]),
            expires_at=data.get("expires_at"),
            grace_expires_at=data.get("grace_expires_at"),
            message=data["message"],
        )
    except Exception as exc:
        logger.debug("Could not load plan cache: %s", exc)
        return None


def _save_plan_cache(result: ValidationResult) -> None:
    """Persist a ValidationResult to the 24h plan cache."""
    data = result.to_dict()
    data["cached_at"] = time.time()
    try:
        _cache_path().write_text(json.dumps(data, indent=2))
    except Exception as exc:
        logger.debug("Could not save plan cache: %s", exc)


def _clear_plan_cache() -> None:
    cp = _cache_path()
    if cp.exists():
        try:
            cp.unlink()
        except Exception:
            pass
