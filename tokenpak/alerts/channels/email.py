"""Email alert delivery channel (SMTP with TLS support).

Sends an alert email via SMTP. Supports STARTTLS (port 587) and
implicit SSL/TLS (port 465). Retries up to 3 times with exponential
backoff (1 s, 2 s, drop). Never raises — logs on failure.

Configuration (env vars or config file):
- ``TOKENPAK_SMTP_HOST``: SMTP server hostname
- ``TOKENPAK_SMTP_PORT``: port (default 587; use 465 for SSL)
- ``TOKENPAK_SMTP_USER``: login username (optional)
- ``TOKENPAK_SMTP_PASS``: login password (optional)
- ``TOKENPAK_ALERT_EMAIL_TO``: recipient address
"""
from __future__ import annotations

import logging
import smtplib
import time
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0

_SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "warning": "⚠️",
    "info": "ℹ️",
}


def _build_subject(event: str, severity: str) -> str:
    emoji = _SEVERITY_EMOJI.get(severity, "📢")
    return f"{emoji} TokenPak Alert: [{severity.upper()}] {event}"


def _build_body(event: str, severity: str, message: str) -> str:
    return f"Event: {event}\nSeverity: {severity}\n\n{message}"


def deliver(
    smtp_host: str,
    smtp_port: int,
    to_addr: str,
    event: str,
    severity: str,
    message: str,
    *,
    smtp_user: str = "",
    smtp_pass: str = "",
    from_addr: str = "",
    use_tls: bool = True,
    **kwargs: Any,
) -> bool:
    """Send an alert email via SMTP.

    Uses implicit SSL/TLS when ``smtp_port == 465``; otherwise attempts
    STARTTLS when ``use_tls=True`` (the default).
    """
    if not from_addr:
        from_addr = smtp_user or f"tokenpak@{smtp_host}"

    subject = _build_subject(event, severity)
    body = _build_body(event, severity, message)

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if smtp_port == 465:
                conn: smtplib.SMTP = smtplib.SMTP_SSL(smtp_host, smtp_port)
            else:
                conn = smtplib.SMTP(smtp_host, smtp_port)
                if use_tls:
                    conn.starttls()

            with conn:
                if smtp_user and smtp_pass:
                    conn.login(smtp_user, smtp_pass)
                conn.sendmail(from_addr, [to_addr], msg.as_string())
                logger.debug(
                    "Email delivered (attempt %d/%d) to %s",
                    attempt,
                    _MAX_RETRIES,
                    to_addr,
                )
                return True
        except (smtplib.SMTPException, OSError) as exc:
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
            else:
                logger.error(
                    "Email delivery failed after %d attempts: %s",
                    _MAX_RETRIES,
                    exc,
                )
    return False


class EmailChannel:
    """Delivers alerts via SMTP email."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        to_addr: str,
        *,
        smtp_user: str = "",
        smtp_pass: str = "",
        from_addr: str = "",
        use_tls: bool = True,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.to_addr = to_addr
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass
        self.from_addr = from_addr
        self.use_tls = use_tls

    def send(self, event: str, severity: str, message: str, **kwargs: Any) -> bool:
        return deliver(
            self.smtp_host,
            self.smtp_port,
            self.to_addr,
            event,
            severity,
            message,
            smtp_user=self.smtp_user,
            smtp_pass=self.smtp_pass,
            from_addr=self.from_addr,
            use_tls=self.use_tls,
            **kwargs,
        )
