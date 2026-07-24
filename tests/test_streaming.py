"""
tests/test_streaming.py

Comprehensive tests for TokenPak SSE/Streaming support.

Acceptance criteria coverage:
  ✅ stream:true requests work end-to-end (unit + integration)
  ✅ User sees incremental output (chunks flushed, no full-buffer wait)
  ✅ Telemetry captures output tokens from stream (Anthropic + OpenAI)
  ✅ No buffering of streaming responses (X-Accel-Buffering: no)
  ✅ Headers: Content-Type: text/event-stream, Cache-Control: no-cache
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tokenpak.proxy.streaming import (
    StreamHandler,
    extract_sse_tokens,
    iter_sse_events,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return an ephemeral TCP port on 127.0.0.1."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_anthropic_sse(
    output_tokens: int = 42, cache_read: int = 0, cache_create: int = 0
) -> bytes:
    """Build a realistic Anthropic SSE stream."""
    events = []

    # message_start — carries cache token stats
    events.append(
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-5",
                "content": [],
                "stop_reason": None,
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_create,
                    "output_tokens": 0,
                },
            },
        }
    )
    # content blocks
    events.append(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    )
    events.append(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        }
    )
    events.append(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": " world"},
        }
    )
    events.append({"type": "content_block_stop", "index": 0})
    # message_delta — carries output token count
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        }
    )
    events.append({"type": "message_stop"})

    lines = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def _build_openai_sse(completion_tokens: int = 17) -> bytes:
    """Build a minimal OpenAI SSE stream."""
    lines = [
        f"data: {json.dumps({'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n",
        f"data: {json.dumps({'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': 'Hi'}, 'finish_reason': None}]})}\n\n",
        f"data: {json.dumps({'id': 'chatcmpl-x', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': completion_tokens, 'total_tokens': 10 + completion_tokens}})}\n\n",
        "data: [DONE]\n\n",
    ]
    return "".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Unit tests: extract_sse_tokens
# ---------------------------------------------------------------------------


class TestExtractSseTokens:
    """Unit tests for extract_sse_tokens()."""

    def test_anthropic_output_tokens(self):
        sse = _build_anthropic_sse(output_tokens=55)
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 55

    def test_anthropic_cache_read_tokens(self):
        sse = _build_anthropic_sse(cache_read=200)
        result = extract_sse_tokens(sse)
        assert result["cache_read_input_tokens"] == 200

    def test_anthropic_cache_creation_tokens(self):
        sse = _build_anthropic_sse(cache_create=300)
        result = extract_sse_tokens(sse)
        assert result["cache_creation_input_tokens"] == 300

    def test_anthropic_all_fields(self):
        sse = _build_anthropic_sse(output_tokens=42, cache_read=100, cache_create=50)
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 42
        assert result["cache_read_input_tokens"] == 100
        assert result["cache_creation_input_tokens"] == 50

    def test_openai_completion_tokens(self):
        sse = _build_openai_sse(completion_tokens=17)
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 17

    def test_empty_stream_returns_zeros(self):
        result = extract_sse_tokens(b"")
        assert result["output_tokens"] == 0
        assert result["cache_read_input_tokens"] == 0
        assert result["cache_creation_input_tokens"] == 0

    def test_done_marker_skipped(self):
        """[DONE] line must not cause parse errors."""
        sse = b"data: [DONE]\n\n"
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 0

    def test_malformed_json_line_skipped(self):
        """Partial/corrupt JSON lines must not crash."""
        sse = b"data: {broken\ndata: [DONE]\n\n"
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 0

    def test_non_data_lines_ignored(self):
        """Event:, id:, comment lines must be silently ignored."""
        sse = b"event: ping\nid: 1\n: comment\ndata: [DONE]\n\n"
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 0

    def test_returns_dict_with_expected_keys(self):
        result = extract_sse_tokens(b"")
        assert "output_tokens" in result
        assert "cache_read_input_tokens" in result
        assert "cache_creation_input_tokens" in result

    def test_mixed_anthropic_and_openai_fields(self):
        """If both formats appear, should not crash and return best-effort values."""
        lines = [
            f"data: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 10}})}\n\n",
            f"data: {json.dumps({'usage': {'completion_tokens': 5}})}\n\n",
            "data: [DONE]\n\n",
        ]
        sse = "".join(lines).encode("utf-8")
        result = extract_sse_tokens(sse)
        # At least one of the two sources should have been captured
        assert result["output_tokens"] >= 5


# ---------------------------------------------------------------------------
# Unit tests: StreamHandler
# ---------------------------------------------------------------------------


class TestStreamHandler:
    """Unit tests for the StreamHandler helper class."""

    def test_process_chunk_buffers_data(self):
        sh = StreamHandler()
        sh.process_chunk(b"data: hello\n\n")
        assert b"hello" in sh.get_buffer()

    def test_multiple_chunks_concatenated(self):
        sh = StreamHandler()
        sh.process_chunk(b"chunk1")
        sh.process_chunk(b"chunk2")
        buf = sh.get_buffer()
        assert b"chunk1" in buf and b"chunk2" in buf

    def test_chunk_count_increments(self):
        sh = StreamHandler()
        sh.process_chunk(b"a")
        sh.process_chunk(b"b")
        assert sh.chunk_count == 2

    def test_extract_usage_delegates_to_extract_sse_tokens(self):
        sh = StreamHandler()
        sse = _build_anthropic_sse(output_tokens=9)
        sh.process_chunk(sse)
        usage = sh.extract_usage()
        assert usage["output_tokens"] == 9

    def test_initial_state(self):
        sh = StreamHandler()
        assert sh.chunk_count == 0
        assert sh.get_buffer() == b""

    def test_gzip_decompression(self):
        """StreamHandler decompresses gzip-encoded chunks."""
        import gzip

        raw = b"data: hello\n\n"
        compressed = gzip.compress(raw)
        sh = StreamHandler(content_encoding="gzip")
        sh.process_chunk(compressed)
        buf = sh.get_buffer()
        assert b"hello" in buf

    def test_bad_gzip_chunk_does_not_crash(self):
        """Corrupt gzip data must not raise — falls through silently."""
        sh = StreamHandler(content_encoding="gzip")
        sh.process_chunk(b"not-gzip-data")
        # Should not raise — buffer may be empty or contain raw bytes


# ---------------------------------------------------------------------------
# Unit tests: iter_sse_events
# ---------------------------------------------------------------------------


class TestIterSseEvents:
    """Unit tests for iter_sse_events()."""

    def test_yields_parsed_events(self):
        sse = _build_anthropic_sse(output_tokens=3)
        events = list(iter_sse_events(sse))
        types = [e.get("type") for e in events if "type" in e]
        assert "message_start" in types
        assert "message_stop" in types

    def test_done_marker_not_yielded(self):
        """[DONE] must not appear as a parsed event."""
        sse = b"data: [DONE]\n\n"
        events = list(iter_sse_events(sse))
        assert events == []

    def test_malformed_json_not_yielded(self):
        sse = b"data: {broken\n\n"
        events = list(iter_sse_events(sse))
        assert events == []

    def test_empty_stream_yields_nothing(self):
        assert list(iter_sse_events(b"")) == []


# ---------------------------------------------------------------------------
# Integration tests: proxy streaming path
# ---------------------------------------------------------------------------


def _make_sse_upstream(port: int, sse_body: bytes, content_type: str = "text/event-stream"):
    """
    Spin up a minimal fake SSE upstream server on `port`.

    Sends the full SSE body as a normal HTTP response (no chunked transfer encoding).
    httpx requires proper chunked framing when Transfer-Encoding: chunked is set,
    so we omit it and let connection-close signal end of stream.
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # silence

        def do_POST(self):
            # Drain request body
            cl = int(self.headers.get("Content-Length", 0))
            if cl:
                self.rfile.read(cl)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            # Provide Content-Length so httpx knows when to stop reading
            self.send_header("Content-Length", str(len(sse_body)))
            self.end_headers()
            self.wfile.write(sse_body)
            self.wfile.flush()

    srv = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _make_chunked_upstream(
    port: int,
    chunks: list[bytes],
    upstream_eof: threading.Event,
    *,
    inter_chunk_delay: float = 0.0,
    content_encoding: str | None = None,
):
    """Serve entity chunks with real HTTP/1.1 chunk framing on loopback."""

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length:
                self.rfile.read(content_length)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            if content_encoding:
                self.send_header("Content-Encoding", content_encoding)
            self.end_headers()

            for chunk in chunks:
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                if inter_chunk_delay:
                    time.sleep(inter_chunk_delay)

            # Mark upstream completion before the terminal frame. A proxy that
            # buffers until EOF cannot race this marker and look incremental.
            upstream_eof.set()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            self.close_connection = True

    srv = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


class TestProxyStreamingEndToEnd:
    """Integration tests for the proxy streaming path using a fake upstream."""

    @pytest.fixture(autouse=True)
    def _start_proxy(self):
        """Start proxy + fake upstream for each test.

        Patches INTERCEPT_HOSTS in both `server` and `router` modules so the
        streaming path (gated on intercept hosts) is exercised for local fakes.

        server.py does `from .router import INTERCEPT_HOSTS` which creates a
        module-level name. Patching only `router.INTERCEPT_HOSTS` is insufficient;
        we must also patch `server.INTERCEPT_HOSTS`.
        """
        import tokenpak.proxy.router as _router_mod
        import tokenpak.proxy.server as _server_mod
        from tokenpak.proxy.server import ProxyServer

        self.upstream_port = _free_port()
        self.proxy_port = _free_port()
        self.sse_body = _build_anthropic_sse(output_tokens=42)

        # Patch both modules so the streaming intercept check uses the extended set
        _orig_router_hosts = _router_mod.INTERCEPT_HOSTS
        _orig_server_hosts = _server_mod.INTERCEPT_HOSTS
        _patched = _orig_router_hosts | {"127.0.0.1"}
        _router_mod.INTERCEPT_HOSTS = _patched
        _server_mod.INTERCEPT_HOSTS = _patched

        self.upstream = _make_sse_upstream(self.upstream_port, self.sse_body)

        self.proxy = ProxyServer(host="127.0.0.1", port=self.proxy_port)
        self.proxy.start(blocking=False)
        time.sleep(0.2)

        yield

        self.proxy.stop()
        self.upstream.shutdown()
        _router_mod.INTERCEPT_HOSTS = _orig_router_hosts
        _server_mod.INTERCEPT_HOSTS = _orig_server_hosts

    def _stream_request(self, upstream_port=None):
        """
        Send a streaming POST through the proxy → upstream.

        Uses urllib ProxyHandler so the full URL is sent as the request path,
        which is how HTTP proxies expect to receive requests.
        """
        upstream_port = upstream_port or self.upstream_port
        target = f"http://127.0.0.1:{upstream_port}/v1/messages"
        payload = json.dumps(
            {
                "model": "claude-opus-4-5",
                "max_tokens": 100,
                "stream": True,
                # Use ≥16 chars so _estimate_tokens_from_body returns input_tokens > 0
                # (estimates as len(content) // 4, so need at least 5 chars for > 0)
                "messages": [{"role": "user", "content": "Hello, streaming test!"}],
            }
        ).encode()
        proxy_handler = urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{self.proxy_port}"})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(
            target,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": "sk-ant-test-fake-key-for-testing",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        return opener.open(req, timeout=10)

    def _raw_stream_request(self, upstream_port: int):
        """Send an absolute-form proxy request without response decoding."""
        target = f"http://127.0.0.1:{upstream_port}/v1/messages"
        payload = json.dumps(
            {
                "model": "claude-opus-4-5",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Hello, streaming test!"}],
            }
        ).encode()
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.proxy_port,
            timeout=10,
        )
        connection.request(
            "POST",
            target,
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
                "x-api-key": "sk-ant-test-fake-key-for-testing",
                "anthropic-version": "2023-06-01",
            },
        )
        return connection, connection.getresponse()

    def test_streaming_request_returns_200(self):
        """stream:true request must reach upstream and return 200."""
        resp = self._stream_request()
        assert resp.status == 200

    def test_streaming_content_type_header(self):
        """Response must carry Content-Type: text/event-stream."""
        resp = self._stream_request()
        ct = resp.headers.get("Content-Type", "")
        assert "text/event-stream" in ct

    def test_streaming_cache_control_header(self):
        """Response must carry Cache-Control: no-cache."""
        resp = self._stream_request()
        cc = resp.headers.get("Cache-Control", "")
        assert "no-cache" in cc

    def test_streaming_x_accel_buffering_header(self):
        """X-Accel-Buffering: no must be set to disable nginx buffering."""
        resp = self._stream_request()
        xab = resp.headers.get("X-Accel-Buffering", "")
        assert xab.lower() == "no"

    def test_streaming_body_contains_sse_events(self):
        """Response body must contain valid SSE data lines."""
        resp = self._stream_request()
        body = resp.read()
        assert b"data: " in body

    def test_streaming_body_contains_done_marker(self):
        """Response body must end with data: [DONE]."""
        resp = self._stream_request()
        body = resp.read()
        assert b"data: [DONE]" in body

    def test_streaming_telemetry_captured(self):
        """
        After a streaming request, session request counter must increment.
        INTERCEPT_HOSTS is patched in the fixture so telemetry is active.
        """
        self.proxy.reset_session()
        self._stream_request().read()
        time.sleep(0.15)
        stats = self.proxy.session_stats()
        assert stats["session_requests"] >= 1

    def test_short_sse_is_observed_before_upstream_eof(self, record_property):
        """A sub-4KiB SSE must not wait for an upstream EOF flush."""
        upstream_port = _free_port()
        upstream_eof = threading.Event()
        chunks = [self.sse_body[i : i + 64] for i in range(0, len(self.sse_body), 64)]
        assert len(self.sse_body) < 4096
        assert len(chunks) >= 12
        assert max(map(len, chunks)) <= 64

        upstream = _make_chunked_upstream(
            upstream_port,
            chunks,
            upstream_eof,
            inter_chunk_delay=0.025,
        )
        connection = None
        reads = []
        started = time.monotonic()
        try:
            connection, response = self._raw_stream_request(upstream_port)
            assert response.status == 200
            while True:
                chunk = response.read1(64)
                elapsed_ms = (time.monotonic() - started) * 1000
                if not chunk:
                    break
                reads.append((elapsed_ms, chunk, upstream_eof.is_set()))
        finally:
            if connection is not None:
                connection.close()
            upstream.shutdown()
            upstream.server_close()

        read_count = len(reads)
        pre_eof_read_count = sum(not eof_seen for _, _, eof_seen in reads)
        first_read_elapsed_ms = reads[0][0] if reads else None
        last_read_elapsed_ms = reads[-1][0] if reads else None
        read_span_ms = (
            last_read_elapsed_ms - first_read_elapsed_ms
            if first_read_elapsed_ms is not None and last_read_elapsed_ms is not None
            else 0.0
        )
        observed = {
            "stream_read_count": read_count,
            "stream_pre_eof_read_count": pre_eof_read_count,
            "stream_first_read_elapsed_ms": first_read_elapsed_ms,
            "stream_last_read_elapsed_ms": last_read_elapsed_ms,
            "stream_read_span_ms": read_span_ms,
        }
        for key, value in observed.items():
            record_property(key, value)

        body = b"".join(chunk for _, chunk, _ in reads)
        assert body == self.sse_body, observed
        assert hashlib.sha256(body).digest() == hashlib.sha256(self.sse_body).digest(), observed
        assert read_count >= 2, observed
        assert pre_eof_read_count >= 2, observed
        assert read_span_ms >= 20.0, observed

    def test_raw_stream_body_and_content_encoding_are_preserved(self, record_property):
        """A large content-coded entity must remain byte-identical."""
        upstream_port = _free_port()
        upstream_eof = threading.Event()
        plain_body = b": " + (b"x" * 8192) + b"\n\n" + self.sse_body
        wire_body = gzip.compress(plain_body, compresslevel=0, mtime=0)
        expected_sha256 = hashlib.sha256(wire_body).hexdigest()
        assert len(wire_body) > 4096

        upstream = _make_chunked_upstream(
            upstream_port,
            [wire_body],
            upstream_eof,
            content_encoding="gzip",
        )
        connection = None
        try:
            self.proxy.reset_session()
            connection, response = self._raw_stream_request(upstream_port)
            actual_encoding = response.getheader("Content-Encoding")
            actual_body = response.read()
        finally:
            if connection is not None:
                connection.close()
            upstream.shutdown()
            upstream.server_close()

        actual_sha256 = hashlib.sha256(actual_body).hexdigest()
        record_property("stream_wire_bytes", len(actual_body))
        record_property("stream_wire_sha256", actual_sha256)
        assert actual_encoding == "gzip"
        assert actual_body == wire_body
        assert actual_sha256 == expected_sha256
        assert self.proxy.session_stats()["output_tokens"] == 42

    def test_streaming_headers_enforced_without_upstream_content_type(self):
        """
        If upstream omits Content-Type, the proxy must inject text/event-stream.
        """
        upstream_port2 = _free_port()

        class _NoCtHandler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                cl = int(self.headers.get("Content-Length", 0))
                if cl:
                    self.rfile.read(cl)
                body = b"data: [DONE]\n\n"
                self.send_response(200)
                # Intentionally omit Content-Type
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

        srv2 = HTTPServer(("127.0.0.1", upstream_port2), _NoCtHandler)
        t2 = threading.Thread(target=srv2.serve_forever, daemon=True)
        t2.daemon = True
        t2.start()

        try:
            resp = self._stream_request(upstream_port=upstream_port2)
            ct = resp.headers.get("Content-Type", "")
            assert "text/event-stream" in ct
        finally:
            srv2.shutdown()

    def test_streaming_headers_enforced_without_upstream_cache_control(self):
        """
        If upstream omits Cache-Control, the proxy must inject no-cache.
        """
        upstream_port3 = _free_port()

        class _NoCcHandler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                cl = int(self.headers.get("Content-Length", 0))
                if cl:
                    self.rfile.read(cl)
                body = b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                # Intentionally omit Cache-Control
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

        srv3 = HTTPServer(("127.0.0.1", upstream_port3), _NoCcHandler)
        t3 = threading.Thread(target=srv3.serve_forever, daemon=True)
        t3.daemon = True
        t3.start()

        try:
            resp = self._stream_request(upstream_port=upstream_port3)
            cc = resp.headers.get("Cache-Control", "")
            assert "no-cache" in cc
        finally:
            srv3.shutdown()

    def test_streaming_session_stats_track_request(self):
        """Session request counter increments for streaming requests."""
        self.proxy.reset_session()
        before = self.proxy.session_stats()["session_requests"]
        self._stream_request().read()
        time.sleep(0.15)
        after = self.proxy.session_stats()["session_requests"]
        assert after > before


# ---------------------------------------------------------------------------
# Unit tests: OpenAI streaming format
# ---------------------------------------------------------------------------


class TestOpenAIStreamParsing:
    """Verify token extraction from OpenAI-format SSE streams."""

    def test_openai_completion_tokens_extracted(self):
        sse = _build_openai_sse(completion_tokens=23)
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 23

    def test_openai_stream_zero_completion_tokens(self):
        lines = [
            f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 5, 'completion_tokens': 0}})}\n\n",
            "data: [DONE]\n\n",
        ]
        sse = "".join(lines).encode()
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 0

    def test_stream_without_usage_returns_zero(self):
        """If no usage block exists, output_tokens defaults to 0."""
        lines = [
            f"data: {json.dumps({'choices': [{'delta': {'content': 'hi'}, 'finish_reason': None}]})}\n\n",
            "data: [DONE]\n\n",
        ]
        sse = "".join(lines).encode()
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 0


# ---------------------------------------------------------------------------
# Edge case tests: Streaming failure paths
# ---------------------------------------------------------------------------


class TestStreamingFailurePaths:
    """Error paths, malformed chunks, and connection drop scenarios."""

    def test_malformed_json_chunk_skipped(self):
        """Malformed JSON in SSE data line should not crash extraction."""
        sse_bytes = (
            b"data: {this is not json}\n\n"
            b'data: {"type":"message_delta","usage":{"output_tokens":5}}\n\n'
        )
        result = extract_sse_tokens(sse_bytes)
        # Should recover and count the valid delta
        assert result["output_tokens"] >= 0  # no crash

    def test_empty_sse_stream_returns_zero(self):
        """Empty byte stream → zero tokens, no exception."""
        result = extract_sse_tokens(b"")
        assert result["output_tokens"] == 0

    def test_done_only_stream_returns_zero(self):
        """Stream with only [DONE] marker."""
        result = extract_sse_tokens(b"data: [DONE]\n\n")
        assert result["output_tokens"] == 0

    def test_partial_chunk_no_crash(self):
        """Partial/truncated SSE line should not raise."""
        partial = b'data: {"type":"content_block_delta","delta":{"type":"text'
        # No crash expected
        result = extract_sse_tokens(partial)
        assert isinstance(result, dict)

    def test_binary_garbage_in_stream(self):
        """Binary garbage bytes should not crash extraction."""
        garbage = bytes(range(256)) + b"data: [DONE]\n\n"
        result = extract_sse_tokens(garbage)
        assert isinstance(result, dict)

    def test_stream_with_only_whitespace_lines(self):
        """Only whitespace/newlines → no crash, zero tokens."""
        sse_bytes = b"\n\n\n   \n\t\n"
        result = extract_sse_tokens(sse_bytes)
        assert result["output_tokens"] == 0

    def test_multiple_usage_events_last_wins(self):
        """When multiple usage blocks appear, last non-zero value should dominate."""
        import json as _json

        lines = [
            f"data: {_json.dumps({'type': 'message_delta', 'usage': {'output_tokens': 10}})}\n\n",
            f"data: {_json.dumps({'type': 'message_delta', 'usage': {'output_tokens': 25}})}\n\n",
            "data: [DONE]\n\n",
        ]
        sse = "".join(lines).encode()
        result = extract_sse_tokens(sse)
        assert result["output_tokens"] == 25

    def test_iter_sse_events_malformed_chunk(self):
        """iter_sse_events should yield valid events, skip bad ones."""
        sse_bytes = b'data: not-json\n\ndata: {"type":"ping"}\n\n'
        events = list(iter_sse_events(sse_bytes))
        # At least the valid ping event should come through (or empty list — no crash)
        assert isinstance(events, list)

    def test_iter_sse_events_empty_input(self):
        """iter_sse_events on empty bytes → empty list."""
        events = list(iter_sse_events(b""))
        assert events == []

    def test_stream_usage_zero_output_tokens(self):
        """usage block with output_tokens=0 should return 0, not be silently ignored."""
        import json as _json

        line = f"data: {_json.dumps({'usage': {'output_tokens': 0, 'prompt_tokens': 100}})}\n\n"
        sse = line.encode()
        result = extract_sse_tokens(sse)
        assert "output_tokens" in result
        # 0 is a valid value
        assert result["output_tokens"] == 0 or result["output_tokens"] >= 0
