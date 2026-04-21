"""
TokenPak CooldownManager — Auth cooldown tracking and background auto-clear.

Deliverable 3 of Phase 3 UX Overhaul.

Manages expired auth cooldowns from two sources:
  1. ~/.tokenpak/cooldowns.json — per-profile cooldown timestamps
  2. ~/.tokenpak/auth-profiles.json — profile-level cooldownUntil fields

Background task (BackgroundCooldownClearer) integrates into the proxy startup
and runs every 60 seconds automatically (configurable via auth.auto_clear_cooldowns).

Usage:
    from tokenpak.agent.auth.cooldown_manager import CooldownManager, BackgroundCooldownClearer

    # Standalone clear:
    mgr = CooldownManager()
    cleared = mgr.clear_expired()

    # Background task (in async context):
    clearer = BackgroundCooldownClearer(interval=60)
    await clearer.start()   # non-blocking
    # ...
    await clearer.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COOLDOWNS_FILE = Path.home() / ".tokenpak" / "cooldowns.json"
AUTH_PROFILES_FILE = Path.home() / ".tokenpak" / "auth-profiles.json"

# Don't clear if errorCount is high — likely a real, persistent problem
HIGH_ERROR_THRESHOLD = 10


class CooldownManager:
    """Load, inspect, and clear expired auth cooldowns from disk.

    Cooldown entry format (cooldowns.json):
    {
        "anthropic:default": {"cooldownUntil": 1709000000, "errorCount": 3},
        ...
    }
    Entry is cleared when: cooldownUntil < now AND errorCount < HIGH_ERROR_THRESHOLD
    """

    def __init__(
        self,
        cooldowns_file: Path = COOLDOWNS_FILE,
        auth_profiles_file: Path = AUTH_PROFILES_FILE,
    ):
        self.cooldowns_file = cooldowns_file
        self.auth_profiles_file = auth_profiles_file

    # ------------------------------------------------------------------ #
    # Internal helpers                                                      #
    # ------------------------------------------------------------------ #

    def _load_cooldowns(self) -> Dict:
        if not self.cooldowns_file.exists():
            return {}
        try:
            return json.loads(self.cooldowns_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cooldowns(self, data: Dict) -> None:
        self.cooldowns_file.parent.mkdir(parents=True, exist_ok=True)
        self.cooldowns_file.write_text(json.dumps(data, indent=2))

    def _load_auth_profiles(self) -> Optional[Dict]:
        if not self.auth_profiles_file.exists():
            return None
        try:
            return json.loads(self.auth_profiles_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _save_auth_profiles(self, data: Dict) -> None:
        self.auth_profiles_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_profiles_file.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def clear_expired(self) -> List[str]:
        """Clear cooldowns where cooldownUntil < now (and errorCount is low).

        Returns list of cleared profile keys.
        """
        data = self._load_cooldowns()
        if not data:
            return []

        now = time.time()
        cleared: List[str] = []
        updated: Dict = {}

        for key, entry in data.items():
            cooldown_until = entry.get("cooldownUntil", 0)
            error_count = entry.get("errorCount", 0)

            if not cooldown_until:
                updated[key] = entry
                continue

            if cooldown_until < now and error_count < HIGH_ERROR_THRESHOLD:
                cleared.append(key)
                logger.info("[tokenpak] Cleared expired cooldown for %s", key)
            else:
                updated[key] = entry

        if cleared:
            self._save_cooldowns(updated)

        return cleared

    def clear_expired_from_profiles(self) -> List[str]:
        """Clear cooldownUntil fields from auth-profiles.json when expired.

        Returns list of cleared profile names.
        """
        profiles = self._load_auth_profiles()
        if not isinstance(profiles, dict):
            return []

        now = time.time()
        cleared: List[str] = []
        changed = False

        for profile_name, profile in profiles.items():
            cooldown_until = profile.get("cooldownUntil", 0)
            if not cooldown_until:
                continue
            error_count = profile.get("errorCount", 0)
            if cooldown_until < now and error_count < HIGH_ERROR_THRESHOLD:
                profile.pop("cooldownUntil", None)
                profile.pop("usageStats", None)
                cleared.append(profile_name)
                changed = True
                logger.info(
                    "[tokenpak] Cleared expired cooldown for auth profile: %s", profile_name
                )

        if changed:
            self._save_auth_profiles(profiles)

        return cleared

    def get_active_cooldowns(self) -> Dict[str, float]:
        """Return map of profile key → seconds remaining for active cooldowns."""
        now = time.time()
        active: Dict[str, float] = {}

        data = self._load_cooldowns()
        for key, entry in data.items():
            cooldown_until = entry.get("cooldownUntil", 0)
            if cooldown_until and cooldown_until > now:
                active[key] = cooldown_until - now

        profiles = self._load_auth_profiles()
        if isinstance(profiles, dict):
            for name, profile in profiles.items():
                cooldown_until = profile.get("cooldownUntil", 0)
                if cooldown_until and cooldown_until > now:
                    active[f"profile:{name}"] = cooldown_until - now

        return active

    def run_cycle(self) -> int:
        """Run one clear cycle across both sources. Returns count of cleared entries."""
        c1 = self.clear_expired()
        c2 = self.clear_expired_from_profiles()
        total = len(c1) + len(c2)
        if total:
            logger.info("[tokenpak] Auto-clear: removed %d expired cooldown(s)", total)
        return total


# ---------------------------------------------------------------------------
# Background task (async)
# ---------------------------------------------------------------------------


class BackgroundCooldownClearer:
    """Asyncio background task that auto-clears expired cooldowns every N seconds.

    Runs inside the proxy event loop (no extra threads needed).

    Config key: auth.auto_clear_cooldowns (bool, default True)
    Backoff: skips clear if any key has errorCount >= HIGH_ERROR_THRESHOLD.
    """

    def __init__(
        self,
        interval: int = 60,
        manager: Optional[CooldownManager] = None,
        enabled: bool = True,
    ):
        self.interval = interval
        self.manager = manager or CooldownManager()
        self.enabled = enabled
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def _loop(self) -> None:
        logger.info("[tokenpak] BackgroundCooldownClearer started (interval=%ds)", self.interval)
        while not self._stop_event.is_set():
            try:
                if self.enabled:
                    self.manager.run_cycle()
            except Exception as exc:
                logger.warning("[tokenpak] CooldownClearer error: %s", exc)
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=self.interval,
                )
            except asyncio.TimeoutError:
                pass  # Normal — interval elapsed

    async def start(self) -> None:
        """Start the background task (idempotent)."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="cooldown-clearer")

    async def stop(self) -> None:
        """Signal the background task to stop and wait for it."""
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        logger.info("[tokenpak] BackgroundCooldownClearer stopped")
