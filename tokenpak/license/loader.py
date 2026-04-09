"""
tokenpak.license.loader — Load and validate the local license on proxy startup.

License file location (checked in order):
  1. $TOKENPAK_TEST_LICENSE  (path to JSON fixture — for CI/test use only)
  2. $TOKENPAK_LICENSE_DIR/license.json  (or ~/.config/tokenpak/license.json)

JSON format:
  {"token": "<b64url_payload>.<b64url_signature>"}

On ANY error (missing file, corrupt JSON, bad signature, expired) the loader
logs loudly and falls back to OSS tier.  The proxy NEVER fails-closed due to
a license problem.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from tokenpak.license.tier import LicenseTier, TIER_FEATURES

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Process-global active tier — set once at startup
# ─────────────────────────────────────────────

_active_tier: LicenseTier = LicenseTier.OSS
_active_expires_at: Optional[str] = None
_active_features: list[str] = list(TIER_FEATURES[LicenseTier.OSS])


def get_active_tier() -> LicenseTier:
    """Return the currently loaded license tier (default: OSS)."""
    return _active_tier


def get_active_features() -> list[str]:
    """Return the feature list for the active tier."""
    return _active_features


def get_active_expires_at() -> Optional[str]:
    """Return the expiry date string for the active license, or None if perpetual/OSS."""
    return _active_expires_at


# ─────────────────────────────────────────────
# License file path resolution
# ─────────────────────────────────────────────

_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "tokenpak"
_LICENSE_FILE_NAME = "license.json"


def _get_license_path() -> Optional[Path]:
    """Return path to license JSON, or None if not configured."""
    # 1. Test fixture override (CI / pytest)
    test_env = os.environ.get("TOKENPAK_TEST_LICENSE", "").strip()
    if test_env:
        p = Path(test_env)
        if p.exists():
            return p
        logger.warning("TOKENPAK_TEST_LICENSE points to non-existent file: %s", test_env)
        return None

    # 2. Config dir (env override or default)
    config_dir = Path(os.environ.get("TOKENPAK_LICENSE_DIR", str(_DEFAULT_CONFIG_DIR)))
    license_file = config_dir / _LICENSE_FILE_NAME
    if license_file.exists():
        return license_file

    return None


# ─────────────────────────────────────────────
# Main loader
# ─────────────────────────────────────────────


def load_license() -> LicenseTier:
    """
    Load and validate the license file.  Call once at proxy startup.

    Returns the active LicenseTier (OSS on any error — never raises).
    Sets the process-global _active_tier.
    """
    global _active_tier, _active_expires_at, _active_features

    license_path = _get_license_path()
    if license_path is None:
        logger.info("license: no license file found — running OSS tier")
        _active_tier = LicenseTier.OSS
        _active_expires_at = None
        _active_features = list(TIER_FEATURES[LicenseTier.OSS])
        return _active_tier

    # Read and parse JSON
    try:
        raw = license_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        token: str = data["token"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        logger.warning(
            "license: failed to read license file %s (%s) — falling back to OSS",
            license_path, exc,
        )
        return _active_tier  # OSS (default)

    # Validate signature and expiry
    try:
        from tokenpak.infrastructure.license_validation import LicenseValidator

        validator = LicenseValidator()
        result = validator.validate(token)
    except Exception as exc:
        logger.warning(
            "license: validation error (%s) — falling back to OSS", exc
        )
        return _active_tier

    if not result.is_usable:
        logger.warning(
            "license: not usable (status=%s, msg=%s) — falling back to OSS",
            result.status.value if hasattr(result.status, "value") else result.status,
            result.message,
        )
        return _active_tier

    # Set globals
    tier_val = result.tier.value if hasattr(result.tier, "value") else str(result.tier)
    _active_tier = LicenseTier.from_str(tier_val)
    _active_expires_at = result.expires_at
    _active_features = list(TIER_FEATURES[_active_tier])

    logger.info(
        "license: loaded tier=%s expires=%s features=%d",
        _active_tier.name,
        _active_expires_at or "perpetual",
        len(_active_features),
    )
    return _active_tier


def reset_for_testing(tier: LicenseTier = LicenseTier.OSS) -> None:
    """Reset process-global state. Used in tests only."""
    global _active_tier, _active_expires_at, _active_features
    _active_tier = tier
    _active_expires_at = None
    _active_features = list(TIER_FEATURES[tier])
