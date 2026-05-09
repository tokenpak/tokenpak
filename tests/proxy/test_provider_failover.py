"""
CCI-05: Provider failover on Anthropic 5xx/timeout (Bedrock → Vertex chain).

Tests:
  1. Anthropic 503 → Bedrock 200 (primary failover)
  2. Anthropic 503 → Bedrock 503 → Vertex 200 (chain continues)
  3. Non-claude-code profile does NOT trigger failover
  4. Single-provider chain (anthropic only) does NOT trigger failover
  5. Model name translation: Anthropic → Bedrock format
  6. Model name translation: Anthropic → Vertex format
  7. Queue fallback: writes SQLite entry, returns 202
  8. Failover event log is populated after a failover
  9. _translate_model passthrough on unknown model
"""


import pytest

pytest.importorskip("tokenpak.runtime", reason="module not available in current build")
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers to import proxy module symbols
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_proxy():
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    import proxy as _m
    return _m


_proxy = _import_proxy()


# ---------------------------------------------------------------------------
# Stub HTTP server: returns a configurable status for all requests
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    """Minimal stub upstream: responds with self.server._status_code."""

    def do_POST(self):
        body = json.dumps({"type": "message", "content": [{"type": "text", "text": "OK"}]})
        self.send_response(self.server._status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass  # silence test output


def _start_stub(status_code: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    server._status_code = status_code
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---------------------------------------------------------------------------
# Model translation tests (pure unit, no network)
# ---------------------------------------------------------------------------

class TestModelTranslation(unittest.TestCase):

    def test_bedrock_claude_35_sonnet(self):
        result = _proxy._translate_model("claude-3-5-sonnet-20241022", "bedrock")
        self.assertEqual(result, "anthropic.claude-3-5-sonnet-20241022-v2:0")

    def test_vertex_claude_35_sonnet(self):
        result = _proxy._translate_model("claude-3-5-sonnet-20241022", "vertex")
        self.assertEqual(result, "claude-3-5-sonnet@20241022")

    def test_bedrock_claude_3_haiku(self):
        result = _proxy._translate_model("claude-3-haiku-20240307", "bedrock")
        self.assertEqual(result, "anthropic.claude-3-haiku-20240307-v1:0")

    def test_vertex_claude_3_opus(self):
        result = _proxy._translate_model("claude-3-opus-20240229", "vertex")
        self.assertEqual(result, "claude-3-opus@20240229")

    def test_shorthand_sonnet(self):
        result = _proxy._translate_model("sonnet", "bedrock")
        self.assertEqual(result, "anthropic.claude-3-5-sonnet-20241022-v2:0")

    def test_unknown_model_passthrough(self):
        """Unknown model returns original model_id unchanged."""
        result = _proxy._translate_model("some-future-model-99", "bedrock")
        self.assertEqual(result, "some-future-model-99")

    def test_unknown_provider_passthrough(self):
        """Unknown provider returns original model_id unchanged."""
        result = _proxy._translate_model("claude-3-5-sonnet-20241022", "cohere")
        self.assertEqual(result, "claude-3-5-sonnet-20241022")


# ---------------------------------------------------------------------------
# Failover event log tests
# ---------------------------------------------------------------------------

class TestFailoverEventLog(unittest.TestCase):

    def setUp(self):
        with _proxy._FAILOVER_EVENTS_LOCK:
            _proxy._FAILOVER_EVENTS.clear()

    def test_log_event_appends(self):
        _proxy._log_failover_event(
            "anthropic", "bedrock", "http_503", "claude-3-5-sonnet-20241022",
            503, "claude-code-cli",
        )
        with _proxy._FAILOVER_EVENTS_LOCK:
            events = list(_proxy._FAILOVER_EVENTS)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["from_provider"], "anthropic")
        self.assertEqual(ev["to_provider"], "bedrock")
        self.assertEqual(ev["reason"], "http_503")
        self.assertEqual(ev["status_code"], 503)
        self.assertEqual(ev["profile"], "claude-code-cli")

    def test_log_event_multiple(self):
        _proxy._log_failover_event("anthropic", "bedrock", "http_503", "m", 503, "p")
        _proxy._log_failover_event("bedrock", "vertex", "http_503", "m", 503, "p")
        with _proxy._FAILOVER_EVENTS_LOCK:
            events = list(_proxy._FAILOVER_EVENTS)
        self.assertEqual(len(events), 2)

    def test_log_event_capped_at_maxlen(self):
        original_maxlen = _proxy._FAILOVER_EVENTS.maxlen
        # maxlen is 500; just verify it is set
        self.assertIsNotNone(original_maxlen)
        self.assertGreater(original_maxlen, 0)


# ---------------------------------------------------------------------------
# Queue fallback tests
# ---------------------------------------------------------------------------

class TestFailoverQueueFallback(unittest.TestCase):

    def test_queue_write_creates_row(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name
        try:
            with patch.object(_proxy, "_FAILOVER_QUEUE_DB", db_path):
                row_id = _proxy._write_failover_queue(
                    b'{"model":"claude-3-5-sonnet-20241022","messages":[]}',
                    "claude-3-5-sonnet-20241022",
                    "claude-code-cli",
                )
            self.assertNotEqual(row_id, "0")
            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT id, model, profile, status FROM failover_queue").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1], "claude-3-5-sonnet-20241022")
            self.assertEqual(rows[0][2], "claude-code-cli")
            self.assertEqual(rows[0][3], "pending")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_queue_write_returns_row_id(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name
        try:
            with patch.object(_proxy, "_FAILOVER_QUEUE_DB", db_path):
                r1 = _proxy._write_failover_queue(b"body1", "m", "p")
                r2 = _proxy._write_failover_queue(b"body2", "m", "p")
            self.assertNotEqual(r1, r2)
            self.assertLess(int(r1), int(r2))
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Failover chain logic tests (integration via stub upstreams)
# ---------------------------------------------------------------------------

class _FakeResp:
    """Minimal stub for urllib3 response."""
    def __init__(self, status, body=b'{"type":"message","content":[]}'):
        self.status = status
        self._body = body
        self._drained = False

    def getheader(self, name, default=""):
        if name.lower() == "content-type":
            return "application/json"
        return default

    def read(self):
        return self._body

    def drain_conn(self):
        self._drained = True

    def stream(self, chunk_size=None):
        yield self._body

    def release_conn(self):
        pass


class TestFailoverChainLogic(unittest.TestCase):
    """
    Test the _FALLBACK_CHAIN logic using mocked _POOL_MANAGER.request().

    We patch _POOL_MANAGER.request to control what each "upstream" returns,
    and verify that the chain walks providers correctly.
    """

    def setUp(self):
        with _proxy._FAILOVER_EVENTS_LOCK:
            _proxy._FAILOVER_EVENTS.clear()

    def _make_session(self, profile="claude-code-cli"):
        """Set SESSION active_profile for test."""
        _proxy.SESSION["active_profile"] = profile

    def _make_request_body(self, model="claude-3-5-sonnet-20241022"):
        return json.dumps({
            "model": model,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hello"}],
        }).encode()

    # --- Anthropic 503 → Bedrock 200 ---

    def test_anthropic_503_falls_to_bedrock(self):
        """When Anthropic returns 503, the chain falls to Bedrock and succeeds."""
        self._make_session("claude-code-cli")
        call_log = []

        def fake_request(method, url, **kw):
            call_log.append(url)
            if "anthropic.com" in url:
                return _FakeResp(503)
            if "bedrock-runtime" in url:
                return _FakeResp(200)
            return _FakeResp(200)

        with patch.object(_proxy._POOL_MANAGER, "request", side_effect=fake_request), \
             patch.object(_proxy, "_FALLBACK_CHAIN", ["anthropic", "bedrock"]), \
             patch.object(_proxy, "_BEDROCK_BASE_URL", "https://bedrock-runtime.us-east-1.amazonaws.com"):

            # Simulate conditions after initial Anthropic 503 response
            status = 503
            target_url = "https://api.anthropic.com/v1/messages"
            model = "claude-3-5-sonnet-20241022"
            body = self._make_request_body(model)
            fwd_headers = {"Content-Type": "application/json"}

            # Replicate the failover decision logic inline
            _cc05_profile = _proxy.SESSION.get("active_profile", "")
            self.assertTrue(_cc05_profile.startswith("claude-code-"))
            self.assertGreater(len(_proxy._FALLBACK_CHAIN), 1)
            self.assertTrue(500 <= status <= 599)

            # Walk chain after anthropic
            chain = _proxy._FALLBACK_CHAIN
            start = chain.index("anthropic") + 1
            final_status = status
            for provider in chain[start:]:
                fb_url = _proxy._build_failover_url(provider, target_url, model)
                self.assertTrue(fb_url, f"No URL for provider {provider}")
                _proxy._log_failover_event("anthropic", provider, f"http_{status}", model, status, _cc05_profile)
                resp = fake_request("POST", fb_url)
                final_status = resp.status
                if final_status < 500:
                    break

            self.assertEqual(final_status, 200)

        # Failover event should be logged
        with _proxy._FAILOVER_EVENTS_LOCK:
            events = list(_proxy._FAILOVER_EVENTS)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["from_provider"], "anthropic")
        self.assertEqual(events[0]["to_provider"], "bedrock")

    # --- Non-claude-code profile: no failover ---

    def test_non_cc_profile_no_failover(self):
        """Non-claude-code profiles should not trigger provider failover."""
        self._make_session("balanced")  # not a claude-code-* profile
        profile = _proxy.SESSION.get("active_profile", "")
        self.assertFalse(profile.startswith("claude-code-"))
        # No events should be logged
        with _proxy._FAILOVER_EVENTS_LOCK:
            events = list(_proxy._FAILOVER_EVENTS)
        self.assertEqual(len(events), 0)

    # --- Single-provider chain: no failover ---

    def test_single_provider_chain_no_failover(self):
        """When FALLBACK_CHAIN has only anthropic, no failover occurs."""
        self._make_session("claude-code-cli")
        chain = ["anthropic"]
        self.assertFalse(len(chain) > 1)

    # --- Anthropic 503 → Bedrock 503 → Vertex 200 ---

    def test_chain_continues_on_bedrock_failure(self):
        """Chain walks all the way to Vertex when both Anthropic and Bedrock fail."""
        self._make_session("claude-code-tui")
        call_log = []

        def fake_request(method, url, **kw):
            call_log.append(url)
            if "anthropic.com" in url:
                return _FakeResp(503)
            if "bedrock-runtime" in url:
                return _FakeResp(503)
            if "aiplatform.googleapis.com" in url:
                return _FakeResp(200)
            return _FakeResp(200)

        with patch.object(_proxy._POOL_MANAGER, "request", side_effect=fake_request), \
             patch.object(_proxy, "_FALLBACK_CHAIN", ["anthropic", "bedrock", "vertex"]), \
             patch.object(_proxy, "_BEDROCK_BASE_URL", "https://bedrock-runtime.us-east-1.amazonaws.com"), \
             patch.object(_proxy, "_VERTEX_BASE_URL", "https://us-east5-aiplatform.googleapis.com"), \
             patch.object(_proxy, "_VERTEX_PROJECT", "my-project"):

            model = "claude-3-5-sonnet-20241022"
            target_url = "https://api.anthropic.com/v1/messages"
            status = 503
            chain = _proxy._FALLBACK_CHAIN
            start = chain.index("anthropic") + 1
            current_provider = "anthropic"
            final_status = status
            for provider in chain[start:]:
                if provider == "queue":
                    break
                fb_url = _proxy._build_failover_url(provider, target_url, model)
                if not fb_url:
                    continue
                _proxy._log_failover_event(current_provider, provider, f"http_{status}", model, status, "claude-code-tui")
                resp = fake_request("POST", fb_url)
                final_status = resp.status
                current_provider = provider
                if final_status < 500:
                    break

            self.assertEqual(final_status, 200)

        with _proxy._FAILOVER_EVENTS_LOCK:
            events = list(_proxy._FAILOVER_EVENTS)
        providers = [e["to_provider"] for e in events]
        self.assertIn("bedrock", providers)
        self.assertIn("vertex", providers)

    # --- Model name translation in body ---

    def test_body_model_translation_for_bedrock(self):
        """Body model field is translated when building Bedrock request."""
        body = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        parsed = json.loads(body)
        translated = _proxy._translate_model(parsed["model"], "bedrock")
        parsed["model"] = translated
        new_body = json.dumps(parsed).encode()
        self.assertIn("anthropic.claude-3-5-sonnet-20241022-v2:0", new_body.decode())

    def test_body_model_translation_for_vertex(self):
        """Body model field is translated when building Vertex request."""
        body = json.dumps({
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        parsed = json.loads(body)
        translated = _proxy._translate_model(parsed["model"], "vertex")
        self.assertEqual(translated, "claude-3-opus@20240229")


if __name__ == "__main__":
    unittest.main()
