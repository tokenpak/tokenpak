"""
TokenPak License Store — local persistence with offline grace period support.

Stores the last-known-good license in XDG config dir so validation can
continue for up to GRACE_PERIOD_DAYS (7) without server connectivity.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

GRACE_PERIOD_DAYS = 7
STORE_FILE_NAME = "license_cache.json"


@dataclass
class CachedLicense:
    token: str
    cached_at: float  # unix timestamp
    last_validated: float  # unix timestamp of last successful online validation
    tier: str
    expires_at: Optional[str]

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "cached_at": self.cached_at,
            "last_validated": self.last_validated,
            "tier": self.tier,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CachedLicense":
        return cls(
            token=d["token"],
            cached_at=float(d.get("cached_at", 0)),
            last_validated=float(d.get("last_validated", 0)),
            tier=d.get("tier", "oss"),
            expires_at=d.get("expires_at"),
        )

    @property
    def within_grace_period(self) -> bool:
        """True if last online validation was within GRACE_PERIOD_DAYS."""
        grace_cutoff = time.time() - (GRACE_PERIOD_DAYS * 86400)
        return self.last_validated >= grace_cutoff

    @property
    def grace_expires_at(self) -> datetime:
        """Absolute datetime when offline grace expires."""
        return datetime.fromtimestamp(
            self.last_validated + GRACE_PERIOD_DAYS * 86400,
            tz=timezone.utc,
        )


class LicenseStore:
    """
    Persist and retrieve cached license data.

    Default storage: ~/.config/tokenpak/license_cache.json
    Override via TOKENPAK_CONFIG_DIR env var or store_dir argument.
    """

    def __init__(self, store_dir: Optional[Path] = None):
        import os

        if store_dir:
            self._dir = Path(store_dir)
        else:
            config_base = os.environ.get(
                "TOKENPAK_CONFIG_DIR",
                Path.home() / ".config" / "tokenpak",
            )
            self._dir = Path(config_base)

        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / STORE_FILE_NAME

    # ──────────────────────────────────────────
    # Read / write
    # ──────────────────────────────────────────

    def save(self, token: str, tier: str, expires_at: Optional[str] = None) -> CachedLicense:
        """Persist a successfully validated license."""
        now = time.time()
        cached = CachedLicense(
            token=token,
            cached_at=now,
            last_validated=now,
            tier=tier,
            expires_at=expires_at,
        )
        self._write(cached)
        logger.debug("License cached: tier=%s expires=%s", tier, expires_at)
        return cached

    def touch(self) -> None:
        """Update last_validated timestamp (called after each successful online check)."""
        cached = self.load()
        if cached:
            cached.last_validated = time.time()
            self._write(cached)

    def load(self) -> Optional[CachedLicense]:
        """Load cached license, or None if not present / corrupt."""
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
            return CachedLicense.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Could not load license cache: %s", exc)
            return None

    def clear(self) -> None:
        """Remove cached license (e.g., on explicit deactivation)."""
        if self._path.exists():
            self._path.unlink()
            logger.debug("License cache cleared")

    # ──────────────────────────────────────────
    # Grace period helpers
    # ──────────────────────────────────────────

    def is_within_grace(self) -> bool:
        cached = self.load()
        return cached is not None and cached.within_grace_period

    def grace_status(self) -> dict:
        cached = self.load()
        if not cached:
            return {"has_cache": False}
        return {
            "has_cache": True,
            "tier": cached.tier,
            "within_grace": cached.within_grace_period,
            "grace_expires_at": cached.grace_expires_at.isoformat(),
            "last_validated": datetime.fromtimestamp(
                cached.last_validated, tz=timezone.utc
            ).isoformat(),
        }

    # ──────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────

    def _write(self, cached: CachedLicense) -> None:
        self._path.write_text(json.dumps(cached.to_dict(), indent=2))
