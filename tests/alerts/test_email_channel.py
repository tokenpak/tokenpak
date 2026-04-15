# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.alerts.channels.email.

All SMTP I/O is mocked — no real mail server is contacted.
"""
from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, call, patch

import pytest

from tokenpak.alerts.channels.email import EmailChannel, deliver, _build_subject, _build_body


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


class TestBuildSubject:
    def test_critical_contains_emoji_and_event(self):
        subj = _build_subject("cost_spike", "critical")
        assert "🔴" in subj
        assert "cost_spike" in subj
        assert "CRITICAL" in subj

    def test_warning_contains_emoji(self):
        subj = _build_subject("cache_drop", "warning")
        assert "⚠️" in subj
        assert "WARNING" in subj

    def test_info_contains_emoji(self):
        subj = _build_subject("startup", "info")
        assert "ℹ️" in subj

    def test_unknown_severity_fallback(self):
        subj = _build_subject("evt", "debug")
        assert "📢" in subj


class TestBuildBody:
    def test_contains_event_severity_message(self):
        body = _build_body("cost_spike", "warning", "Costs up 60%")
        assert "cost_spike" in body
        assert "warning" in body
        assert "Costs up 60%" in body


# ---------------------------------------------------------------------------
# EmailChannel.send — happy path (STARTTLS, port 587)
# ---------------------------------------------------------------------------


class TestEmailChannelStarttls:
    def _mock_smtp_cls(self):
        """Return a mock SMTP class (context manager) that records send calls."""
        smtp_instance = MagicMock()
        smtp_instance.__enter__ = lambda s: s
        smtp_instance.__exit__ = MagicMock(return_value=False)
        smtp_cls = MagicMock(return_value=smtp_instance)
        return smtp_cls, smtp_instance

    def test_returns_true_on_success(self):
        smtp_cls, inst = self._mock_smtp_cls()
        ch = EmailChannel(
            smtp_host="smtp.example.com",
            smtp_port=587,
            to_addr="alerts@example.com",
        )
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", smtp_cls):
            result = ch.send(event="cost_spike", severity="warning", message="Costs up 60%")
        assert result is True

    def test_calls_starttls_for_port_587(self):
        smtp_cls, inst = self._mock_smtp_cls()
        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=587, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", smtp_cls):
            ch.send(event="evt", severity="info", message="msg")
        inst.starttls.assert_called_once()

    def test_login_called_when_credentials_provided(self):
        smtp_cls, inst = self._mock_smtp_cls()
        ch = EmailChannel(
            smtp_host="smtp.example.com",
            smtp_port=587,
            to_addr="alerts@example.com",
            smtp_user="user@example.com",
            smtp_pass="secret",
        )
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", smtp_cls):
            ch.send(event="test", severity="info", message="auth test")
        inst.login.assert_called_once_with("user@example.com", "secret")

    def test_sendmail_called_with_correct_recipient(self):
        smtp_cls, inst = self._mock_smtp_cls()
        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=587, to_addr="dest@example.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", smtp_cls):
            ch.send(event="evt", severity="warning", message="msg")
        args = inst.sendmail.call_args
        # sendmail(from, [to], msg_string)
        assert "dest@example.com" in args[0][1]

    def test_no_login_when_no_credentials(self):
        smtp_cls, inst = self._mock_smtp_cls()
        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=587, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", smtp_cls):
            ch.send(event="test", severity="info", message="no creds")
        inst.login.assert_not_called()


# ---------------------------------------------------------------------------
# EmailChannel.send — SSL mode (port 465)
# ---------------------------------------------------------------------------


class TestEmailChannelSsl:
    def _mock_smtp_ssl_cls(self):
        inst = MagicMock()
        inst.__enter__ = lambda s: s
        inst.__exit__ = MagicMock(return_value=False)
        smtp_cls = MagicMock(return_value=inst)
        return smtp_cls, inst

    def test_uses_smtp_ssl_for_port_465(self):
        ssl_cls, inst = self._mock_smtp_ssl_cls()
        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=465, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP_SSL", ssl_cls):
            result = ch.send(event="test", severity="critical", message="ssl test")
        assert result is True
        ssl_cls.assert_called_once_with("smtp.example.com", 465)

    def test_starttls_not_called_for_port_465(self):
        ssl_cls, inst = self._mock_smtp_ssl_cls()
        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=465, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP_SSL", ssl_cls):
            ch.send(event="test", severity="info", message="no starttls")
        inst.starttls.assert_not_called()


# ---------------------------------------------------------------------------
# EmailChannel.send — retry behaviour
# ---------------------------------------------------------------------------


class TestEmailChannelRetry:
    def test_retries_three_times_on_smtp_exception(self):
        """SMTPException triggers up to 3 attempts."""
        call_count = 0

        def _failing_smtp(host, port):
            nonlocal call_count
            call_count += 1
            raise smtplib.SMTPException("connection refused")

        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=587, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", side_effect=_failing_smtp):
            with patch("tokenpak.alerts.channels.email.time.sleep"):
                result = ch.send(event="test", severity="warning", message="retry test")

        assert result is False
        assert call_count == 3

    def test_returns_false_on_permanent_failure(self):
        """Returns False (never raises) after all attempts are exhausted."""
        ch = EmailChannel(smtp_host="bad.host", smtp_port=587, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP",
                   side_effect=OSError("no route to host")):
            with patch("tokenpak.alerts.channels.email.time.sleep"):
                result = ch.send(event="test", severity="critical", message="permanent fail")
        assert result is False

    def test_succeeds_on_second_attempt(self):
        """Returns True if the second attempt succeeds after first fails."""
        attempt = 0

        def _flaky_smtp(host, port):
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise smtplib.SMTPException("transient error")
            inst = MagicMock()
            inst.__enter__ = lambda s: s
            inst.__exit__ = MagicMock(return_value=False)
            return inst

        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=587, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", side_effect=_flaky_smtp):
            with patch("tokenpak.alerts.channels.email.time.sleep"):
                result = ch.send(event="test", severity="info", message="flaky")

        assert result is True
        assert attempt == 2

    def test_sleeps_between_retries(self):
        """time.sleep is called between failed attempts."""
        sleep_calls = []

        def _record_sleep(secs):
            sleep_calls.append(secs)

        ch = EmailChannel(smtp_host="smtp.example.com", smtp_port=587, to_addr="x@x.com")
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP",
                   side_effect=smtplib.SMTPException("err")):
            with patch("tokenpak.alerts.channels.email.time.sleep", side_effect=_record_sleep):
                ch.send(event="test", severity="warning", message="sleep test")

        # 3 attempts → 2 sleeps between them
        assert len(sleep_calls) == 2


# ---------------------------------------------------------------------------
# deliver() function directly
# ---------------------------------------------------------------------------


class TestDeliverFunction:
    def test_deliver_returns_true_on_success(self):
        inst = MagicMock()
        inst.__enter__ = lambda s: s
        inst.__exit__ = MagicMock(return_value=False)
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", return_value=inst):
            result = deliver(
                "smtp.example.com", 587, "dest@example.com", "evt", "info", "msg"
            )
        assert result is True

    def test_deliver_returns_false_on_failure(self):
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP",
                   side_effect=smtplib.SMTPException("fail")):
            with patch("tokenpak.alerts.channels.email.time.sleep"):
                result = deliver(
                    "smtp.example.com", 587, "dest@example.com", "evt", "warning", "msg"
                )
        assert result is False

    def test_custom_from_addr_used(self):
        inst = MagicMock()
        inst.__enter__ = lambda s: s
        inst.__exit__ = MagicMock(return_value=False)
        with patch("tokenpak.alerts.channels.email.smtplib.SMTP", return_value=inst):
            deliver(
                "smtp.example.com", 587, "to@x.com", "evt", "info", "msg",
                from_addr="sender@x.com"
            )
        args = inst.sendmail.call_args[0]
        assert args[0] == "sender@x.com"
