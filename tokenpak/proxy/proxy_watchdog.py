"""
TokenPak Health Watchdog — Auto-healing daemon

Monitors proxy health and automatically fixes common issues:
- Restarts crashed proxy with exponential backoff
- Clears expired auth cooldowns
- Triggers OAuth token refresh before expiry
- Kills orphan processes on port conflicts
- Logs to ~/.tokenpak/watchdog.log
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TypeAlias

from tokenpak import _paths  # scoped-home path resolver (honors TOKENPAK_HOME)

# Configuration
PROXY_PORT = int(os.environ.get("TOKENPAK_PORT", "8766"))
HEALTH_CHECK_INTERVAL = 30  # seconds
STATS_INTERVAL = 3600  # 1 hour
MAX_RESTART_ATTEMPTS = 5
RESTART_BACKOFF_BASE = 2  # exponential: 2s, 4s, 8s, 16s, 32s


# Runtime-state paths are resolved on use, never at import.
#
# These were four module-level constants built from the *read* resolver, and
# the module then created a directory and opened a log file next to them —
# all at import. Importing the watchdog therefore wrote to whichever home the
# read resolver named, and since resolution keys on which home holds state,
# that single import could hand the installation to the legacy directory
# permanently. Reading must not create state, and importing a module must not
# touch the filesystem; both rules were broken by the same eight lines.
def proxy_pid_file() -> Path:
    """Where the proxy records its PID."""
    return _paths.write_under("proxy.pid")


def watchdog_log() -> Path:
    """Where this daemon writes its own log."""
    return _paths.write_under("watchdog.log")


def cooldowns_file_path() -> Path:
    """Auth cooldown ledger."""
    return _paths.write_under("cooldowns.json")


def auth_profiles_file() -> Path:
    """Auth profile store the cooldown sweep edits."""
    return _paths.write_under("auth-profiles.json")


logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Attach the file handler. Called by the entrypoint, not by import.

    Creating the log directory is a side effect the daemon is entitled to; an
    import is not.
    """
    log_path = watchdog_log()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [watchdog] %(levelname)s — %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )


# ---------------------------------------------------------------------------
# Cooldown Manager
# ---------------------------------------------------------------------------


_CooldownEntry: TypeAlias = dict[str, object]


class CooldownManager:
    """Manage and auto-clear expired auth cooldowns.

    Cooldown entries are stored in ~/.tokenpak/cooldowns.json:
    {
        "anthropic:default": {"cooldownUntil": 1709000000, "errorCount": 3},
        ...
    }
    When cooldownUntil < now AND errorCount is low, the entry is cleared.
    """

    HIGH_ERROR_THRESHOLD = 10  # don't clear if errorCount is high (real problem)

    def __init__(self, cooldowns_file: Path | None = None) -> None:
        # Resolved here rather than as a default argument: a default is
        # evaluated once at import, which is the same bug in a second form.
        self.cooldowns_file = (
            cooldowns_file if cooldowns_file is not None else cooldowns_file_path()
        )

    def _load(self) -> dict[str, _CooldownEntry]:
        if not self.cooldowns_file.exists():
            return {}
        try:
            raw = json.loads(self.cooldowns_file.read_text())
            if not isinstance(raw, dict):
                return {}
            entries: dict[str, _CooldownEntry] = {}
            for key, entry in raw.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                # Preserve provider-specific fields such as usageStats.  The
                # watchdog narrows only the fields it reads; it does not own
                # or rewrite the rest of the persisted entry schema.
                entries[key] = dict(entry)
            return entries
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, _CooldownEntry]) -> None:
        self.cooldowns_file.write_text(json.dumps(data, indent=2))

    def clear_expired(self) -> list[str]:
        """Clear cooldowns where cooldownUntil < now. Returns list of cleared keys."""
        data = self._load()
        if not data:
            return []

        now = time.time()
        cleared: list[str] = []
        updated: dict[str, _CooldownEntry] = {}

        for key, entry in data.items():
            raw_cooldown_until = entry.get("cooldownUntil", 0)
            raw_error_count = entry.get("errorCount", 0)
            cooldown_until = (
                float(raw_cooldown_until) if isinstance(raw_cooldown_until, (int, float)) else 0.0
            )
            error_count = raw_error_count if isinstance(raw_error_count, int) else 0

            if cooldown_until == 0:
                # No cooldown timestamp — skip
                updated[key] = entry
                continue

            if cooldown_until < now and error_count < self.HIGH_ERROR_THRESHOLD:
                cleared.append(key)
                logger.info(f"Cleared expired cooldown for {key}")
            else:
                updated[key] = entry

        if cleared:
            self._save(updated)

        return cleared

    def check_auth_profiles(self) -> list[str]:
        """Check auth-profiles.json for profiles with cooldownUntil set. Returns warnings."""
        if not auth_profiles_file().exists():
            return []

        try:
            profiles = json.loads(auth_profiles_file().read_text())
        except (json.JSONDecodeError, OSError):
            return []

        warnings: list[str] = []
        now = time.time()
        changed = False

        if isinstance(profiles, dict):
            for profile_name, profile in profiles.items():
                if not isinstance(profile_name, str) or not isinstance(profile, dict):
                    continue
                cooldown_until = profile.get("cooldownUntil", 0)
                if cooldown_until and cooldown_until < now:
                    error_count = profile.get("errorCount", 0)
                    if error_count < self.HIGH_ERROR_THRESHOLD:
                        profile.pop("cooldownUntil", None)
                        profile.pop("usageStats", None)
                        changed = True
                        logger.info(f"Cleared expired cooldown for auth profile: {profile_name}")
                elif cooldown_until and cooldown_until > now:
                    remaining = int(cooldown_until - now)
                    warnings.append(f"{profile_name} in cooldown for {remaining}s more")

        if changed:
            auth_profiles_file().write_text(json.dumps(profiles, indent=2))

        return warnings


# ---------------------------------------------------------------------------
# Proxy Watchdog
# ---------------------------------------------------------------------------


class ProxyWatchdog:
    """Monitor and auto-heal proxy process."""

    def __init__(self) -> None:
        self.restart_count = 0
        self.last_stats_log = 0.0
        self.cooldown_mgr = CooldownManager()

    def is_proxy_running(self) -> bool:
        """Check if proxy process is running and responding."""
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "2", f"http://localhost:{PROXY_PORT}/health"],
                capture_output=True,
                timeout=3,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("status") in ("ok", "degraded")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            pass
        return False

    def is_port_listening(self) -> bool:
        """Check if the proxy port is actually listening."""
        try:
            result = subprocess.run(
                ["ss", "-tlnp"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return f":{PROXY_PORT}" in result.stdout
        except Exception:
            pass
        return False

    def restart_proxy(self) -> bool:
        """Restart the proxy with exponential backoff."""
        if self.restart_count >= MAX_RESTART_ATTEMPTS:
            logger.error(
                f"Max restart attempts ({MAX_RESTART_ATTEMPTS}) reached. Manual intervention required."
            )
            return False

        backoff = RESTART_BACKOFF_BASE**self.restart_count
        logger.info(f"Restarting proxy... (attempt {self.restart_count + 1}, backoff {backoff}s)")

        try:
            # Kill any existing proxy process
            subprocess.run(["pkill", "-f", "tokenpak.proxy"], timeout=2)
            subprocess.run(["pkill", "-f", "tokenpak/proxy"], timeout=2)
            time.sleep(1)

            # Start new proxy
            subprocess.Popen(
                [sys.executable, "-m", "tokenpak.proxy"],
                cwd=Path.home() / "tokenpak",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.restart_count += 1
            time.sleep(backoff)

            # Verify it started
            for _ in range(10):
                if self.is_proxy_running():
                    logger.info("Proxy restarted successfully")
                    self.restart_count = 0
                    return True
                time.sleep(1)

            logger.warning("Proxy started but not yet responding")
            return False

        except Exception as e:
            logger.error(f"Failed to restart proxy: {e}")
            return False

    def check_memory_usage(self) -> None:
        """Warn if proxy memory exceeds 500MB."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "tokenpak.proxy"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            pids = result.stdout.strip().split()
            for pid in pids:
                if pid:
                    result2 = subprocess.run(
                        ["ps", "-p", pid, "-o", "rss="],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    rss_kb = int(result2.stdout.strip() or 0)
                    rss_mb = rss_kb / 1024
                    if rss_mb > 500:
                        logger.warning(f"Proxy memory high: {rss_mb:.1f}MB (pid {pid})")
        except Exception:
            pass

    def check_error_rate(self) -> None:
        """Warn if proxy error rate in session is high."""
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "2", f"http://localhost:{PROXY_PORT}/stats/session"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                errors = data.get("errors", 0)
                if errors > 10:
                    logger.warning(f"High error rate in session: {errors} errors")
        except Exception:
            pass

    def clear_cooldowns(self) -> None:
        """Clear any expired cooldowns from state files."""
        cleared = self.cooldown_mgr.clear_expired()
        warnings = self.cooldown_mgr.check_auth_profiles()
        for w in warnings:
            logger.info(f"Auth profile cooldown active: {w}")

    def log_stats(self) -> None:
        """Log summary stats every hour."""
        now = time.time()
        if now - self.last_stats_log > STATS_INTERVAL:
            try:
                result = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "--max-time",
                        "2",
                        f"http://localhost:{PROXY_PORT}/stats/session",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    stats = json.loads(result.stdout)
                    logger.info(
                        f"Hourly stats — requests: {stats.get('requests', 0)}, "
                        f"errors: {stats.get('errors', 0)}, "
                        f"saved_tokens: {stats.get('saved_tokens', 0)}"
                    )
                    self.last_stats_log = now
            except Exception:
                pass

    def run(self) -> None:
        """Main watchdog loop."""
        logger.info("TokenPak watchdog started")

        while True:
            try:
                # Check proxy health; restart if needed
                if not self.is_proxy_running():
                    logger.warning("Proxy not responding — attempting restart")
                    self.restart_proxy()

                # Memory and error rate checks
                self.check_memory_usage()
                self.check_error_rate()

                # Auto-clear expired cooldowns
                self.clear_cooldowns()

                # Periodic stats log
                self.log_stats()

                time.sleep(HEALTH_CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Watchdog shutting down")
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                time.sleep(HEALTH_CHECK_INTERVAL)


def main() -> None:
    """Run watchdog daemon."""
    configure_logging()
    watchdog = ProxyWatchdog()
    watchdog.run()


if __name__ == "__main__":
    main()
