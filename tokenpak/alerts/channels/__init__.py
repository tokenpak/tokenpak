"""Alert delivery channel registry.

Loads channel config from ~/.tokenpak/config.json and dispatches alerts
to all configured channels in a background thread (fire-and-forget).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_channel_configs() -> list[dict]:
    """Load channel list from ~/.tokenpak/config.json alerts.channels."""
    config_path = Path.home() / ".tokenpak" / "config.json"
    if not config_path.exists():
        return []
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        return cfg.get("alerts", {}).get("channels", [])
    except Exception:
        return []


def _get_channel(cfg: dict):
    """Return a channel instance for the given config entry, or None."""
    ch_type = cfg.get("type", "")
    if ch_type == "webhook":
        from .webhook import WebhookChannel
        return WebhookChannel(cfg["url"])
    elif ch_type == "slack":
        from .slack import SlackChannel
        return SlackChannel(cfg["webhook"])
    else:
        logger.warning("Unknown alert channel type: %s", ch_type)
        return None


def dispatch_alert(event: str, severity: str, message: str, **kwargs) -> None:
    """Fire-and-forget: deliver alert to all configured channels.

    Runs in a daemon background thread so it never blocks the caller.
    Failures are logged and swallowed — never raised.
    """
    channel_cfgs = _load_channel_configs()
    if not channel_cfgs:
        return

    def _send() -> None:
        for cfg in channel_cfgs:
            ch = _get_channel(cfg)
            if ch is None:
                continue
            try:
                ch.send(event=event, severity=severity, message=message, **kwargs)
            except Exception as exc:
                logger.error("Alert channel delivery failed (%s): %s", cfg.get("type"), exc)

    t = threading.Thread(target=_send, daemon=True)
    t.start()
