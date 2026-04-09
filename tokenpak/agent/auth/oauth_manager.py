"""
TokenPak OAuth Token Auto-Refresh Manager — Phase 3 Deliverable 4.

Background task that checks OAuth token expiry every 5 minutes and
proactively refreshes tokens expiring in < 1 hour.

Supported providers:
  - openai-codex   (OAuth subscription tokens)
  - anthropic      (OAuth session tokens, when OAuth mode enabled)

Auth profiles are read from / written to: ~/.tokenpak/auth-profiles.json

Profile schema (subset relevant to OAuth):
{
  "openai-codex:default": {
    "provider": "openai-codex",
    "access_token": "<opaque>",
    "refresh_token": "<opaque>",
    "expires_at": 1709000000,   # Unix timestamp; 0 or absent = no expiry
    "token_endpoint": "https://...",
    "client_id": "...",
    ...
  }
}

SECURITY: Token values are NEVER logged. Only metadata (expiry, provider name,
seconds-remaining) is written to logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AUTH_PROFILES_FILE = Path.home() / ".tokenpak" / "auth-profiles.json"

# Providers where OAuth auto-refresh is attempted
OAUTH_PROVIDERS = {"openai-codex", "anthropic"}

# Refresh tokens expiring within this window (seconds)
REFRESH_WINDOW_SECONDS = 3600  # 1 hour

# Background task interval (seconds)
DEFAULT_INTERVAL = 300  # 5 minutes


class OAuthRefreshError(Exception):
    """Raised when a token refresh attempt fails."""


def _load_profiles() -> Dict[str, Any]:
    if not AUTH_PROFILES_FILE.exists():
        return {}
    try:
        data = json.loads(AUTH_PROFILES_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[tokenpak] OAuthManager: could not load auth-profiles.json: %s", exc)
        return {}


def _save_profiles(data: Dict[str, Any]) -> None:
    AUTH_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PROFILES_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Token refresh logic (per-provider)
# ---------------------------------------------------------------------------


async def _refresh_token_openai_codex(
    profile_name: str,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Refresh an OpenAI Codex OAuth token using the refresh_token grant."""
    import httpx

    refresh_token = profile.get("refresh_token", "")
    client_id = profile.get("client_id", "")
    token_endpoint = profile.get("token_endpoint", "https://auth.openai.com/oauth/token")

    if not refresh_token:
        raise OAuthRefreshError(f"{profile_name}: no refresh_token available")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code != 200:
        raise OAuthRefreshError(f"{profile_name}: refresh failed HTTP {resp.status_code}")

    data = resp.json()
    updated = dict(profile)
    updated["access_token"] = data["access_token"]
    if "refresh_token" in data:
        updated["refresh_token"] = data["refresh_token"]
    expires_in = data.get("expires_in", 0)
    if expires_in:
        updated["expires_at"] = int(time.time()) + int(expires_in)
    return updated


async def _refresh_token_anthropic(
    profile_name: str,
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Refresh an Anthropic OAuth session token."""
    import httpx

    refresh_token = profile.get("refresh_token", "")
    client_id = profile.get("client_id", "")
    token_endpoint = profile.get("token_endpoint", "https://claude.ai/api/oauth/token")

    if not refresh_token:
        raise OAuthRefreshError(f"{profile_name}: no refresh_token available")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code != 200:
        raise OAuthRefreshError(f"{profile_name}: Anthropic refresh failed HTTP {resp.status_code}")

    data = resp.json()
    updated = dict(profile)
    updated["access_token"] = data["access_token"]
    if "refresh_token" in data:
        updated["refresh_token"] = data["refresh_token"]
    expires_in = data.get("expires_in", 0)
    if expires_in:
        updated["expires_at"] = int(time.time()) + int(expires_in)
    return updated


_REFRESH_HANDLERS = {
    "openai-codex": _refresh_token_openai_codex,
    "anthropic": _refresh_token_anthropic,
}


# ---------------------------------------------------------------------------
# OAuthManager — check and refresh
# ---------------------------------------------------------------------------


class OAuthManager:
    """Check OAuth token expiry and refresh tokens proactively.

    Reads/writes ~/.tokenpak/auth-profiles.json.
    SECURITY: Never logs token values. Only logs metadata.
    """

    def __init__(
        self,
        auth_profiles_file: Path = AUTH_PROFILES_FILE,
        refresh_window: int = REFRESH_WINDOW_SECONDS,
    ):
        self.auth_profiles_file = auth_profiles_file
        self.refresh_window = refresh_window

    def get_expiring_profiles(self) -> List[tuple[str, Dict[str, Any], float]]:
        """Return list of (name, profile, seconds_remaining) for expiring OAuth tokens."""
        if not self.auth_profiles_file.exists():
            return []
        try:
            data = json.loads(self.auth_profiles_file.read_text())
            profiles = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return []
        now = time.time()
        expiring = []

        for name, profile in profiles.items():
            provider = profile.get("provider", "")
            if provider not in OAUTH_PROVIDERS:
                continue
            if not profile.get("refresh_token"):
                continue
            expires_at = profile.get("expires_at", 0)
            if not expires_at:
                continue  # no expiry info — skip
            remaining = expires_at - now
            if remaining < self.refresh_window:
                expiring.append((name, profile, remaining))

        return expiring

    async def refresh_profile(self, profile_name: str, profile: Dict[str, Any]) -> bool:
        """Attempt to refresh a single profile. Returns True on success.

        On success: updates auth-profiles.json on disk.
        On failure: logs error, suggests manual re-auth.
        SECURITY: Token values never logged.
        """
        provider = profile.get("provider", "unknown")
        handler = _REFRESH_HANDLERS.get(provider)

        if not handler:
            logger.warning(
                "[tokenpak] OAuthManager: no refresh handler for provider '%s' (profile: %s)",
                provider,
                profile_name,
            )
            return False

        try:
            logger.info(
                "[tokenpak] OAuthManager: refreshing %s (provider=%s)",
                profile_name,
                provider,
            )
            updated_profile = await handler(profile_name, profile)
            # Persist update
            if self.auth_profiles_file.exists():
                try:
                    all_profiles = json.loads(self.auth_profiles_file.read_text())
                    if not isinstance(all_profiles, dict):
                        all_profiles = {}
                except Exception:
                    all_profiles = {}
            else:
                all_profiles = {}
            all_profiles[profile_name] = updated_profile
            self.auth_profiles_file.parent.mkdir(parents=True, exist_ok=True)
            self.auth_profiles_file.write_text(json.dumps(all_profiles, indent=2))

            new_expiry = updated_profile.get("expires_at", 0)
            remaining_h = (new_expiry - time.time()) / 3600 if new_expiry else 0
            logger.info(
                "[tokenpak] OAuthManager: refreshed %s — new expiry in %.1fh",
                profile_name,
                remaining_h,
            )
            return True

        except OAuthRefreshError as exc:
            logger.error(
                "[tokenpak] OAuthManager: refresh failed for %s: %s. "
                "Run: tokenpak auth login --profile %s",
                profile_name,
                exc,
                profile_name,
            )
            return False
        except Exception as exc:
            logger.error(
                "[tokenpak] OAuthManager: unexpected error refreshing %s: %s. "
                "Run: tokenpak auth login --profile %s",
                profile_name,
                exc,
                profile_name,
            )
            return False

    async def run_cycle(self) -> Dict[str, bool]:
        """Check all profiles and refresh expiring ones. Returns {name: success}."""
        expiring = self.get_expiring_profiles()
        if not expiring:
            return {}

        results: Dict[str, bool] = {}
        for name, profile, remaining in expiring:
            remaining_h = remaining / 3600
            logger.info(
                "[tokenpak] OAuthManager: %s expires in %.1fh — refreshing",
                name,
                remaining_h,
            )
            results[name] = await self.refresh_profile(name, profile)
        return results


# ---------------------------------------------------------------------------
# Background task (async)
# ---------------------------------------------------------------------------


class BackgroundOAuthRefresher:
    """Asyncio background task that checks and refreshes OAuth tokens every N seconds.

    Runs inside the proxy event loop.
    Default interval: 5 minutes (300s).
    """

    def __init__(
        self,
        interval: int = DEFAULT_INTERVAL,
        manager: Optional[OAuthManager] = None,
        enabled: bool = True,
    ):
        self.interval = interval
        self.manager = manager or OAuthManager()
        self.enabled = enabled
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def _loop(self) -> None:
        logger.info("[tokenpak] BackgroundOAuthRefresher started (interval=%ds)", self.interval)
        while not self._stop_event.is_set():
            try:
                if self.enabled:
                    results = await self.manager.run_cycle()
                    if results:
                        ok = sum(1 for v in results.values() if v)
                        fail = len(results) - ok
                        logger.info(
                            "[tokenpak] OAuthRefresher: cycle done — %d refreshed, %d failed",
                            ok,
                            fail,
                        )
            except Exception as exc:
                logger.warning("[tokenpak] OAuthRefresher error: %s", exc)
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=self.interval,
                )
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        """Start the background task (idempotent)."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="oauth-refresher")

    async def stop(self) -> None:
        """Signal the background task to stop and wait for it."""
        self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        logger.info("[tokenpak] BackgroundOAuthRefresher stopped")
