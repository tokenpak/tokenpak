"""
TokenPak Auth Alert Hook — Phase 1

Wires the AuthGuard event into a Telegram notification for Kevin.
Loaded lazily at proxy startup via register_auth_alert_hook().

The alert message matches the spec from the task:
  ⚠️ TokenPak Auth Failure
  Your Anthropic token is expired/revoked.
  Proxy is OFFLINE. Requests now bypass compression (2-3x cost).
  Fix immediately: bash ~/update-anthropic-token.sh <NEW_TOKEN>
"""

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TOKENPAK_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TOKENPAK_TELEGRAM_CHAT_ID", "461720084")


def _send_telegram(message: str, chat_id: Optional[str] = None) -> bool:
    """
    Send a Telegram message via the openclaw message tool (subprocess) or
    curl if the bot token is configured directly.

    Returns True if sent successfully, False otherwise.
    """
    target = chat_id or TELEGRAM_CHAT_ID

    # Strategy 1: Try openclaw CLI (preferred — uses existing Telegram integration)
    try:
        result = subprocess.run(
            ["openclaw", "message", "send", "--target", target, "--message", message],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("auth_alert: Telegram sent via openclaw CLI")
            return True
        else:
            logger.warning("auth_alert: openclaw CLI failed: %s", result.stderr.strip())
    except FileNotFoundError:
        logger.debug("auth_alert: openclaw CLI not found, trying curl")
    except Exception as exc:
        logger.warning("auth_alert: openclaw CLI error: %s", exc)

    # Strategy 2: Direct Telegram API via curl
    if TELEGRAM_BOT_TOKEN:
        try:
            import json
            import urllib.request

            payload = json.dumps({
                "chat_id": target,
                "text": message,
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                data = json.loads(body)
                if data.get("ok"):
                    logger.info("auth_alert: Telegram sent via direct API")
                    return True
                else:
                    logger.warning("auth_alert: Telegram API error: %s", data)
        except Exception as exc:
            logger.error("auth_alert: direct Telegram API failed: %s", exc)

    return False


def _build_alert_message(provider: str, details: dict) -> str:
    count = details.get("consecutive_failures", "?")
    ts = details.get("timestamp", "unknown")
    provider_display = provider.capitalize()
    return (
        f"⚠️ <b>TokenPak Auth Failure</b>\n"
        f"Your {provider_display} token is expired or revoked.\n"
        f"Proxy is OFFLINE. Requests now bypass compression (2-3x cost).\n\n"
        f"Fix immediately:\n"
        f"<code>bash ~/update-anthropic-token.sh &lt;NEW_TOKEN&gt;</code>\n\n"
        f"Details: {count} consecutive 401/403 from {provider_display} @ {ts}"
    )


def _on_auth_failure(provider: str, event: str, details: dict) -> None:
    """Handler registered with AuthGuard."""
    if event != "auth-failure-detected":
        return

    logger.warning(
        "auth_alert: AUTH FAILURE DETECTED — provider=%s failures=%s",
        provider,
        details.get("consecutive_failures"),
    )

    message = _build_alert_message(provider, details)
    sent = _send_telegram(message)
    if not sent:
        # Last resort: print to stdout so it shows in proxy logs
        logger.error("auth_alert: FAILED TO SEND TELEGRAM ALERT!\nMessage was:\n%s", message)
        print(f"\n{'='*60}")
        print("⚠️  AUTH ALERT (Telegram delivery failed):")
        print(message)
        print(f"{'='*60}\n")


def register_auth_alert_hook() -> None:
    """
    Register the Telegram alert handler with the global AUTH_GUARD singleton.
    Call once at proxy startup.
    """
    from tokenpak.auth_guard import AUTH_GUARD
    AUTH_GUARD.on_auth_failure(_on_auth_failure)
    logger.info("auth_alert: Telegram alert hook registered")
