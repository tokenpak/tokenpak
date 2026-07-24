# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.alerts.channels.telegram.

All network I/O is mocked — no real Telegram API calls are made.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from tokenpak.alerts.channels.telegram import TelegramChannel, _build_text, deliver

# ---------------------------------------------------------------------------
# _build_text helper
# ---------------------------------------------------------------------------


class TestBuildText:
    def test_critical_emoji(self):
        text = _build_text("cost_spike", "critical", "Costs doubled")
        assert text.startswith("🔴")
        assert "[cost_spike]" in text
        assert "Costs doubled" in text

    def test_warning_emoji(self):
        text = _build_text("cache_drop", "warning", "Cache hit 20%")
        assert "⚠️" in text

    def test_info_emoji(self):
        text = _build_text("startup", "info", "Service started")
        assert "ℹ️" in text

    def test_unknown_severity_fallback_emoji(self):
        text = _build_text("test", "debug", "msg")
        assert "📢" in text


# ---------------------------------------------------------------------------
# TelegramChannel.send — happy path
# ---------------------------------------------------------------------------


class TestTelegramChannelHappyPath:
    def _make_response(self):
        """Return a mock urllib response that yields ok:true."""
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.status = 200
        resp.read.return_value = json.dumps({"ok": True, "result": {}}).encode()
        return resp

    def test_returns_true_on_success(self):
        ch = TelegramChannel(token="fake-token", chat_id="12345")
        with patch(
            "tokenpak.alerts.channels.telegram.urllib.request.urlopen",
            return_value=self._make_response(),
        ):
            result = ch.send(event="cost_spike", severity="warning", message="Costs up 60%")
        assert result is True

    def test_posts_correct_chat_id(self):
        """The POST body contains the configured chat_id."""
        captured_bodies = []

        def _mock_urlopen(req, timeout=None):
            captured_bodies.append(json.loads(req.data))
            return self._make_response()

        ch = TelegramChannel(token="tok", chat_id="999")
        with patch("tokenpak.alerts.channels.telegram.urllib.request.urlopen", _mock_urlopen):
            ch.send(event="test", severity="info", message="hello")

        assert len(captured_bodies) == 1
        assert captured_bodies[0]["chat_id"] == "999"

    def test_posts_to_correct_url(self):
        """Request URL includes the bot token and sendMessage endpoint."""
        captured_urls = []

        def _mock_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            return self._make_response()

        ch = TelegramChannel(token="mytoken", chat_id="42")
        with patch("tokenpak.alerts.channels.telegram.urllib.request.urlopen", _mock_urlopen):
            ch.send(event="test", severity="info", message="msg")

        assert len(captured_urls) == 1
        assert "/botmytoken/sendMessage" in captured_urls[0]

    def test_message_text_in_payload(self):
        """The text field in the POST body contains the event and message."""
        captured_bodies = []

        def _mock_urlopen(req, timeout=None):
            captured_bodies.append(json.loads(req.data))
            return self._make_response()

        ch = TelegramChannel(token="tok", chat_id="1")
        with patch("tokenpak.alerts.channels.telegram.urllib.request.urlopen", _mock_urlopen):
            ch.send(event="budget_exceeded", severity="critical", message="Over limit")

        text = captured_bodies[0]["text"]
        assert "budget_exceeded" in text
        assert "Over limit" in text


# ---------------------------------------------------------------------------
# TelegramChannel.send — retry behaviour
# ---------------------------------------------------------------------------


class TestTelegramChannelRetry:
    def test_retries_three_times_on_url_error(self):
        """URLError triggers up to 3 attempts."""
        call_count = 0

        def _failing_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise urllib.error.URLError("connection refused")

        ch = TelegramChannel(token="tok", chat_id="1")
        with patch("tokenpak.alerts.channels.telegram.urllib.request.urlopen", _failing_urlopen):
            with patch("tokenpak.alerts.channels.telegram.time.sleep"):
                result = ch.send(event="test", severity="warning", message="retry test")

        assert result is False
        assert call_count == 3

    def test_succeeds_on_second_attempt(self):
        """If the first attempt fails but the second succeeds, returns True."""
        attempt = 0

        def _flaky_urlopen(req, timeout=None):
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise urllib.error.URLError("transient error")
            resp = MagicMock()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            resp.status = 200
            return resp

        ch = TelegramChannel(token="tok", chat_id="1")
        with patch("tokenpak.alerts.channels.telegram.urllib.request.urlopen", _flaky_urlopen):
            with patch("tokenpak.alerts.channels.telegram.time.sleep"):
                result = ch.send(event="test", severity="info", message="flaky")

        assert result is True
        assert attempt == 2

    def test_returns_false_on_permanent_failure(self):
        """Returns False (never raises) after all retry attempts are exhausted."""
        ch = TelegramChannel(token="bad-token", chat_id="0")
        with patch(
            "tokenpak.alerts.channels.telegram.urllib.request.urlopen",
            side_effect=urllib.error.URLError("no route"),
        ):
            with patch("tokenpak.alerts.channels.telegram.time.sleep"):
                result = ch.send(event="test", severity="critical", message="down")
        assert result is False

    def test_sleeps_between_retries(self):
        """time.sleep is called between failed attempts."""
        sleep_calls = []

        def _record_sleep(secs):
            sleep_calls.append(secs)

        ch = TelegramChannel(token="tok", chat_id="1")
        with patch(
            "tokenpak.alerts.channels.telegram.urllib.request.urlopen",
            side_effect=urllib.error.URLError("err"),
        ):
            with patch("tokenpak.alerts.channels.telegram.time.sleep", side_effect=_record_sleep):
                ch.send(event="test", severity="info", message="sleep test")

        # 3 attempts → 2 sleeps between them
        assert len(sleep_calls) == 2


# ---------------------------------------------------------------------------
# deliver() function directly
# ---------------------------------------------------------------------------


class TestDeliverFunction:
    def test_deliver_returns_true_on_success(self):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.status = 200

        with patch("tokenpak.alerts.channels.telegram.urllib.request.urlopen", return_value=resp):
            result = deliver("tok", "123", "evt", "info", "msg")

        assert result is True

    def test_deliver_returns_false_on_failure(self):
        with patch(
            "tokenpak.alerts.channels.telegram.urllib.request.urlopen",
            side_effect=urllib.error.URLError("fail"),
        ):
            with patch("tokenpak.alerts.channels.telegram.time.sleep"):
                result = deliver("tok", "123", "evt", "warning", "msg")

        assert result is False
