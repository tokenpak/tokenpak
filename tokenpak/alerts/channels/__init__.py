# SPDX-License-Identifier: Apache-2.0
"""Alert channel registry — loads configured delivery channels and dispatches alerts.

Channels are read from ~/.tokenpak/config.yaml under the key:

    alerts:
      channels:
        - type: webhook
          url: https://...
        - type: slack
          webhook: https://hooks.slack.com/...
        - type: telegram
          bot_token: <token>
          chat_id: <chat_id>
        - type: email
          smtp_host: smtp.example.com
          smtp_port: 587
          smtp_user: user@example.com
          smtp_pass: secret
          to: alerts@example.com

If no config file is present, a single channel may be selected via env vars:

    TOKENPAK_ALERT_CHANNEL=telegram  (or email / slack / webhook)

    For telegram: TOKENPAK_TELEGRAM_BOT_TOKEN, TOKENPAK_TELEGRAM_CHAT_ID
    For email:    TOKENPAK_SMTP_HOST, TOKENPAK_SMTP_PORT, TOKENPAK_SMTP_USER,
                  TOKENPAK_SMTP_PASS, TOKENPAK_ALERT_EMAIL_TO
    For slack:    TOKENPAK_SLACK_WEBHOOK
    For webhook:  TOKENPAK_WEBHOOK_URL
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, cast

logger = logging.getLogger(__name__)


def _load_channel_configs() -> list[dict[str, Any]]:
    """Load channel configs from ~/.tokenpak/config.yaml (or config.json fallback).

    Falls back to env-var-based channel selection when no file config exists.
    """
    import json
    from pathlib import Path

    from tokenpak import _paths

    for config_path in (
        _paths.under("config.yaml"),
        _paths.under("config.json"),
    ):
        if not config_path.exists():
            continue
        try:
            if config_path.suffix == ".yaml":
                try:
                    import yaml

                    with open(config_path) as f:
                        data = yaml.safe_load(f) or {}
                except ImportError:
                    with open(config_path) as f:
                        data = json.load(f)
            else:
                with open(config_path) as f:
                    data = json.load(f)
            channels = data.get("alerts", {}).get("channels", [])
            if channels:
                return cast(list[dict[str, Any]], channels)
        except Exception as exc:
            logger.debug("failed to load channel config from %s: %s", config_path, exc)

    # Env-var fallback — single channel selected by TOKENPAK_ALERT_CHANNEL
    channel_type = os.environ.get("TOKENPAK_ALERT_CHANNEL", "")
    if channel_type == "telegram":
        token = os.environ.get("TOKENPAK_TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TOKENPAK_TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            return [{"type": "telegram", "bot_token": token, "chat_id": chat_id}]
    elif channel_type == "email":
        smtp_host = os.environ.get("TOKENPAK_SMTP_HOST", "")
        to_addr = os.environ.get("TOKENPAK_ALERT_EMAIL_TO", "")
        if smtp_host and to_addr:
            return [
                {
                    "type": "email",
                    "smtp_host": smtp_host,
                    "smtp_port": int(os.environ.get("TOKENPAK_SMTP_PORT", "587")),
                    "smtp_user": os.environ.get("TOKENPAK_SMTP_USER", ""),
                    "smtp_pass": os.environ.get("TOKENPAK_SMTP_PASS", ""),
                    "to": to_addr,
                }
            ]
    elif channel_type == "slack":
        webhook_url = os.environ.get("TOKENPAK_SLACK_WEBHOOK", "")
        if webhook_url:
            return [{"type": "slack", "webhook": webhook_url}]
    elif channel_type == "webhook":
        url = os.environ.get("TOKENPAK_WEBHOOK_URL", "")
        if url:
            return [{"type": "webhook", "url": url}]

    return []


def dispatch(event: str, severity: str, message: str, **extra: Any) -> None:
    """Fire-and-forget delivery to all configured channels.

    Spawns a daemon thread so delivery never blocks the caller.
    """
    channels = _load_channel_configs()
    if not channels:
        return

    def _deliver_all() -> None:
        from . import email as email_channel
        from . import slack, telegram, webhook

        for ch in channels:
            ch_type = ch.get("type")
            try:
                if ch_type == "webhook":
                    url = ch.get("url", "")
                    if url:
                        webhook.deliver(url, event, severity, message, **extra)
                elif ch_type == "slack":
                    webhook_url = ch.get("webhook", "")
                    if webhook_url:
                        slack.deliver(webhook_url, event, severity, message, **extra)
                elif ch_type == "telegram":
                    token = ch.get("bot_token", "")
                    chat_id = ch.get("chat_id", "")
                    if token and chat_id:
                        telegram.deliver(token, chat_id, event, severity, message, **extra)
                elif ch_type == "email":
                    smtp_host = ch.get("smtp_host", "")
                    to_addr = ch.get("to", "")
                    if smtp_host and to_addr:
                        email_channel.deliver(
                            smtp_host,
                            int(ch.get("smtp_port", 587)),
                            to_addr,
                            event,
                            severity,
                            message,
                            smtp_user=ch.get("smtp_user", ""),
                            smtp_pass=ch.get("smtp_pass", ""),
                            from_addr=ch.get("from", ""),
                            **extra,
                        )
                else:
                    logger.debug("unknown channel type: %s", ch_type)
            except Exception as exc:  # noqa: BLE001
                logger.warning("channel dispatch error (%s): %s", ch_type, exc)

    t = threading.Thread(target=_deliver_all, daemon=True, name="tokenpak-alert-dispatch")
    t.start()


# Alias for backwards-compatibility with tests that import dispatch_alert
dispatch_alert = dispatch

__all__ = ["email", "slack", "telegram", "webhook", "dispatch", "dispatch_alert"]
