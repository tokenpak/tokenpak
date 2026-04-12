# SPDX-License-Identifier: MIT
"""Tests for alert delivery channels (webhook + Slack).

Uses a local stub HTTP server instead of httpbin.org to avoid flakiness.
The stub server records all received requests so tests can assert on them.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

# Guard against environments where tokenpak.alerts is unavailable.
# tokenpak/alerts/__init__.py is a shim that imports tokenpak._internal.alerts;
# if that module does not exist the entire package init fails at collection time.
try:
    from tokenpak.alerts.channels.webhook import WebhookChannel
    from tokenpak.alerts.channels.slack import SlackChannel
    from tokenpak.alerts.channels import dispatch_alert, _load_channel_configs
    from tokenpak.license.tier import LicenseTier
    _ALERTS_AVAILABLE = True
except ImportError:
    WebhookChannel = None  # type: ignore[assignment,misc]
    SlackChannel = None  # type: ignore[assignment,misc]
    dispatch_alert = None  # type: ignore[assignment]
    _load_channel_configs = None  # type: ignore[assignment]
    LicenseTier = None  # type: ignore[assignment,misc]
    _ALERTS_AVAILABLE = False

pytestmark = [
    pytest.mark.needs_internal_alerts,
    pytest.mark.skipif(
        not _ALERTS_AVAILABLE,
        reason="tokenpak.alerts modules not available in this environment (tokenpak._internal.alerts missing)",
    ),
]


@pytest.fixture(autouse=True)
def _mock_pro_tier(monkeypatch):
    """Bypass the Pro license gate so delivery logic can be tested without a real license."""
    monkeypatch.setattr(
        "tokenpak.license.loader._active_tier",
        LicenseTier.PRO,
    )


# ---------------------------------------------------------------------------
# Local stub HTTP server
# ---------------------------------------------------------------------------


class _RequestRecord:
    """Thread-safe store for requests captured by the stub server."""

    def __init__(self):
        self._lock = threading.Lock()
        self.requests: list[dict] = []

    def add(self, method: str, path: str, body: bytes, headers: dict) -> None:
        with self._lock:
            self.requests.append(
                {"method": method, "path": path, "body": body, "headers": dict(headers)}
            )

    def pop_all(self) -> list[dict]:
        with self._lock:
            out = list(self.requests)
            self.requests.clear()
            return out


_recorder = _RequestRecord()


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        _recorder.add("POST", self.path, body, dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass  # silence server output during tests


@pytest.fixture(scope="module")
def stub_server():
    """Start a local HTTP stub server; yield its base URL; stop after tests."""
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ---------------------------------------------------------------------------
# WebhookChannel tests
# ---------------------------------------------------------------------------


class TestWebhookChannel:
    def test_posts_json_payload(self, stub_server):
        """WebhookChannel POSTs a JSON body with required fields."""
        _recorder.pop_all()
        ch = WebhookChannel(f"{stub_server}/webhook-test")
        result = ch.send(event="cost_spike", severity="warning", message="Costs up 60%")

        assert result is True
        reqs = _recorder.pop_all()
        assert len(reqs) == 1
        req = reqs[0]
        assert req["method"] == "POST"
        assert req["headers"].get("Content-Type") == "application/json"
        body = json.loads(req["body"])
        assert body["event"] == "cost_spike"
        assert body["severity"] == "warning"
        assert body["message"] == "Costs up 60%"
        assert "timestamp" in body

    def test_extra_kwargs_included(self, stub_server):
        """Extra kwargs are serialised into the webhook payload."""
        _recorder.pop_all()
        ch = WebhookChannel(f"{stub_server}/webhook-kwargs")
        ch.send(event="test", severity="info", message="hello", rule_name="cache_drop")

        reqs = _recorder.pop_all()
        body = json.loads(reqs[0]["body"])
        assert body["rule_name"] == "cache_drop"

    def test_retry_on_failure(self):
        """WebhookChannel retries 3 times before returning False."""
        call_count = 0

        import urllib.error
        import urllib.request as _urlreq

        original_urlopen = _urlreq.urlopen

        def _failing_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise urllib.error.URLError("connection refused")

        ch = WebhookChannel("http://127.0.0.1:1")  # nothing listening here
        with patch("tokenpak.alerts.channels.webhook.urllib.request.urlopen", _failing_urlopen):
            with patch("tokenpak.alerts.channels.webhook.time.sleep"):  # skip actual sleep
                result = ch.send(event="test", severity="warning", message="retry test")

        assert result is False
        assert call_count == 3

    def test_returns_false_on_unreachable_url(self):
        """WebhookChannel returns False (not raises) when destination is unreachable."""
        ch = WebhookChannel("http://127.0.0.1:1")
        with patch("tokenpak.alerts.channels.webhook.time.sleep"):
            result = ch.send(event="test", severity="info", message="unreachable")
        assert result is False


# ---------------------------------------------------------------------------
# SlackChannel tests
# ---------------------------------------------------------------------------


class TestSlackChannel:
    def test_posts_slack_shaped_json(self, stub_server):
        """SlackChannel POSTs {text: ...} shaped body."""
        _recorder.pop_all()
        ch = SlackChannel(f"{stub_server}/slack-test")
        result = ch.send(event="error_spike", severity="critical", message="Errors at 15%")

        assert result is True
        reqs = _recorder.pop_all()
        assert len(reqs) == 1
        req = reqs[0]
        body = json.loads(req["body"])
        assert "text" in body
        assert isinstance(body["text"], str)
        # Slack text must contain the message content
        assert "Errors at 15%" in body["text"]

    def test_severity_emoji_critical(self, stub_server):
        """Critical severity uses red circle emoji."""
        _recorder.pop_all()
        ch = SlackChannel(f"{stub_server}/slack-emoji")
        ch.send(event="test", severity="critical", message="down")

        reqs = _recorder.pop_all()
        body = json.loads(reqs[0]["body"])
        assert "🔴" in body["text"]

    def test_severity_emoji_warning(self, stub_server):
        """Warning severity uses warning emoji."""
        _recorder.pop_all()
        ch = SlackChannel(f"{stub_server}/slack-warn")
        ch.send(event="test", severity="warning", message="watch out")

        reqs = _recorder.pop_all()
        body = json.loads(reqs[0]["body"])
        assert "⚠️" in body["text"]

    def test_retry_on_failure(self):
        """SlackChannel retries 3 times before returning False."""
        call_count = 0

        import urllib.error

        def _failing_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise urllib.error.URLError("connection refused")

        ch = SlackChannel("http://127.0.0.1:1")
        with patch("tokenpak.alerts.channels.slack.urllib.request.urlopen", _failing_urlopen):
            with patch("tokenpak.alerts.channels.slack.time.sleep"):
                result = ch.send(event="test", severity="info", message="retry test")

        assert result is False
        assert call_count == 3

    def test_content_type_header(self, stub_server):
        """SlackChannel sets Content-Type: application/json."""
        _recorder.pop_all()
        ch = SlackChannel(f"{stub_server}/slack-ct")
        ch.send(event="test", severity="info", message="header check")

        reqs = _recorder.pop_all()
        assert reqs[0]["headers"].get("Content-Type") == "application/json"


# ---------------------------------------------------------------------------
# Channel registry tests
# ---------------------------------------------------------------------------


class TestChannelRegistry:
    def test_dispatch_alert_fires_background_thread(self, stub_server):
        """dispatch_alert fires a daemon thread that posts to all channels."""
        _recorder.pop_all()
        cfg = [{"type": "webhook", "url": f"{stub_server}/registry-test"}]

        with patch(
            "tokenpak.alerts.channels._load_channel_configs", return_value=cfg
        ):
            dispatch_alert(event="budget_exceeded", severity="critical", message="Budget hit")
            time.sleep(0.5)  # let the thread deliver

        reqs = _recorder.pop_all()
        assert len(reqs) == 1
        body = json.loads(reqs[0]["body"])
        assert body["event"] == "budget_exceeded"

    def test_dispatch_alert_noop_when_no_channels(self):
        """dispatch_alert does nothing (no exception) when channel list is empty."""
        with patch("tokenpak.alerts.channels._load_channel_configs", return_value=[]):
            dispatch_alert(event="test", severity="info", message="noop")  # must not raise

    def test_dispatch_alert_unknown_channel_type_ignored(self):
        """Unknown channel types are skipped without raising."""
        cfg = [{"type": "pagerduty", "url": "https://example.com"}]
        with patch("tokenpak.alerts.channels._load_channel_configs", return_value=cfg):
            dispatch_alert(event="test", severity="info", message="skip unknown")
            time.sleep(0.2)

    def test_load_channel_configs_missing_file(self, tmp_path, monkeypatch):
        """_load_channel_configs returns [] when config.json does not exist."""
        monkeypatch.setenv("HOME", str(tmp_path))
        result = _load_channel_configs()
        assert result == []

    def test_load_channel_configs_reads_json(self, tmp_path, monkeypatch):
        """_load_channel_configs parses the channels list from config.json."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tokenpak_dir = tmp_path / ".tokenpak"
        tokenpak_dir.mkdir()
        config = {
            "alerts": {
                "channels": [
                    {"type": "webhook", "url": "https://example.com/hook"},
                    {"type": "slack", "webhook": "https://hooks.slack.com/abc"},
                ]
            }
        }
        (tokenpak_dir / "config.json").write_text(json.dumps(config))

        # Reload with patched HOME
        from pathlib import Path
        with patch("tokenpak.alerts.channels.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            mock_path.return_value = tmp_path / ".tokenpak" / "config.json"
            # Use direct file read instead
            pass

        # Re-test by calling _load_channel_configs with HOME set
        result = _load_channel_configs()
        assert len(result) == 2
        assert result[0]["type"] == "webhook"
        assert result[1]["type"] == "slack"


# ---------------------------------------------------------------------------
# Integration: check_alerts dispatches to channels
# ---------------------------------------------------------------------------


class TestCheckAlertsDispatch:
    def test_check_alerts_calls_dispatch_when_fired(self, stub_server):
        """When check_alerts() fires a rule, dispatch_alert is called."""
        from tokenpak._internal.alerts import check_alerts, AlertRule

        webhook_url = f"{stub_server}/check-alerts-dispatch"
        cfg = [{"type": "webhook", "url": webhook_url}]
        _recorder.pop_all()

        fired_rule = AlertRule(
            name="error_spike",
            condition="error_rate > 0.05",
            message="Error rate at {value:.1f}%",
            cooldown_minutes=0,
        )

        with patch("tokenpak._internal.alerts.load_config", return_value={"enabled": True, "rules": [
            {"name": "error_spike", "condition": "error_rate > 0.05",
             "message": "Error rate at {value:.1f}%", "cooldown_minutes": 0}
        ]}), \
             patch("tokenpak._internal.alerts.load_state", return_value={}), \
             patch("tokenpak._internal.alerts.save_state"), \
             patch("tokenpak._internal.alerts._get_proxy_stats", return_value={"requests": 100, "errors": 20}), \
             patch("tokenpak._internal.alerts._get_proxy_health", return_value={"status": "ok"}), \
             patch("tokenpak.alerts.channels._load_channel_configs", return_value=cfg):
            result = check_alerts()
            time.sleep(0.5)  # allow background thread to deliver

        assert len(result) == 1
        reqs = _recorder.pop_all()
        assert len(reqs) == 1
        body = json.loads(reqs[0]["body"])
        assert body["event"] == "error_spike"
