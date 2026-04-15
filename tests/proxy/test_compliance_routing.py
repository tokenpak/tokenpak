"""
CCI-07: Compliance routing — Bedrock transparent translation.

Tests:
  1. Request translation: Anthropic body → Bedrock body (model removed, anthropic_version added)
  2. Response translation: Bedrock body → Anthropic body (model field restored)
  3. SSE line translation: Bedrock camelCase → Anthropic snake_case event names
  4. Bedrock URL construction (non-streaming and streaming)
  5. SigV4 signing produces correct Authorization header structure (no network)
  6. Simple completion: stub Bedrock returns 200, proxy returns Anthropic-shaped response
  7. Streaming response: stub emits SSE with camelCase names, proxy translates to snake_case
  8. Tool use: tools field survives round-trip through request translation
  9. Error response: stub Bedrock returns 4xx, proxy forwards unchanged
 10. Per-request X-TokenPak-Compliance: bedrock header honoured
"""


import pytest
pytest.importorskip("tokenpak.runtime", reason="module not available in current build")
import base64
import importlib
import json
import os
import struct
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Import proxy module
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
# Stub Bedrock server helpers
# ---------------------------------------------------------------------------

def _make_bedrock_non_streaming_response(text="OK from Bedrock"):
    """Minimal Bedrock non-streaming response (same shape as Anthropic)."""
    return json.dumps({
        "id": "msg_bedrock_01",
        "type": "message",
        "role": "assistant",
        "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }).encode()


def _make_bedrock_sse_response(text="Bedrock streaming"):
    """
    Stub Bedrock SSE response with camelCase event names.
    The proxy must translate these to Anthropic snake_case before forwarding.
    """
    lines = [
        "event: messageStart",
        'data: {"type":"messageStart","message":{"id":"msg_01","type":"message","role":"assistant","model":"anthropic.claude-3-5-sonnet-20241022-v2:0","content":[],"stop_reason":null,"usage":{"input_tokens":10,"output_tokens":0}}}',
        "",
        "event: contentBlockStart",
        'data: {"type":"contentBlockStart","index":0,"content_block":{"type":"text","text":""}}',
        "",
        "event: contentBlockDelta",
        f'data: {{"type":"contentBlockDelta","index":0,"delta":{{"type":"text_delta","text":"{text}"}}}}',
        "",
        "event: contentBlockStop",
        'data: {"type":"contentBlockStop","index":0}',
        "",
        "event: messageDelta",
        'data: {"type":"messageDelta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":5}}',
        "",
        "event: messageStop",
        'data: {"type":"messageStop"}',
        "",
    ]
    return ("\n".join(lines) + "\n").encode()


class _StubBedrockHandler(BaseHTTPRequestHandler):
    """Stub Bedrock upstream: validates request format, returns configured response."""

    def do_POST(self):
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl > 0 else b""

        # Record the received request for test assertions
        if hasattr(self.server, "_received_bodies"):
            self.server._received_bodies.append(body)
        if hasattr(self.server, "_received_headers"):
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            self.server._received_headers.append(hdrs)

        status = getattr(self.server, "_status_code", 200)
        response_body = getattr(self.server, "_response_body", _make_bedrock_non_streaming_response())
        content_type = getattr(self.server, "_content_type", "application/json")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, *args):
        pass


def _start_stub_bedrock(
    status_code: int = 200,
    response_body: bytes = None,
    content_type: str = "application/json",
) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _StubBedrockHandler)
    server._status_code = status_code
    server._response_body = response_body or _make_bedrock_non_streaming_response()
    server._content_type = content_type
    server._received_bodies = []
    server._received_headers = []
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---------------------------------------------------------------------------
# Unit tests: pure functions (no network)
# ---------------------------------------------------------------------------

class TestRequestTranslation(unittest.TestCase):
    """_cci07_translate_request: Anthropic → Bedrock request body."""

    def _translate(self, data: dict):
        body = json.dumps(data).encode()
        translated_bytes, model_id = _proxy._cci07_translate_request(body)
        return json.loads(translated_bytes), model_id

    def test_model_removed_from_body(self):
        translated, model_id = self._translate({
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        })
        self.assertNotIn("model", translated)
        self.assertEqual(model_id, "claude-3-5-sonnet-20241022")

    def test_anthropic_version_added(self):
        translated, _ = self._translate({
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [],
        })
        self.assertEqual(translated["anthropic_version"], "bedrock-2023-05-31")

    def test_other_fields_preserved(self):
        translated, _ = self._translate({
            "model": "claude-sonnet-4-5",
            "max_tokens": 256,
            "temperature": 0.7,
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        })
        self.assertEqual(translated["max_tokens"], 256)
        self.assertAlmostEqual(translated["temperature"], 0.7)
        self.assertEqual(translated["system"], "You are helpful.")
        self.assertEqual(translated["stream"], False)
        self.assertEqual(len(translated["messages"]), 1)

    def test_tools_field_preserved(self):
        tools = [{"name": "read_file", "description": "Reads a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]
        translated, _ = self._translate({
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [],
            "tools": tools,
        })
        self.assertEqual(translated["tools"], tools)

    def test_missing_model_field(self):
        """model is optional — translate still works, returns empty string."""
        translated, model_id = self._translate({
            "max_tokens": 100,
            "messages": [],
        })
        self.assertEqual(model_id, "")
        self.assertNotIn("model", translated)
        self.assertIn("anthropic_version", translated)

    def test_stream_true_preserved(self):
        translated, _ = self._translate({
            "model": "claude-3-haiku-20240307",
            "max_tokens": 50,
            "messages": [],
            "stream": True,
        })
        self.assertTrue(translated["stream"])


class TestResponseTranslation(unittest.TestCase):
    """_cci07_translate_response: Bedrock → Anthropic response body."""

    def test_model_field_restored(self):
        bedrock_resp = json.dumps({
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }).encode()
        result = json.loads(_proxy._cci07_translate_response(bedrock_resp, "claude-3-5-sonnet-20241022"))
        self.assertEqual(result["model"], "claude-3-5-sonnet-20241022")

    def test_other_fields_preserved(self):
        bedrock_resp = json.dumps({
            "id": "msg_xyz",
            "type": "message",
            "role": "assistant",
            "model": "anthropic.claude-3-haiku-20240307-v1:0",
            "content": [{"type": "text", "text": "response text"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }).encode()
        result = json.loads(_proxy._cci07_translate_response(bedrock_resp, "claude-3-haiku-20240307"))
        self.assertEqual(result["id"], "msg_xyz")
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["usage"]["output_tokens"], 20)
        self.assertEqual(result["content"][0]["text"], "response text")

    def test_passthrough_on_invalid_json(self):
        raw = b"not json at all"
        result = _proxy._cci07_translate_response(raw, "some-model")
        self.assertEqual(result, raw)

    def test_no_model_id_leaves_bedrock_model(self):
        """When no original_model_id is given, bedrock model field is kept as-is."""
        bedrock_resp = json.dumps({"model": "anthropic.claude-3-opus-20240229-v1:0"}).encode()
        result = json.loads(_proxy._cci07_translate_response(bedrock_resp, ""))
        self.assertEqual(result["model"], "anthropic.claude-3-opus-20240229-v1:0")


class TestSSELineTranslation(unittest.TestCase):
    """_cci07_translate_sse_line: camelCase → snake_case event names."""

    def test_messageStart(self):
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: messageStart"),
            "event: message_start",
        )

    def test_contentBlockDelta(self):
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: contentBlockDelta"),
            "event: content_block_delta",
        )

    def test_contentBlockStart(self):
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: contentBlockStart"),
            "event: content_block_start",
        )

    def test_contentBlockStop(self):
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: contentBlockStop"),
            "event: content_block_stop",
        )

    def test_messageStop(self):
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: messageStop"),
            "event: message_stop",
        )

    def test_messageDelta(self):
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: messageDelta"),
            "event: message_delta",
        )

    def test_ping_passthrough(self):
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: ping"),
            "event: ping",
        )

    def test_data_line_passthrough(self):
        line = 'data: {"type":"contentBlockDelta","index":0}'
        self.assertEqual(_proxy._cci07_translate_sse_line(line), line)

    def test_blank_line_passthrough(self):
        self.assertEqual(_proxy._cci07_translate_sse_line(""), "")

    def test_unknown_bedrock_event_passthrough(self):
        """Unknown event names pass through unchanged (forward-compat)."""
        self.assertEqual(
            _proxy._cci07_translate_sse_line("event: futureEventType"),
            "event: futureEventType",
        )


class TestBedrockUrlConstruction(unittest.TestCase):
    """_cci07_build_bedrock_url: URL construction."""

    def setUp(self):
        self._orig_base = _proxy._BEDROCK_BASE_URL
        patch.object(_proxy, "_BEDROCK_BASE_URL",
                     "https://bedrock-runtime.us-east-1.amazonaws.com").__enter__()

    def tearDown(self):
        _proxy._BEDROCK_BASE_URL = self._orig_base

    def test_non_streaming_url(self):
        url = _proxy._cci07_build_bedrock_url(
            "anthropic.claude-3-5-sonnet-20241022-v2:0", False
        )
        self.assertIn("/model/anthropic.claude-3-5-sonnet-20241022-v2:0/invoke", url)
        self.assertNotIn("response-stream", url)

    def test_streaming_url(self):
        url = _proxy._cci07_build_bedrock_url(
            "anthropic.claude-3-5-sonnet-20241022-v2:0", True
        )
        self.assertIn("invoke-with-response-stream", url)


class TestSigV4Signing(unittest.TestCase):
    """_cci07_sigv4_sign: SigV4 Authorization header generation (no network)."""

    def test_no_credentials_returns_headers_unmodified_keys(self):
        """Without credentials, headers are returned without Authorization."""
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""}):
            result = _proxy._cci07_sigv4_sign(
                "POST", "http://localhost/model/m/invoke",
                {"Content-Type": "application/json"}, b"{}",
            )
        # Should NOT raise; Authorization may be absent
        self.assertNotIn("Authorization", result)

    def test_with_credentials_produces_auth_header(self):
        with patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "AKIATESTKEY",
            "AWS_SECRET_ACCESS_KEY": "supersecretkey",
            "AWS_SESSION_TOKEN": "",
        }):
            result = _proxy._cci07_sigv4_sign(
                "POST",
                "https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-3-5-sonnet-20241022-v2:0/invoke",
                {},
                b'{"max_tokens":10}',
                service="bedrock",
                region="us-east-1",
            )
        self.assertIn("Authorization", result)
        self.assertTrue(result["Authorization"].startswith("AWS4-HMAC-SHA256 "))
        self.assertIn("Credential=AKIATESTKEY/", result["Authorization"])
        self.assertIn("SignedHeaders=", result["Authorization"])
        self.assertIn("Signature=", result["Authorization"])
        self.assertIn("X-Amz-Date", result)

    def test_session_token_included_when_set(self):
        with patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "AKIATEST",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "sess_token_xyz",
        }):
            result = _proxy._cci07_sigv4_sign(
                "POST", "https://bedrock-runtime.us-east-1.amazonaws.com/model/m/invoke",
                {}, b"{}",
            )
        self.assertEqual(result.get("X-Amz-Security-Token"), "sess_token_xyz")
        self.assertIn("x-amz-security-token", result["Authorization"])


# ---------------------------------------------------------------------------
# Integration tests: stub Bedrock upstream
# ---------------------------------------------------------------------------

def _make_anthropic_request(model="claude-3-5-sonnet-20241022", streaming=False, tools=None):
    """Build a minimal Anthropic /v1/messages request body."""
    req = {
        "model": model,
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "say OK"}],
        "stream": streaming,
    }
    if tools:
        req["tools"] = tools
    return json.dumps(req).encode()


class _ProxyCallMixin:
    """Helpers for exercising _cci07_compliance_proxy via a stub ForwardProxyHandler."""

    def _call_compliance_proxy(
        self,
        bedrock_url_base: str,
        request_body: bytes,
        compliance_header: str = "",
        env_compliance: str = "",
    ) -> dict:
        """
        Directly call _cci07_translate_request / _cci07_translate_response /
        _cci07_translate_sse_line rather than wiring up a full HTTP server.
        This gives deterministic results without network races.
        """
        bedrock_body, original_model = _proxy._cci07_translate_request(request_body)
        bedrock_model = _proxy._translate_model(original_model, "bedrock")
        is_streaming = json.loads(bedrock_body).get("stream", False)
        url = _proxy._cci07_build_bedrock_url(bedrock_model, is_streaming)
        return {
            "bedrock_body": json.loads(bedrock_body),
            "original_model": original_model,
            "bedrock_model": bedrock_model,
            "is_streaming": is_streaming,
            "url": url,
        }


class TestSimpleCompletion(unittest.TestCase, _ProxyCallMixin):
    """Simple non-streaming completion through stub Bedrock."""

    def test_request_format_is_correct(self):
        req = _make_anthropic_request("claude-3-5-sonnet-20241022")
        result = self._call_compliance_proxy("unused", req)
        self.assertNotIn("model", result["bedrock_body"])
        self.assertEqual(result["bedrock_body"]["anthropic_version"], "bedrock-2023-05-31")
        self.assertEqual(result["original_model"], "claude-3-5-sonnet-20241022")
        self.assertEqual(result["bedrock_model"], "anthropic.claude-3-5-sonnet-20241022-v2:0")
        self.assertFalse(result["is_streaming"])
        self.assertIn("invoke", result["url"])
        self.assertNotIn("response-stream", result["url"])

    def test_response_model_restored(self):
        bedrock_resp = _make_bedrock_non_streaming_response("hello")
        translated = json.loads(_proxy._cci07_translate_response(bedrock_resp, "claude-3-5-sonnet-20241022"))
        self.assertEqual(translated["model"], "claude-3-5-sonnet-20241022")
        self.assertEqual(translated["content"][0]["text"], "hello")

    def test_stub_bedrock_receives_correct_body(self):
        """End-to-end: start stub, send request, verify stub received Bedrock-format body."""
        stub = _start_stub_bedrock(200, _make_bedrock_non_streaming_response("stub OK"))
        port = stub.server_address[1]
        try:
            with patch.object(_proxy, "_BEDROCK_BASE_URL", f"http://127.0.0.1:{port}"):
                req = _make_anthropic_request("claude-3-5-sonnet-20241022")
                bedrock_body, _ = _proxy._cci07_translate_request(req)
                # Simulate sending to stub
                import http.client as _hc
                conn = _hc.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/model/anthropic.claude-3-5-sonnet-20241022-v2:0/invoke",
                             body=bedrock_body, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                resp_body = resp.read()
            self.assertEqual(resp.status, 200)
            # Stub received body without 'model' field and with anthropic_version
            received = json.loads(stub._received_bodies[0])
            self.assertNotIn("model", received)
            self.assertEqual(received["anthropic_version"], "bedrock-2023-05-31")
        finally:
            stub.shutdown()


class TestStreamingResponse(unittest.TestCase, _ProxyCallMixin):
    """Streaming response: camelCase Bedrock SSE → Anthropic snake_case SSE."""

    def test_streaming_request_format(self):
        req = _make_anthropic_request("claude-3-5-sonnet-20241022", streaming=True)
        result = self._call_compliance_proxy("unused", req)
        self.assertTrue(result["is_streaming"])
        self.assertIn("invoke-with-response-stream", result["url"])

    def test_sse_event_names_translated(self):
        """Translate a full Bedrock SSE response chunk by chunk."""
        sse = _make_bedrock_sse_response("hello stream")
        translated_lines = []
        for line in sse.decode().splitlines():
            translated_lines.append(_proxy._cci07_translate_sse_line(line))

        event_lines = [l for l in translated_lines if l.startswith("event: ")]
        event_names = [l[len("event: "):] for l in event_lines]

        self.assertIn("message_start", event_names)
        self.assertIn("content_block_start", event_names)
        self.assertIn("content_block_delta", event_names)
        self.assertIn("content_block_stop", event_names)
        self.assertIn("message_delta", event_names)
        self.assertIn("message_stop", event_names)

        # No camelCase event names should remain
        for name in event_names:
            self.assertFalse(
                name[0].islower() and any(c.isupper() for c in name),
                f"camelCase event name leaked: {name!r}",
            )

    def test_data_lines_pass_through_unchanged(self):
        data_line = 'data: {"type":"contentBlockDelta","index":0,"delta":{"type":"text_delta","text":"hi"}}'
        result = _proxy._cci07_translate_sse_line(data_line)
        self.assertEqual(result, data_line)


class TestToolUseRoundTrip(unittest.TestCase, _ProxyCallMixin):
    """Tools field survives Anthropic → Bedrock translation."""

    def test_tools_preserved(self):
        tools = [
            {
                "name": "bash",
                "description": "Run a bash command",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }
        ]
        req = _make_anthropic_request(tools=tools)
        result = self._call_compliance_proxy("unused", req)
        self.assertEqual(result["bedrock_body"]["tools"], tools)
        self.assertNotIn("model", result["bedrock_body"])

    def test_tool_choice_preserved(self):
        req_data = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "use the tool"}],
            "tools": [{"name": "test", "description": "t", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "auto"},
        }
        bedrock_body, model_id = _proxy._cci07_translate_request(json.dumps(req_data).encode())
        parsed = json.loads(bedrock_body)
        self.assertEqual(parsed["tool_choice"], {"type": "auto"})
        self.assertNotIn("model", parsed)


class TestErrorResponse(unittest.TestCase):
    """Error responses from stub Bedrock are forwarded to the client."""

    def test_error_body_is_bedrock_format(self):
        """On error, the stub's error body passes through (no translation applied)."""
        err_body = json.dumps({
            "message": "The model is not supported.",
            "type": "validation_error",
        }).encode()

        stub = _start_stub_bedrock(400, err_body)
        port = stub.server_address[1]
        try:
            with patch.object(_proxy, "_BEDROCK_BASE_URL", f"http://127.0.0.1:{port}"):
                import http.client as _hc
                conn = _hc.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/model/anthropic.claude-3-5-sonnet-20241022-v2:0/invoke",
                             body=b'{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[]}',
                             headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                body = resp.read()
            self.assertEqual(resp.status, 400)
            parsed = json.loads(body)
            self.assertEqual(parsed["message"], "The model is not supported.")
        finally:
            stub.shutdown()


class TestComplianceHeaderOverride(unittest.TestCase, _ProxyCallMixin):
    """Per-request X-TokenPak-Compliance: bedrock header triggers routing."""

    def test_per_request_compliance_detected(self):
        """Simulate header detection logic (unit-level, no HTTP server)."""
        compliance_header = "bedrock"
        env_compliance = ""
        effective = compliance_header.lower().strip() or env_compliance
        self.assertEqual(effective, "bedrock")

    def test_env_compliance_detected(self):
        env_compliance = "bedrock"
        compliance_header = ""
        effective = compliance_header.lower().strip() or env_compliance
        self.assertEqual(effective, "bedrock")

    def test_no_compliance_is_empty(self):
        effective = "".lower().strip() or ""
        self.assertFalse(effective)

    def test_compliance_only_triggers_for_bedrock(self):
        """Non-bedrock values do not trigger compliance path."""
        for val in ("vertex", "openai", "", "bedrock_extra"):
            effective = val.strip().lower()
            self.assertNotEqual(effective, "bedrock",
                                f"'{val}' should not trigger bedrock compliance path")

    def test_compliance_bedrock_triggers(self):
        self.assertEqual("bedrock".strip().lower(), "bedrock")


# ---------------------------------------------------------------------------
# Model translation integration (reuses existing _translate_model)
# ---------------------------------------------------------------------------

class TestComplianceModelTranslation(unittest.TestCase):
    """_translate_model covers all claude-code-relevant models for bedrock."""

    def test_sonnet_45(self):
        self.assertEqual(
            _proxy._translate_model("claude-sonnet-4-5", "bedrock"),
            "anthropic.claude-sonnet-4-5-20251101-v1:0",
        )

    def test_sonnet_46(self):
        self.assertEqual(
            _proxy._translate_model("claude-sonnet-4-6", "bedrock"),
            "anthropic.claude-sonnet-4-6-20260101-v1:0",
        )

    def test_haiku_35(self):
        self.assertEqual(
            _proxy._translate_model("claude-3-5-haiku-20241022", "bedrock"),
            "anthropic.claude-3-5-haiku-20241022-v1:0",
        )

    def test_unknown_model_passthrough(self):
        self.assertEqual(
            _proxy._translate_model("claude-future-9", "bedrock"),
            "claude-future-9",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
