"""
swap_alert.py — Swap pressure monitoring and Telegram alerting.

Monitors system swap usage on the local host and sends a Telegram
notification when usage exceeds a configurable threshold. Rate-limited
to prevent alert spam (max 1 alert per cooldown window).

Usage:
    from tokenpak.monitoring.swap_alert import check_swap_pressure

    # Call periodically (e.g. every 60s in proxy health loop)
    check_swap_pressure()
"""
import json
import os
import time
import urllib.request
import urllib.error
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration (overridable via env vars) ──────────────────────────────────

SWAP_ALERT_THRESHOLD_MB: int = int(os.environ.get("TOKENPAK_SWAP_ALERT_THRESHOLD_MB", "1024"))
SWAP_ALERT_COOLDOWN_S: int = int(os.environ.get("TOKENPAK_SWAP_ALERT_COOLDOWN_S", "1800"))  # 30 min
TELEGRAM_CHAT_ID: str = os.environ.get("TOKENPAK_ALERT_CHAT_ID", "")
HOSTNAME: str = os.environ.get("TOKENPAK_ALERT_HOSTNAME", os.uname().nodename)

# ── Additional thresholds — transferred from monolith (TPK-CONSOLIDATION-A2a) ──
# TOKENPAK_SWAP_WARN_MB: lower warn threshold (process-level swap pressure check)
SWAP_PRESSURE_THRESHOLD_MB: int = int(os.environ.get("TOKENPAK_SWAP_WARN_MB", "600"))
# TOKENPAK_SWAP_ALERT_MB: higher threshold for Telegram alert (system-wide swap)
SWAP_TELEGRAM_ALERT_MB: int = int(os.environ.get("TOKENPAK_SWAP_ALERT_MB", "1024"))
# Self-heal script path and cooldown
SWAP_SELF_HEAL_SCRIPT: str = os.environ.get(
    "TOKENPAK_SWAP_SELF_HEAL_SCRIPT",
    os.path.expanduser("~/.tokenpak/scripts/self-heal-memory.sh"),
)
_SWAP_SELF_HEAL_COOLDOWN_S: int = int(os.environ.get("TOKENPAK_SWAP_SELF_HEAL_COOLDOWN_S", "1800"))

# ── Module-level state ────────────────────────────────────────────────────────

_last_alert_time: float = 0.0


def _get_swap_mb() -> tuple[float, float, float]:
    """
    Read swap usage from /proc/meminfo.
    Returns (used_mb, total_mb, pct_used).
    Falls back to psutil if available.
    """
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])  # kB
        total_kb = info.get("SwapTotal", 0)
        free_kb = info.get("SwapFree", 0)
        used_kb = total_kb - free_kb
        total_mb = total_kb / 1024
        used_mb = used_kb / 1024
        pct = (used_mb / total_mb * 100) if total_mb > 0 else 0.0
        return used_mb, total_mb, pct
    except Exception:
        pass

    try:
        import psutil
        s = psutil.swap_memory()
        used_mb = s.used / 1024 ** 2
        total_mb = s.total / 1024 ** 2
        return used_mb, total_mb, s.percent
    except ImportError:
        return 0.0, 0.0, 0.0


def _get_telegram_token() -> Optional[str]:
    """Read Telegram bot token from tokenpak config."""
    config_path = os.path.expanduser("~/.tokenpak/config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        return (
            cfg.get("channels", {}).get("telegram", {}).get("botToken")
            or os.environ.get("TELEGRAM_BOT_TOKEN")
        )
    except Exception:
        return os.environ.get("TELEGRAM_BOT_TOKEN")


def _send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    token = _get_telegram_token()
    if not token:
        logger.warning("[swap_alert] No Telegram bot token found — cannot send alert")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }).encode()

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except (urllib.error.URLError, OSError) as e:
        logger.warning(f"[swap_alert] Telegram send failed: {e}")
        return False


def check_swap_pressure(
    threshold_mb: Optional[int] = None,
    cooldown_s: Optional[int] = None,
) -> bool:
    """
    Check current swap usage and send a Telegram alert if threshold exceeded.

    Args:
        threshold_mb: Alert if swap used > this (MB). Defaults to SWAP_ALERT_THRESHOLD_MB.
        cooldown_s: Minimum seconds between alerts. Defaults to SWAP_ALERT_COOLDOWN_S.

    Returns:
        True if an alert was fired, False otherwise.
    """
    global _last_alert_time

    threshold = threshold_mb if threshold_mb is not None else SWAP_ALERT_THRESHOLD_MB
    cooldown = cooldown_s if cooldown_s is not None else SWAP_ALERT_COOLDOWN_S

    used_mb, total_mb, pct = _get_swap_mb()

    if used_mb < threshold:
        return False  # Under threshold — no alert needed

    now = time.time()
    if now - _last_alert_time < cooldown:
        logger.debug(
            f"[swap_alert] Swap at {used_mb:.0f}MB but rate-limited "
            f"(next alert in {int(cooldown - (now - _last_alert_time))}s)"
        )
        return False  # Rate-limited

    _last_alert_time = now
    msg = (
        f"⚠️ <b>Swap alert — {HOSTNAME}</b>\n"
        f"Swap: {used_mb:.0f}MB / {total_mb:.0f}MB ({pct:.0f}%)\n"
        f"Threshold: {threshold}MB — investigate memory pressure"
    )
    logger.warning(f"[swap_alert] {msg}")
    success = _send_telegram(msg)
    if success:
        logger.info("[swap_alert] Alert sent to Telegram")
    return success


def get_swap_stats() -> dict:
    """Return current swap stats as a dict (for /stats endpoint)."""
    used_mb, total_mb, pct = _get_swap_mb()
    return {
        "swap_used_mb": round(used_mb, 1),
        "swap_total_mb": round(total_mb, 1),
        "swap_pct": round(pct, 1),
        "alert_threshold_mb": SWAP_ALERT_THRESHOLD_MB,
        "last_alert_ago_s": int(time.time() - _last_alert_time) if _last_alert_time > 0 else None,
    }
