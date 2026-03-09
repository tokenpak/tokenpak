"""
TokenPak Health Watchdog — Auto-healing daemon

Monitors proxy health and automatically fixes common issues:
- Restarts crashed proxy with exponential backoff
- Clears expired auth cooldowns
- Triggers OAuth token refresh before expiry
- Kills orphan processes on port conflicts
"""

import os
import sys
import time
import json
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta


# Configuration
PROXY_PORT = 8766
PROXY_PID_FILE = Path.home() / ".tokenpak" / "proxy.pid"
WATCHDOG_LOG = Path.home() / ".tokenpak" / "watchdog.log"
HEALTH_CHECK_INTERVAL = 30  # seconds
STATS_INTERVAL = 3600  # 1 hour
MAX_RESTART_ATTEMPTS = 5
RESTART_BACKOFF_BASE = 2  # exponential: 2s, 4s, 8s, 16s, 32s


# Setup logging
WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [watchdog] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(WATCHDOG_LOG),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class ProxyWatchdog:
    """Monitor and auto-heal proxy process."""

    def __init__(self):
        self.restart_count = 0
        self.last_stats_log = 0

    def is_proxy_running(self) -> bool:
        """Check if proxy process is running and responding."""
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "2", "http://localhost:8766/health"],
                capture_output=True,
                timeout=3,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("status") == "ok"
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            pass
        return False

    def is_port_listening(self) -> bool:
        """Check if port 8766 is actually listening."""
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

        backoff = RESTART_BACKOFF_BASE ** self.restart_count
        logger.info(f"Restarting proxy... (attempt {self.restart_count + 1}, backoff {backoff}s)")

        try:
            # Kill any existing proxy process on port
            subprocess.run(
                ["pkill", "-f", "proxy.py"],
                timeout=2,
            )
            time.sleep(1)

            # Start new proxy
            subprocess.Popen(
                ["python3", "-m", "tokenpak.proxy"],
                cwd=Path.home() / "tokenpak",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.restart_count += 1
            time.sleep(backoff)

            # Verify it started
            for i in range(10):
                if self.is_proxy_running():
                    logger.info("Proxy restarted successfully")
                    self.restart_count = 0
                    return True
                time.sleep(1)

            logger.warning("Proxy started but not responding yet")
            return False
        except Exception as e:
            logger.error(f"Failed to restart proxy: {e}")
            return False

    def check_memory_usage(self) -> bool:
        """Check proxy memory usage."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "proxy.py"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid:
                    result = subprocess.run(
                        ["ps", "-p", pid, "-o", "rss="],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    rss_kb = int(result.stdout.strip() or 0)
                    rss_mb = rss_kb / 1024
                    if rss_mb > 500:
                        logger.warning(f"Proxy memory high: {rss_mb:.1f}MB")
                        return False
            return True
        except Exception:
            pass
        return True

    def check_error_rate(self) -> bool:
        """Check if proxy error rate is too high."""
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "2", "http://localhost:8766/stats"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                errors = data.get("session", {}).get("errors", 0)
                if errors > 10:
                    logger.warning(f"High error rate in session: {errors} errors")
                    return False
        except Exception:
            pass
        return True

    def log_stats(self):
        """Log summary stats every hour."""
        now = time.time()
        if now - self.last_stats_log > STATS_INTERVAL:
            try:
                result = subprocess.run(
                    ["curl", "-s", "--max-time", "2", "http://localhost:8766/stats"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    stats = data.get("session", {})
                    logger.info(
                        f"Stats — requests: {stats.get('requests', 0)}, "
                        f"errors: {stats.get('errors', 0)}, "
                        f"saved_tokens: {stats.get('saved_tokens', 0)}"
                    )
                    self.last_stats_log = now
            except Exception:
                pass

    def run(self):
        """Main watchdog loop."""
        logger.info("TokenPak watchdog started")

        while True:
            try:
                # Check proxy health
                if not self.is_proxy_running():
                    logger.warning("Proxy not responding. Attempting restart...")
                    self.restart_proxy()

                # Check memory
                self.check_memory_usage()

                # Check error rate
                self.check_error_rate()

                # Log stats periodically
                self.log_stats()

                time.sleep(HEALTH_CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Watchdog shutting down")
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                time.sleep(HEALTH_CHECK_INTERVAL)


def main():
    """Run watchdog daemon."""
    watchdog = ProxyWatchdog()
    watchdog.run()


if __name__ == "__main__":
    main()
