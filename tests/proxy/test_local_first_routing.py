"""
CCI-06: Local-first routing for low-stakes turns (Ollama opt-in).

Tests:
  1. Feature flag OFF — request passes through to Anthropic unchanged
  2. Profile not claude-code-* — fallthrough
  3. Tools block present — fallthrough (local models can't do tool use)
  4. Input tokens over limit — fallthrough
  5. Ollama error (connection refused) — fallthrough
  6. Ollama short response (under-delivery) — fallthrough
  7. Ollama normal response (non-streaming) — local route, Anthropic JSON shape
  8. Ollama normal response (streaming) — local route, SSE shape, parseable
  9. Translation: system string → OpenAI system message
 10. Translation: system block array → flattened string
 11. _cci06_translate_to_ollama: tools field absent in translated payload
"""


import pytest

pytest.importorskip("tokenpak.runtime", reason="module not available in current build")
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Bootstrap: add repo root to sys.path
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_proxy():
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    os.environ.setdefault("TOKENPAK_NO_THREADS", "1")
    import proxy as _m
    return _m


_proxy = _import_proxy()


# ---------------------------------------------------------------------------
# Stub Ollama server
# ---------------------------------------------------------------------------

def _make_ollama_response(content: str = "Hello from Ollama!") -> bytes:
    """Minimal OpenAI-compatible chat completion response from Ollama."""
    return json.dumps({
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "model": "qwen2.5-coder:7b",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    }).encode()


class _StubOllamaHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl > 0 else b""
        if hasattr(self.server, "_received_bodies"):
            self.server._received_bodies.append(body)

        status = getattr(self.server, "_status_code", 200)
        resp = getattr(self.server, "_response_body", _make_ollama_response())

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args):
        pass


def _start_stub_ollama(status_code=200, response_body=None) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _StubOllamaHandler)
    server._status_code = status_code
    server._response_body = response_body or _make_ollama_response()
    server._received_bodies = []
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---------------------------------------------------------------------------
# Minimal handler harness for unit-testing _local_first_check
# ---------------------------------------------------------------------------

class _MockHeaders(dict):
    """Case-insensitive dict for request headers."""
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class _FakeHandler:
    """Minimal stand-in for ForwardProxyHandler that lets us call _local_first_check."""

    def __init__(self):
        self.headers = _MockHeaders()
        self._response_status = None
        self._response_headers = {}
        self._response_body = b""
        self._wfile = BytesIO()

    @property
    def wfile(self):
        return self._wfile

    def send_response(self, status):
        self._response_status = status

    def send_header(self, key, value):
        self._response_headers[key.lower()] = value

    def end_headers(self):
        pass

    # Bind the real _local_first_check from the proxy module
    _local_first_check = _proxy.ForwardProxyHandler._local_first_check


def _make_request(messages=None, system=None, tools=None, stream=False) -> bytes:
    payload: dict = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": messages or [{"role": "user", "content": "what is 2+2?"}],
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    if stream:
        payload["stream"] = True
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# Unit tests: translation helpers
# ---------------------------------------------------------------------------

class TestCci06TranslateToOllama(unittest.TestCase):

    def _translate(self, body: bytes) -> dict:
        result = _proxy._cci06_translate_to_ollama(body)
        self.assertIsNotNone(result)
        return result

    def test_system_string_becomes_system_message(self):
        body = _make_request(system="You are a helpful assistant.")
        payload = self._translate(body)
        self.assertTrue(any(
            m["role"] == "system" and "You are a helpful assistant." in m["content"]
            for m in payload["messages"]
        ))

    def test_system_block_array_flattened(self):
        system = [
            {"type": "text", "text": "First block."},
            {"type": "text", "text": "Second block."},
        ]
        body = _make_request(system=system)
        payload = self._translate(body)
        sys_msg = next(m for m in payload["messages"] if m["role"] == "system")
        self.assertIn("First block.", sys_msg["content"])
        self.assertIn("Second block.", sys_msg["content"])

    def test_user_message_preserved(self):
        body = _make_request(messages=[{"role": "user", "content": "hello"}])
        payload = self._translate(body)
        user_msgs = [m for m in payload["messages"] if m["role"] == "user"]
        self.assertTrue(any("hello" in m["content"] for m in user_msgs))

    def test_model_replaced_with_local_model(self):
        body = _make_request()
        payload = self._translate(body)
        self.assertEqual(payload["model"], _proxy.LOCAL_FIRST_MODEL)

    def test_no_tools_in_translated_payload(self):
        """tools must NOT appear in the Ollama payload (tool use routes to Anthropic)."""
        body = _make_request()  # no tools
        payload = self._translate(body)
        self.assertNotIn("tools", payload)

    def test_max_tokens_carried_over(self):
        body = json.dumps({
            "model": "claude-sonnet-4-6", "max_tokens": 512,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        payload = self._translate(body)
        self.assertEqual(payload["max_tokens"], 512)

    def test_invalid_json_returns_none(self):
        result = _proxy._cci06_translate_to_ollama(b"not valid json{{{")
        self.assertIsNone(result)

    def test_stream_false_in_payload(self):
        body = _make_request()
        payload = self._translate(body)
        self.assertFalse(payload["stream"])


class TestCci06Wrappers(unittest.TestCase):

    def test_wrap_to_json_shape(self):
        resp = _proxy._cci06_wrap_to_json("Hello!", "qwen2.5-coder:7b", 10, 5)
        data = json.loads(resp)
        self.assertEqual(data["type"], "message")
        self.assertEqual(data["role"], "assistant")
        self.assertEqual(data["stop_reason"], "end_turn")
        self.assertEqual(data["content"][0]["text"], "Hello!")
        self.assertEqual(data["usage"]["input_tokens"], 10)
        self.assertEqual(data["usage"]["output_tokens"], 5)

    def test_wrap_to_sse_contains_all_events(self):
        sse = _proxy._cci06_wrap_to_sse("World!", "qwen2.5-coder:7b", 8, 3).decode()
        self.assertIn("event: message_start", sse)
        self.assertIn("event: content_block_start", sse)
        self.assertIn("event: content_block_delta", sse)
        self.assertIn("event: content_block_stop", sse)
        self.assertIn("event: message_delta", sse)
        self.assertIn("event: message_stop", sse)

    def test_wrap_to_sse_has_response_text(self):
        sse = _proxy._cci06_wrap_to_sse("Cheese!", "qwen2.5-coder:7b", 5, 2).decode()
        # The text should appear in the content_block_delta data line
        data_lines = [
            line[len("data: "):] for line in sse.splitlines()
            if line.startswith("data: ") and "content_block_delta" in line
        ]
        self.assertTrue(any("Cheese!" in d for d in data_lines))

    def test_wrap_to_sse_each_data_line_is_valid_json(self):
        sse = _proxy._cci06_wrap_to_sse("Hi", "qwen2.5-coder:7b", 3, 1).decode()
        for line in sse.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
                self.assertIn("type", payload)


# ---------------------------------------------------------------------------
# Integration tests: _local_first_check with stub Ollama
# ---------------------------------------------------------------------------

class TestLocalFirstCheck(unittest.TestCase):

    def setUp(self):
        # Reset SESSION counters
        _proxy.SESSION["local_first_routed"] = 0
        _proxy.SESSION["local_first_fallthrough"] = 0
        _proxy.SESSION["active_profile"] = "claude-code-cli"

    def _make_handler(self):
        return _FakeHandler()

    def _patch_upstream(self, url: str):
        """Return a context manager that points LOCAL_FIRST to a given Ollama base URL."""
        return patch.object(_proxy, "OLLAMA_UPSTREAM", url)

    def test_feature_flag_off_returns_false(self):
        handler = self._make_handler()
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", False):
            result = handler._local_first_check(_make_request())
        self.assertFalse(result)

    def test_non_claude_code_profile_fallthrough(self):
        handler = self._make_handler()
        _proxy.SESSION["active_profile"] = "balanced"
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True):
            result = handler._local_first_check(_make_request())
        self.assertFalse(result)
        self.assertEqual(_proxy.SESSION["local_first_fallthrough"], 0)

    def test_tools_block_triggers_fallthrough(self):
        handler = self._make_handler()
        tools = [{"name": "read_file", "description": "reads", "input_schema": {"type": "object"}}]
        body = _make_request(tools=tools)
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True):
            result = handler._local_first_check(body)
        self.assertFalse(result)
        self.assertEqual(_proxy.SESSION["local_first_fallthrough"], 1)

    def test_token_over_limit_triggers_fallthrough(self):
        handler = self._make_handler()
        # Build a request that will count over the limit
        long_message = "word " * 300  # ~300 words ≈ 375+ tokens
        body = _make_request(messages=[{"role": "user", "content": long_message}])
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True), \
             patch.object(_proxy, "LOCAL_FIRST_MAX_INPUT_TOKENS", 50):
            result = handler._local_first_check(body)
        self.assertFalse(result)
        self.assertEqual(_proxy.SESSION["local_first_fallthrough"], 1)

    def test_ollama_error_triggers_fallthrough(self):
        """Stub Ollama returns 500 → fallthrough."""
        stub = _start_stub_ollama(status_code=500, response_body=b'{"error":"fail"}')
        port = stub.server_address[1]
        stub_url = f"http://127.0.0.1:{port}"

        handler = self._make_handler()
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True), \
             self._patch_upstream(stub_url):
            result = handler._local_first_check(_make_request())

        self.assertFalse(result)
        self.assertEqual(_proxy.SESSION["local_first_fallthrough"], 1)
        stub.shutdown()

    def test_ollama_short_response_triggers_fallthrough(self):
        """Stub Ollama returns fewer tokens than min threshold → fallthrough."""
        short_text = "Hi"  # ~0-1 tokens — well under default 50
        stub = _start_stub_ollama(response_body=_make_ollama_response(short_text))
        port = stub.server_address[1]
        stub_url = f"http://127.0.0.1:{port}"

        handler = self._make_handler()
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True), \
             self._patch_upstream(stub_url), \
             patch.object(_proxy, "LOCAL_FIRST_MIN_RESPONSE_TOKENS", 50):
            result = handler._local_first_check(_make_request())

        self.assertFalse(result)
        self.assertEqual(_proxy.SESSION["local_first_fallthrough"], 1)
        stub.shutdown()

    def test_ollama_normal_response_non_streaming(self):
        """Stub Ollama returns adequate response, stream=False → handled, Anthropic JSON."""
        long_text = "Four. " * 30  # ~30 * 4-char words * 4 ≈ 30 tokens
        stub = _start_stub_ollama(response_body=_make_ollama_response(long_text))
        port = stub.server_address[1]
        stub_url = f"http://127.0.0.1:{port}"

        handler = self._make_handler()
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True), \
             self._patch_upstream(stub_url), \
             patch.object(_proxy, "LOCAL_FIRST_MIN_RESPONSE_TOKENS", 5):
            result = handler._local_first_check(_make_request(stream=False))

        self.assertTrue(result)
        self.assertEqual(_proxy.SESSION["local_first_routed"], 1)
        self.assertEqual(handler._response_status, 200)
        self.assertEqual(handler._response_headers.get("content-type"), "application/json")

        resp_json = json.loads(handler.wfile.getvalue())
        self.assertEqual(resp_json["type"], "message")
        self.assertEqual(resp_json["role"], "assistant")
        self.assertEqual(resp_json["stop_reason"], "end_turn")
        self.assertIn(long_text.strip(), resp_json["content"][0]["text"])

        stub.shutdown()

    def test_ollama_normal_response_streaming(self):
        """Stub Ollama returns adequate response, stream=True → handled, SSE format."""
        long_text = "The answer is four. " * 5
        stub = _start_stub_ollama(response_body=_make_ollama_response(long_text))
        port = stub.server_address[1]
        stub_url = f"http://127.0.0.1:{port}"

        handler = self._make_handler()
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True), \
             self._patch_upstream(stub_url), \
             patch.object(_proxy, "LOCAL_FIRST_MIN_RESPONSE_TOKENS", 5):
            result = handler._local_first_check(_make_request(stream=True))

        self.assertTrue(result)
        self.assertEqual(_proxy.SESSION["local_first_routed"], 1)
        self.assertEqual(handler._response_headers.get("content-type"), "text/event-stream")

        sse_text = handler.wfile.getvalue().decode()
        self.assertIn("event: message_start", sse_text)
        self.assertIn("event: message_stop", sse_text)

        stub.shutdown()

    def test_x_tokenpak_local_first_header_set(self):
        """Successful local route must include X-TokenPak-Local-First: routed header."""
        long_text = "Plenty of text here to satisfy the minimum. " * 3
        stub = _start_stub_ollama(response_body=_make_ollama_response(long_text))
        port = stub.server_address[1]
        stub_url = f"http://127.0.0.1:{port}"

        handler = self._make_handler()
        with patch.object(_proxy, "LOCAL_FIRST_ENABLED", True), \
             self._patch_upstream(stub_url), \
             patch.object(_proxy, "LOCAL_FIRST_MIN_RESPONSE_TOKENS", 5):
            handler._local_first_check(_make_request())

        self.assertEqual(
            handler._response_headers.get("x-tokenpak-local-first"), "routed"
        )
        stub.shutdown()


if __name__ == "__main__":
    unittest.main()
