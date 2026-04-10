# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Telegram and email alert delivery channels.

All external calls (urllib.request.urlopen, smtplib.SMTP/SMTP_SSL) are mocked
so no real network or SMTP connection is made.
"""

from __future__ import annotations

import smtplib
import time
from unittest import mock

import pytest

from tokenpak.alerts.channels import telegram, email as email_channel
from tokenpak.alerts import channels as ch_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_response(status: int = 200):
    """Return a context-manager mock that simulates urlopen success."""
    resp = mock.MagicMock()
    resp.status = status
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Telegram channel
# ---------------------------------------------------------------------------


class TestTelegramAlertDeliver:
    def test_posts_to_bot_api(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response()) as m:
            result = telegram.deliver(
                token="tok123",
                chat_id="-100abc",
                event="cache_drop",
                severity="warning",
                message="Cache hit rate low",
            )
        assert result is True
        req = m.call_args[0][0]
        assert "api.telegram.org" in req.full_url
        assert "bot123" not in req.full_url  # token is tok123
        assert "sendMessage" in req.full_url

    def test_request_contains_chat_id_and_text(self):
        import json as _json

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = _json.loads(req.data)
            return _fake_response()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            telegram.deliver("TOKEN", "CHATID", "ev", "critical", "Something broke")

        assert captured["body"]["chat_id"] == "CHATID"
        assert "ev" in captured["body"]["text"]
        assert "Something broke" in captured["body"]["text"]
        assert "🔴" in captured["body"]["text"]

    def test_content_type_header(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["ct"] = req.get_header("Content-type")
            return _fake_response()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            telegram.deliver("T", "C", "ev", "info", "msg")

        assert captured["ct"] == "application/json"

    def test_returns_false_on_network_error(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=OSError("connection refused")
        ):
            with mock.patch.object(telegram, "_BACKOFF_BASE", 0.0):
                result = telegram.deliver("T", "C", "ev", "warning", "msg")
        assert result is False

    def test_retries_up_to_max_attempts(self):
        call_count = 0

        def flaky(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise OSError("simulated")

        with mock.patch("urllib.request.urlopen", side_effect=flaky):
            with mock.patch.object(telegram, "_BACKOFF_BASE", 0.0):
                telegram.deliver("T", "C", "ev", "warning", "msg")

        assert call_count == telegram._MAX_RETRIES

    def test_severity_emoji_mapping(self):
        assert "⚠️" in telegram._build_text("e", "warning", "m")
        assert "🔴" in telegram._build_text("e", "critical", "m")
        assert "ℹ️" in telegram._build_text("e", "info", "m")
        assert "📢" in telegram._build_text("e", "unknown", "m")

    def test_telegram_channel_class_send(self):
        ch = telegram.TelegramChannel(token="T", chat_id="C")
        with mock.patch("urllib.request.urlopen", return_value=_fake_response()):
            result = ch.send("ev", "info", "msg")
        assert result is True


# ---------------------------------------------------------------------------
# Email channel
# ---------------------------------------------------------------------------


class TestEmailAlertDeliver:
    def _mock_smtp(self):
        """Return a mock SMTP instance usable as a context manager."""
        smtp_instance = mock.MagicMock(spec=smtplib.SMTP)
        smtp_instance.__enter__ = mock.Mock(return_value=smtp_instance)
        smtp_instance.__exit__ = mock.Mock(return_value=False)
        return smtp_instance

    def test_sends_email_via_smtp(self):
        smtp_inst = self._mock_smtp()
        with mock.patch("smtplib.SMTP", return_value=smtp_inst):
            result = email_channel.deliver(
                "smtp.example.com",
                587,
                "alerts@example.com",
                "error_spike",
                "warning",
                "High error rate",
                smtp_user="user",
                smtp_pass="pass",
            )
        assert result is True
        smtp_inst.starttls.assert_called_once()
        smtp_inst.login.assert_called_once_with("user", "pass")
        smtp_inst.sendmail.assert_called_once()

    def test_uses_ssl_on_port_465(self):
        smtp_inst = self._mock_smtp()
        with mock.patch("smtplib.SMTP_SSL", return_value=smtp_inst) as ssl_cls:
            email_channel.deliver(
                "smtp.example.com", 465, "a@b.com", "ev", "info", "msg"
            )
        ssl_cls.assert_called_once_with("smtp.example.com", 465)
        smtp_inst.starttls.assert_not_called()

    def test_no_login_when_no_credentials(self):
        smtp_inst = self._mock_smtp()
        with mock.patch("smtplib.SMTP", return_value=smtp_inst):
            email_channel.deliver(
                "smtp.example.com", 587, "a@b.com", "ev", "info", "msg"
            )
        smtp_inst.login.assert_not_called()

    def test_subject_contains_event_and_severity(self):
        subject = email_channel._build_subject("cache_drop", "critical")
        assert "cache_drop" in subject
        assert "CRITICAL" in subject
        assert "🔴" in subject

    def test_body_contains_event_and_message(self):
        body = email_channel._build_body("proxy_down", "critical", "Proxy unreachable")
        assert "proxy_down" in body
        assert "Proxy unreachable" in body
        assert "critical" in body

    def test_returns_false_on_smtp_error(self):
        with mock.patch(
            "smtplib.SMTP", side_effect=smtplib.SMTPException("connect failed")
        ):
            with mock.patch.object(email_channel, "_BACKOFF_BASE", 0.0):
                result = email_channel.deliver(
                    "bad-host", 587, "a@b.com", "ev", "warning", "msg"
                )
        assert result is False

    def test_retries_up_to_max_attempts(self):
        call_count = 0

        def flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise smtplib.SMTPException("simulated")

        with mock.patch("smtplib.SMTP", side_effect=flaky):
            with mock.patch.object(email_channel, "_BACKOFF_BASE", 0.0):
                email_channel.deliver(
                    "host", 587, "a@b.com", "ev", "warning", "msg"
                )

        assert call_count == email_channel._MAX_RETRIES

    def test_from_addr_defaults_to_smtp_user(self):
        smtp_inst = self._mock_smtp()
        with mock.patch("smtplib.SMTP", return_value=smtp_inst):
            email_channel.deliver(
                "smtp.example.com",
                587,
                "to@example.com",
                "ev",
                "info",
                "msg",
                smtp_user="sender@example.com",
                smtp_pass="pw",
            )
        call_args = smtp_inst.sendmail.call_args
        assert call_args[0][0] == "sender@example.com"

    def test_email_channel_class_send(self):
        smtp_inst = self._mock_smtp()
        ch = email_channel.EmailChannel(
            smtp_host="smtp.example.com",
            smtp_port=587,
            to_addr="to@example.com",
            smtp_user="u",
            smtp_pass="p",
        )
        with mock.patch("smtplib.SMTP", return_value=smtp_inst):
            result = ch.send("ev", "warning", "msg")
        assert result is True


# ---------------------------------------------------------------------------
# Dispatcher — telegram and email integration
# ---------------------------------------------------------------------------


class TestAlertDispatchTelegramEmail:
    def test_dispatch_calls_telegram(self):
        cfg = [{"type": "telegram", "bot_token": "TOK", "chat_id": "CID"}]

        with mock.patch("tokenpak.alerts.channels._load_channel_configs", return_value=cfg):
            with mock.patch(
                "tokenpak.alerts.channels.telegram.deliver", return_value=True
            ) as mock_deliver:
                ch_registry.dispatch("test_event", "warning", "test message")
                time.sleep(0.3)

        mock_deliver.assert_called_once_with(
            "TOK", "CID", "test_event", "warning", "test message"
        )

    def test_dispatch_calls_email(self):
        cfg = [
            {
                "type": "email",
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_user": "u",
                "smtp_pass": "p",
                "to": "alerts@example.com",
                "from": "",
            }
        ]

        with mock.patch("tokenpak.alerts.channels._load_channel_configs", return_value=cfg):
            with mock.patch(
                "tokenpak.alerts.channels.email.deliver", return_value=True
            ) as mock_deliver:
                ch_registry.dispatch("error_spike", "critical", "High errors")
                time.sleep(0.3)

        mock_deliver.assert_called_once_with(
            "smtp.example.com",
            587,
            "alerts@example.com",
            "error_spike",
            "critical",
            "High errors",
            smtp_user="u",
            smtp_pass="p",
            from_addr="",
        )

    def test_dispatch_skips_telegram_without_token(self):
        cfg = [{"type": "telegram", "bot_token": "", "chat_id": "CID"}]

        with mock.patch("tokenpak.alerts.channels._load_channel_configs", return_value=cfg):
            with mock.patch(
                "tokenpak.alerts.channels.telegram.deliver"
            ) as mock_deliver:
                ch_registry.dispatch("ev", "info", "msg")
                time.sleep(0.3)

        mock_deliver.assert_not_called()

    def test_dispatch_skips_email_without_host(self):
        cfg = [{"type": "email", "smtp_host": "", "to": "a@b.com"}]

        with mock.patch("tokenpak.alerts.channels._load_channel_configs", return_value=cfg):
            with mock.patch(
                "tokenpak.alerts.channels.email.deliver"
            ) as mock_deliver:
                ch_registry.dispatch("ev", "info", "msg")
                time.sleep(0.3)

        mock_deliver.assert_not_called()

    def test_env_var_telegram_config(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_ALERT_CHANNEL", "telegram")
        monkeypatch.setenv("TOKENPAK_TELEGRAM_BOT_TOKEN", "MYTOKEN")
        monkeypatch.setenv("TOKENPAK_TELEGRAM_CHAT_ID", "MYCHAT")

        # Also prevent config-file lookup from matching anything
        with mock.patch(
            "pathlib.Path.exists", return_value=False
        ):
            configs = ch_registry._load_channel_configs()

        assert len(configs) == 1
        assert configs[0]["type"] == "telegram"
        assert configs[0]["bot_token"] == "MYTOKEN"
        assert configs[0]["chat_id"] == "MYCHAT"

    def test_env_var_email_config(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_ALERT_CHANNEL", "email")
        monkeypatch.setenv("TOKENPAK_SMTP_HOST", "smtp.myco.com")
        monkeypatch.setenv("TOKENPAK_SMTP_PORT", "465")
        monkeypatch.setenv("TOKENPAK_SMTP_USER", "bot@myco.com")
        monkeypatch.setenv("TOKENPAK_SMTP_PASS", "secret")
        monkeypatch.setenv("TOKENPAK_ALERT_EMAIL_TO", "oncall@myco.com")

        with mock.patch("pathlib.Path.exists", return_value=False):
            configs = ch_registry._load_channel_configs()

        assert len(configs) == 1
        assert configs[0]["type"] == "email"
        assert configs[0]["smtp_host"] == "smtp.myco.com"
        assert configs[0]["smtp_port"] == 465
        assert configs[0]["to"] == "oncall@myco.com"
