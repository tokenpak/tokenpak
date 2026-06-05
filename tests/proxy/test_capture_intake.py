# SPDX-License-Identifier: Apache-2.0
"""Tests for the opt-in response-egress capture intake (OSS side).

Covers the structural invariants of `tokenpak/proxy/capture_intake.py`:

* two-factor gate (operator flag AND per-request opt-in header),
* read-only response-text extraction (Anthropic + OpenAI shapes),
* daemon-shaped payload construction (the CaptureEvent wire contract),
* loopback forward to a stand-in daemon (round-trip), and
* fail-silent / inert-by-default behavior.

No Pro code is imported — the OSS proxy constructs the documented JSON shape.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tokenpak.proxy import capture_intake as ci

# ── Gate: two-factor (operator flag AND opt-in header) ──────────────────────

class _Hdrs:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, k, default=None):
        return self._m.get(k, default)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ci.ENABLE_ENV, raising=False)
    assert ci.should_attempt(_Hdrs({ci.OPTIN_HEADER: "opt-in"})) is False


def test_enabled_requires_header(monkeypatch):
    monkeypatch.setenv(ci.ENABLE_ENV, "1")
    assert ci.should_attempt(_Hdrs({})) is False
    assert ci.should_attempt(_Hdrs({ci.OPTIN_HEADER: "nope"})) is False
    assert ci.should_attempt(_Hdrs({ci.OPTIN_HEADER: "opt-in"})) is True
    # case/space-insensitive on the value
    assert ci.should_attempt(_Hdrs({ci.OPTIN_HEADER: "  Opt-In "})) is True


def test_header_without_flag_is_inert(monkeypatch):
    monkeypatch.setenv(ci.ENABLE_ENV, "0")
    assert ci.should_attempt(_Hdrs({ci.OPTIN_HEADER: "opt-in"})) is False


# ── extract_response_text: read-only, fail-soft ────────────────────────────

def test_extract_anthropic_text():
    body = json.dumps({
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "tool_use", "name": "x"},
            {"type": "text", "text": "world"},
        ]
    }).encode()
    assert ci.extract_response_text(body, "claude-x") == "Hello world"


def test_extract_openai_text():
    body = json.dumps({"choices": [{"message": {"content": "hi there"}}]})
    assert ci.extract_response_text(body) == "hi there"


def test_extract_returns_none_on_garbage():
    assert ci.extract_response_text(b"not json") is None
    assert ci.extract_response_text(b"") is None
    assert ci.extract_response_text(None) is None
    assert ci.extract_response_text(json.dumps({"content": []})) is None
    assert ci.extract_response_text(json.dumps({"foo": "bar"})) is None


# ── build_capture_payload: the daemon wire contract ────────────────────────

def test_payload_required_fields():
    p = ci.build_capture_payload(
        "some response", model="claude-x", session_id="sess-1", platform="anthropic",
        captured_at_iso="2026-06-02T00:00:00+00:00",
    )
    # Required keys per CaptureEvent.from_dict
    for k in ("source", "content", "captured_at", "platform"):
        assert k in p
    assert p["source"] == "llm_response"
    assert p["content"] == "some response"
    assert p["platform"] == "anthropic"
    assert p["session_id"] == "sess-1"
    assert p["metadata"]["via"] == "proxy-capture-intake"
    assert p["metadata"]["model"] == "claude-x"
    # must be JSON-serializable (it is sent over the wire)
    json.dumps(p)


def test_payload_omits_empty_session():
    p = ci.build_capture_payload("x")
    assert "session_id" not in p
    assert p["platform"] == "proxy"


# ── Stand-in daemon for loopback round-trip ────────────────────────────────

class _StandInDaemon:
    """Minimal HTTP server impersonating the Pro daemon's /pak/v1/promote."""

    def __init__(self):
        self.received: list[dict] = []
        self.requests: list[dict] = []  # full metadata: path + headers + body
        srv_self = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {"_unparseable": True}
                srv_self.received.append(body)
                srv_self.requests.append({
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                })
                payload = json.dumps({"promoted": True, "pak_id": "test-pak", "path": self.path}).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def active_daemon(monkeypatch, tmp_path):
    """Spin up a stand-in daemon and point the probe at it."""
    import tokenpak.licensing.daemon_probe as probe

    with _StandInDaemon() as d:
        info = tmp_path / "daemon.sock-info"
        info.write_text(json.dumps({"port": d.port, "tip_version": "1.0", "started_at": 0}))
        monkeypatch.setattr(probe, "detect_daemon_state", lambda *a, **k: "active")
        monkeypatch.setattr(probe, "sock_info_path", lambda *a, **k: info)
        yield d


def test_forward_to_daemon_round_trip(active_daemon):
    payload = ci.build_capture_payload("captured text", model="m", platform="anthropic")
    result = ci.forward_to_daemon(payload)
    assert result is not None
    assert result["status"] == 201
    assert result["body"]["promoted"] is True
    assert len(active_daemon.received) == 1
    got = active_daemon.received[0]
    assert got["source"] == "llm_response"
    assert got["content"] == "captured text"


def test_forward_noop_when_daemon_unavailable(monkeypatch):
    import tokenpak.licensing.daemon_probe as probe
    monkeypatch.setattr(probe, "detect_daemon_state", lambda *a, **k: "unavailable")
    assert ci.forward_to_daemon({"source": "llm_response", "content": "x"}) is None


# ── _run_capture: gate → extract → build → forward ─────────────────────────

def test_run_capture_end_to_end(monkeypatch, active_daemon):
    monkeypatch.setenv(ci.ENABLE_ENV, "1")
    body = json.dumps({"content": [{"type": "text", "text": "answer"}]}).encode()
    result = ci._run_capture(_Hdrs({ci.OPTIN_HEADER: "opt-in"}), body, "claude-x")
    assert result is not None and result["status"] == 201
    assert active_daemon.received[0]["content"] == "answer"


def test_no_license_channel_egress(monkeypatch, active_daemon):
    """Privacy/egress invariant: captured content egresses ONLY to the daemon
    capture channel (`/pak/v1/promote`) over loopback — never to a
    license-validation endpoint — and the caller's auth/license headers are
    NOT forwarded to the daemon.
    """
    monkeypatch.setenv(ci.ENABLE_ENV, "1")
    body = json.dumps({"content": [{"type": "text", "text": "secret answer"}]}).encode()
    hdrs = _Hdrs({
        ci.OPTIN_HEADER: "opt-in",
        "Authorization": "Bearer should-not-leak",
        "X-TPK-Key": "should-not-leak",
    })
    result = ci._run_capture(hdrs, body, "claude-x")
    assert result is not None and result["status"] == 201

    # Exactly one egress, and it is the capture channel — not a license path.
    assert len(active_daemon.requests) == 1
    req = active_daemon.requests[0]
    assert req["path"] == "/pak/v1/promote"
    assert "license" not in req["path"]
    assert req["path"] != "/v1/features"

    # The caller's auth / license headers were NOT forwarded to the daemon.
    assert "authorization" not in req["headers"]
    assert "x-tpk-key" not in req["headers"]

    # Positive control: the captured content reached the capture channel.
    assert req["body"]["content"] == "secret answer"
    assert req["body"]["source"] == "llm_response"


def test_run_capture_inert_when_disabled(monkeypatch, active_daemon):
    monkeypatch.delenv(ci.ENABLE_ENV, raising=False)
    body = json.dumps({"content": [{"type": "text", "text": "answer"}]}).encode()
    assert ci._run_capture(_Hdrs({ci.OPTIN_HEADER: "opt-in"}), body, "m") is None
    assert active_daemon.received == []  # nothing forwarded → OSS captured nothing


def test_run_capture_noop_without_text(monkeypatch, active_daemon):
    monkeypatch.setenv(ci.ENABLE_ENV, "1")
    assert ci._run_capture(_Hdrs({ci.OPTIN_HEADER: "opt-in"}), b"not json", "m") is None
    assert active_daemon.received == []


# ── maybe_forward_capture: non-blocking, fail-silent ───────────────────────

def test_maybe_forward_inert_when_disabled(monkeypatch):
    monkeypatch.delenv(ci.ENABLE_ENV, raising=False)
    # Must not raise and must not spawn work.
    assert ci.maybe_forward_capture(_Hdrs({ci.OPTIN_HEADER: "opt-in"}), b"{}", "m") is None


def test_maybe_forward_spawns_and_forwards(monkeypatch, active_daemon):
    monkeypatch.setenv(ci.ENABLE_ENV, "1")
    body = json.dumps({"content": [{"type": "text", "text": "async answer"}]}).encode()
    ci.maybe_forward_capture(_Hdrs({ci.OPTIN_HEADER: "opt-in"}), body, "m")
    # Join the spawned capture thread deterministically.
    for t in threading.enumerate():
        if t.name == "tpk-capture-intake":
            t.join(timeout=5)
    assert len(active_daemon.received) == 1
    assert active_daemon.received[0]["content"] == "async answer"
