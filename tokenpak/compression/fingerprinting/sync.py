"""
TokenPak Fingerprint Sync Client — send fingerprints, receive directives.

Features:
- Syncs fingerprint to the intelligence server
- Caches directives locally with configurable TTL (default 1h)
- Requires Pro+ license
- Offline fallback: cached directives → OSS recipes
- Dry-run mode to preview what would be sent
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .generator import Fingerprint
from .privacy import PrivacyLevel, apply_privacy

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Constants / defaults
# ─────────────────────────────────────────────

_DEFAULT_CACHE_DIR = Path.home() / ".tokenpak" / "fingerprint_cache"
_DEFAULT_TTL = 3600  # 1 hour
_DEFAULT_SERVER = "https://intelligence.tokenpak.ai"
_SYNC_ENDPOINT = "/v1/fingerprint/sync"
_REQUEST_TIMEOUT = 10  # seconds


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────


@dataclass
class Directive:
    """A recipe/strategy directive received from the intelligence server."""

    directive_id: str
    action: str  # e.g. "compress", "route", "summarize"
    params: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Directive":
        return cls(
            directive_id=d.get("directive_id", ""),
            action=d.get("action", "noop"),
            params=d.get("params", {}),
            priority=d.get("priority", 0),
            description=d.get("description", ""),
        )

    def to_dict(self) -> dict:
        return {
            "directive_id": self.directive_id,
            "action": self.action,
            "params": self.params,
            "priority": self.priority,
            "description": self.description,
        }


@dataclass
class SyncResult:
    """Result of a fingerprint sync operation."""

    success: bool
    source: str  # "server" | "cache" | "oss_fallback"
    directives: list[Directive] = field(default_factory=list)
    cached_at: Optional[float] = None
    expires_at: Optional[float] = None
    error: Optional[str] = None
    dry_run: bool = False

    @property
    def from_cache(self) -> bool:
        return self.source == "cache"

    @property
    def is_fallback(self) -> bool:
        return self.source == "oss_fallback"


# ─────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────


def _cache_path(fingerprint_id: str, cache_dir: Path) -> Path:
    return cache_dir / f"{fingerprint_id}.json"


def _write_cache(
    fingerprint_id: str, directives: list[Directive], ttl: int, cache_dir: Path
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    payload = {
        "fingerprint_id": fingerprint_id,
        "cached_at": now,
        "expires_at": now + ttl,
        "directives": [d.to_dict() for d in directives],
    }
    path = _cache_path(fingerprint_id, cache_dir)
    path.write_text(json.dumps(payload, indent=2))
    logger.debug("Wrote directive cache: %s", path)


def _read_cache(fingerprint_id: str, cache_dir: Path) -> Optional[dict]:
    path = _cache_path(fingerprint_id, cache_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() > data.get("expires_at", 0):
            logger.debug("Cache expired for %s", fingerprint_id)
            return None
        return data
    except Exception as exc:
        logger.warning("Failed to read cache %s: %s", path, exc)
        return None


def _oss_fallback_directives() -> list[Directive]:
    """Minimal OSS recipe set used when offline and no cache is available."""
    return [
        Directive(
            directive_id="oss-basic-compress",
            action="compress",
            params={"strategy": "basic", "ratio": 0.8},
            priority=0,
            description="Basic OSS compression recipe (offline fallback)",
        )
    ]


# ─────────────────────────────────────────────
# Sync client
# ─────────────────────────────────────────────


class FingerprintSync:
    """
    Syncs fingerprints to the intelligence server and caches returned directives.

    Requires a Pro+ license. Falls back to cached or OSS directives when offline.

    Usage:
        sync = FingerprintSync()
        result = sync.sync(fingerprint)
        result = sync.sync(fingerprint, dry_run=True)
        directives = sync.cached_directives(fingerprint_id)
        sync.clear_cache()
    """

    def __init__(
        self,
        server_url: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        ttl: int = _DEFAULT_TTL,
        privacy_level: PrivacyLevel = PrivacyLevel.STANDARD,
        timeout: int = _REQUEST_TIMEOUT,
    ):
        self.server_url = (
            server_url
            or os.environ.get("TOKENPAK_INTELLIGENCE_URL", _DEFAULT_SERVER)
            or _DEFAULT_SERVER
        ).rstrip("/")
        self.cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self.ttl = ttl
        self.privacy_level = privacy_level
        self.timeout = timeout

    # ── License gate ──────────────────────────────────────────────────────

    def _assert_pro(self) -> None:
        """Raise if not Pro+. Import guard so OSS installs skip cleanly."""
        try:
            from tokenpak.agent.license.activation import is_pro

            if not is_pro():
                raise PermissionError(
                    "Fingerprint sync requires a Pro+ license. "
                    "Run `tokenpak activate <key>` to unlock."
                )
        except ImportError:
            # License module not available — allow (dev/test scenario)
            logger.warning("License module unavailable; skipping Pro gate.")

    # ── Public API ────────────────────────────────────────────────────────

    def sync(
        self,
        fingerprint: Fingerprint,
        dry_run: bool = False,
        skip_cache: bool = False,
    ) -> SyncResult:
        """
        Sync fingerprint to intelligence server and return directives.

        Args:
            fingerprint:  The Fingerprint to sync.
            dry_run:      If True, show what would be sent but don't transmit.
            skip_cache:   If True, bypass local cache and always contact server.

        Returns:
            SyncResult with directives from server, cache, or OSS fallback.
        """
        self._assert_pro()

        # Dry run — preview payload only
        if dry_run:
            payload = apply_privacy(fingerprint.to_dict(), self.privacy_level)
            return SyncResult(
                success=True,
                source="dry_run",
                directives=[],
                dry_run=True,
                error=None,
            )

        # Cache hit
        if not skip_cache:
            cached = _read_cache(fingerprint.fingerprint_id, self.cache_dir)
            if cached:
                directives = [Directive.from_dict(d) for d in cached.get("directives", [])]
                return SyncResult(
                    success=True,
                    source="cache",
                    directives=directives,
                    cached_at=cached.get("cached_at"),
                    expires_at=cached.get("expires_at"),
                )

        # Network sync
        try:
            payload = apply_privacy(fingerprint.to_dict(), self.privacy_level)
            directives = self._post_fingerprint(payload)
            _write_cache(fingerprint.fingerprint_id, directives, self.ttl, self.cache_dir)
            now = time.time()
            return SyncResult(
                success=True,
                source="server",
                directives=directives,
                cached_at=now,
                expires_at=now + self.ttl,
            )
        except Exception as exc:
            logger.warning("Fingerprint sync failed: %s — using fallback", exc)
            # Stale cache fallback
            stale = self._stale_cache(fingerprint.fingerprint_id)
            if stale:
                directives = [Directive.from_dict(d) for d in stale.get("directives", [])]
                return SyncResult(
                    success=False,
                    source="cache",
                    directives=directives,
                    cached_at=stale.get("cached_at"),
                    error=str(exc),
                )
            # OSS recipe fallback
            return SyncResult(
                success=False,
                source="oss_fallback",
                directives=_oss_fallback_directives(),
                error=str(exc),
            )

    def cached_directives(self, fingerprint_id: str) -> list[Directive]:
        """Return cached directives for a fingerprint_id, or [] if missing/expired."""
        cached = _read_cache(fingerprint_id, self.cache_dir)
        if not cached:
            return []
        return [Directive.from_dict(d) for d in cached.get("directives", [])]

    def clear_cache(self, fingerprint_id: Optional[str] = None) -> int:
        """
        Clear cached directives.

        Args:
            fingerprint_id: If given, clear only that entry. Else clear all.

        Returns:
            Number of cache files deleted.
        """
        if not self.cache_dir.exists():
            return 0
        if fingerprint_id:
            path = _cache_path(fingerprint_id, self.cache_dir)
            if path.exists():
                path.unlink()
                return 1
            return 0
        count = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
            count += 1
        return count

    def cache_status(self) -> dict[str, Any]:
        """Return a summary of the local directive cache."""
        if not self.cache_dir.exists():
            return {
                "entries": 0,
                "valid": 0,
                "expired": 0,
                "cache_dir": str(self.cache_dir),
                "ttl_seconds": self.ttl,
            }
        entries = list(self.cache_dir.glob("*.json"))
        now = time.time()
        valid = 0
        expired = 0
        for e in entries:
            try:
                data = json.loads(e.read_text())
                if now <= data.get("expires_at", 0):
                    valid += 1
                else:
                    expired += 1
            except Exception:
                expired += 1
        return {
            "entries": len(entries),
            "valid": valid,
            "expired": expired,
            "cache_dir": str(self.cache_dir),
            "ttl_seconds": self.ttl,
        }

    # ── Internal ──────────────────────────────────────────────────────────

    def _post_fingerprint(self, payload: dict) -> list[Directive]:
        """POST payload to intelligence server; return parsed directives."""
        url = f"{self.server_url}{_SYNC_ENDPOINT}"
        body = json.dumps(payload).encode()

        # Attach API key if available
        api_key = os.environ.get("TOKENPAK_API_KEY", "")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode())

        raw_directives = data.get("directives", [])
        return [Directive.from_dict(d) for d in raw_directives]

    def _stale_cache(self, fingerprint_id: str) -> Optional[dict]:
        """Read cache ignoring TTL (for offline fallback)."""
        path = _cache_path(fingerprint_id, self.cache_dir)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
