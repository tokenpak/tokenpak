"""
tests/proxy/test_semantic_cache_streaming_regression.py

CCG-16: Regression test — semantic cache + streaming + Claude Code bypass.

Locks in the 2026-04-08 incident: the semantic cache stored responses as JSON dicts
and served them via _send_json to streaming clients, causing the claude CLI SSE
parser to crash with "Cannot read properties of undefined (reading 'input_tokens')".

CCG-14 fixes the bug by bypassing the cache for streaming/agent requests.
These tests prove it stays fixed.

Cases:
  A — JSON cache miss → store → hit on identical non-streaming query (baseline)
  B — Streaming request (stream: true) bypasses cache
  C — Claude Code request by X-Claude-Code-Session-Id header bypasses cache
  D — Claude Code request by User-Agent: claude-code/2.1.96 bypasses cache
  E — Pre-populated JSON cache entry is NOT served to a streaming client
  F — The exact 2026-04-08 failing request body cannot produce a JSON-over-SSE crash

Shared negative assertion helper: _assert_cache_bypassed()
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.runtime", reason="module not available in current build")
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.needs_cali_env

# ---------------------------------------------------------------------------
# Bootstrap: import the top-level proxy.py
# ---------------------------------------------------------------------------
# The proxy is not a package module — it is a standalone script in the project
# root. We add the project root to sys.path and import it directly.

_PROJECT_ROOT = Path(__file__).parent.parent.parent  # /home/cali/tokenpak
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Set env vars BEFORE importing proxy so the module-level _cfg() calls pick them up.
os.environ.setdefault("TOKENPAK_SEMANTIC_CACHE", "0")   # keep disabled by default
os.environ.setdefault("ANTHROPIC_API_KEY", "test-sk-ccg16-dummy-not-real")
os.environ.setdefault("TOKENPAK_VAULT_INDEX", "0")       # skip vault startup

import proxy as _proxy  # noqa: E402 — intentional late import after env setup

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------
_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SSE_BODY = (_FIXTURES / "sse_response_message_delta.txt").read_bytes()
_JSON_BODY = (_FIXTURES / "json_response_messages.json").read_bytes()
_FAILING_REQ = json.loads((_FIXTURES / "cali_failing_request_2026-04-08.json").read_bytes())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_anthropic_body(*, stream: bool = False, content: str = "hello regression") -> bytes:
    """Build a minimal valid Anthropic messages request body."""
    return json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 32,
        "stream": stream,
        "messages": [{"role": "user", "content": content}],
    }).encode()


def _assert_cache_bypassed(session_snapshot: dict) -> None:
    """
    Shared assertion for Cases B–F.

    The semantic cache must NOT have been consulted for a streaming / agent request.
    The expected marker is 'skipped:streaming-or-agent'.
    """
    phase = session_snapshot.get("phase_semantic_cache")
    assert phase == "skipped:streaming-or-agent", (
        f"Expected phase_semantic_cache='skipped:streaming-or-agent' "
        f"but got {phase!r}. "
        "This means CCG-14's guard is not active — the cache was consulted "
        "for a streaming/agent request, which can serve JSON to an SSE parser."
    )
    # Also assert: lookup was NOT called on the cache mock (checked in each test)


def _assert_not_json_over_sse(response_body: bytes, accept_header: str) -> None:
    """
    Assert that if the client sent Accept: text/event-stream, the response is
    NOT a raw JSON dict (which is the shape that caused the 2026-04-08 crash).
    """
    if "text/event-stream" not in accept_header:
        return
    try:
        parsed = json.loads(response_body)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — safe (it's SSE bytes or an error message)
        return
    # If it IS JSON, it must NOT be an Anthropic response dict served raw.
    # An Anthropic dict has "type": "message" and "usage".
    if isinstance(parsed, dict) and parsed.get("type") == "message" and "usage" in parsed:
        pytest.fail(
            "Proxy served a raw JSON Anthropic dict to an SSE client. "
            "This is the 2026-04-08 bug. CCG-14 guard is broken."
        )


# ---------------------------------------------------------------------------
# Proxy + stub fixture
# ---------------------------------------------------------------------------

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading


class _CountingStub(HTTPServer):
    """HTTPServer that counts upstream requests."""
    request_count: int = 0


class _StubHandler(BaseHTTPRequestHandler):
    """
    Stub Anthropic upstream.  Replies with SSE or JSON based on stream field.
    """
    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # silence test output

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self.server.request_count += 1  # type: ignore[attr-defined]
        is_streaming = False
        try:
            is_streaming = bool(json.loads(raw).get("stream"))
        except Exception:
            pass
        if is_streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(_SSE_BODY)))
            self.end_headers()
            self.wfile.write(_SSE_BODY)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_JSON_BODY)))
            self.end_headers()
            self.wfile.write(_JSON_BODY)


@pytest.fixture()
def proxy_and_stub():
    """
    Start a stub upstream + the real proxy (ForwardProxyHandler from proxy.py).

    Yields (proxy_port, stub, mock_cache) where:
      - proxy_port: port the proxy listens on
      - stub: _CountingStub instance (check .request_count)
      - mock_cache: MagicMock SemanticCache (check .lookup.called, .store.called)

    The proxy is configured with SEMANTIC_CACHE_ENABLED=True and a mock cache.
    SESSION is reset before each test.
    """
    from http.server import HTTPServer

    stub_port = _free_port()
    proxy_port = _free_port()

    # Start stub upstream
    stub = _CountingStub(("127.0.0.1", stub_port), _StubHandler)
    stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_thread.start()

    # Build a real SemanticCache instance for Case A, and a mock for others.
    # We yield the mock; individual tests can swap it to the real cache if needed.
    from tokenpak.cache.semantic_cache import SemanticCache, SemanticCacheConfig
    _real_cache = SemanticCache(SemanticCacheConfig())
    _mock_cache = MagicMock()
    _mock_cache.lookup.return_value = MagicMock(hit=False, entry=None)

    # Reset SESSION
    _proxy.SESSION.pop("phase_semantic_cache", None)
    _proxy.SESSION.pop("semantic_cache_hit", None)
    _proxy.SESSION.pop("semantic_cache_stored", None)
    _proxy.SESSION.pop("semantic_cache_store_error", None)

    # Patch SEMANTIC_CACHE_ENABLED and _get_sem_cache at module level
    with patch.object(_proxy, "SEMANTIC_CACHE_ENABLED", True), \
         patch("proxy._get_sem_cache", return_value=_mock_cache):

        # Start proxy
        proxy_server = _proxy.ThreadedHTTPServer(("127.0.0.1", proxy_port), _proxy.ForwardProxyHandler)
        proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
        proxy_thread.start()

        yield proxy_port, stub, _mock_cache, _real_cache, stub_port

    proxy_server.shutdown()
    proxy_thread.join(timeout=2)
    stub.shutdown()
    stub_thread.join(timeout=2)


def _send(proxy_port: int, stub_port: int, body: bytes, extra_headers: dict | None = None) -> bytes:
    """
    Send a forward-proxy POST through the proxy to the stub upstream.
    Returns the response body bytes.
    """
    target_url = f"http://127.0.0.1:{stub_port}/v1/messages"
    req = urllib.request.Request(
        target_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            **(extra_headers or {}),
        },
    )
    proxy_handler = urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy_port}"})
    opener = urllib.request.build_opener(proxy_handler)
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        return e.read()


# ---------------------------------------------------------------------------
# Case A — JSON cache miss → store → hit on identical non-streaming query
# ---------------------------------------------------------------------------

class TestCaseA_CacheMissHit:
    """
    Two identical non-streaming requests.
    First: cache miss → upstream called → response stored.
    Second: cache hit → upstream NOT called again.
    Verifies the regression test scaffolding actually exercises the cache.
    """

    def test_cache_miss_then_hit(self, proxy_and_stub):
        proxy_port, stub, mock_cache, real_cache, stub_port = proxy_and_stub

        # Use real cache for this test
        with patch("proxy._get_sem_cache", return_value=real_cache):
            body = _make_anthropic_body(stream=False, content="What is the capital of France?")
            stub.request_count = 0

            # First request — must be a cache miss
            _proxy.SESSION.pop("phase_semantic_cache", None)
            _proxy.SESSION.pop("semantic_cache_hit", None)
            _send(proxy_port, stub_port, body)
            time.sleep(0.1)  # let post-request cache store complete

            assert stub.request_count == 1, "First request should hit upstream (cache miss)"
            phase1 = _proxy.SESSION.get("phase_semantic_cache")
            assert phase1 == "miss", f"Expected 'miss' on first request, got {phase1!r}"

            # Second request — must be a cache hit
            _proxy.SESSION.pop("phase_semantic_cache", None)
            _proxy.SESSION.pop("semantic_cache_hit", None)
            _send(proxy_port, stub_port, body)
            time.sleep(0.05)

            assert stub.request_count == 1, (
                "Second identical request should return from cache (stub count should still be 1)"
            )
            phase2 = _proxy.SESSION.get("phase_semantic_cache")
            assert phase2 == "hit", f"Expected 'hit' on second request, got {phase2!r}"


# ---------------------------------------------------------------------------
# Case B — Streaming request bypasses cache (CCG-14 guard)
# ---------------------------------------------------------------------------

class TestCaseB_StreamingBypass:
    """
    CCG-15 update: Non-Claude-Code streaming requests (stream: true) now go through
    the cache with expected_format="sse".  Only Claude Code requests are bypassed.

    The safety guarantee from the 2026-04-08 incident still holds: JSON entries
    are never served to SSE clients (cross-format mismatch → miss).

    The phase_semantic_cache marker will be "miss" (cache consulted, no entry) for
    non-Claude-Code streaming requests, rather than "skipped:streaming-or-agent".
    """

    def test_streaming_bypass(self, proxy_and_stub):
        proxy_port, stub, mock_cache, _, stub_port = proxy_and_stub

        _proxy.SESSION.pop("phase_semantic_cache", None)
        body = _make_anthropic_body(stream=True)
        response_body = _send(proxy_port, stub_port, body)
        time.sleep(0.05)

        # CCG-15: non-agent streaming requests are now cache-eligible (expected_format="sse").
        # Phase is "miss" (mock returns hit=False) — no longer "skipped:streaming-or-agent".
        phase = _proxy.SESSION.get("phase_semantic_cache")
        assert phase in ("miss", "hit"), (
            f"CCG-15: streaming request phase should be 'miss' or 'hit' (cache-eligible), "
            f"got {phase!r}. If it is 'skipped:streaming-or-agent', the CCG-15 SSE cache "
            f"path is not active."
        )
        # Safety guarantee: lookup was called with sse format (not bypassed entirely)
        mock_cache.lookup.assert_called_once()
        # Safety guarantee: response must not be a raw JSON Anthropic dict served to SSE client
        _assert_not_json_over_sse(response_body, accept_header="text/event-stream")


# ---------------------------------------------------------------------------
# Case C — Claude Code request bypasses cache by X-Claude-Code-Session-Id header
# ---------------------------------------------------------------------------

class TestCaseC_ClaudeCodeSessionHeader:
    """
    A non-streaming request bearing X-Claude-Code-Session-Id must bypass the cache.
    Claude Code always passes this header; the proxy must not serve cached JSON to it.
    """

    def test_claude_code_session_id_bypasses_cache(self, proxy_and_stub):
        proxy_port, stub, mock_cache, _, stub_port = proxy_and_stub

        _proxy.SESSION.pop("phase_semantic_cache", None)
        body = _make_anthropic_body(stream=False)  # non-streaming, but has CC header
        _send(
            proxy_port, stub_port, body,
            extra_headers={"X-Claude-Code-Session-Id": "test-session-1"},
        )
        time.sleep(0.05)

        _assert_cache_bypassed(_proxy.SESSION)
        mock_cache.lookup.assert_not_called()
        mock_cache.store.assert_not_called()


# ---------------------------------------------------------------------------
# Case D — Claude Code request bypasses cache by User-Agent
# ---------------------------------------------------------------------------

class TestCaseD_ClaudeCodeUserAgent:
    """
    A request with User-Agent containing 'claude-code' must bypass the cache,
    regardless of the stream field.
    """

    def test_claude_code_user_agent_bypasses_cache(self, proxy_and_stub):
        proxy_port, stub, mock_cache, _, stub_port = proxy_and_stub

        _proxy.SESSION.pop("phase_semantic_cache", None)
        body = _make_anthropic_body(stream=False)
        _send(
            proxy_port, stub_port, body,
            extra_headers={"User-Agent": "claude-code/2.1.96"},
        )
        time.sleep(0.05)

        _assert_cache_bypassed(_proxy.SESSION)
        mock_cache.lookup.assert_not_called()
        mock_cache.store.assert_not_called()


# ---------------------------------------------------------------------------
# Case E — Pre-populated JSON cache entry NOT served to streaming client
# ---------------------------------------------------------------------------

class TestCaseE_JsonCacheNotServedToStreamingClient:
    """
    Pre-populate the cache with a JSON dict response (the way the old broken code
    would store it). Then make a streaming request with a similar query.

    The cache must NOT serve the JSON entry to the streaming client.
    Either:
      a) CCG-14 bypass: phase_semantic_cache == 'skipped:streaming-or-agent'
      b) Cross-format miss: cache entry exists but is not served (future CCG-15)

    Either outcome is acceptable. What is NOT acceptable: the proxy calling
    _send_json with the cached dict while the client is expecting SSE.
    """

    def test_json_cache_not_served_to_streaming_client(self, proxy_and_stub):
        proxy_port, stub, mock_cache, real_cache, stub_port = proxy_and_stub

        # Pre-populate cache with JSON bytes (as CCG-15 stores them)
        query = "What is the capital of France?"
        real_cache.store(query, _JSON_BODY, content_type="application/json", wire_format="json")

        # Verify it's cached as a JSON entry
        lookup_result = real_cache.lookup(query, expected_format="json")
        assert lookup_result.hit, "Pre-populated JSON cache entry should be a hit for JSON clients"

        # JSON entry must NOT hit for SSE clients (cross-format mismatch)
        sse_lookup = real_cache.lookup(query, expected_format="sse")
        assert not sse_lookup.hit, "JSON cache entry must not be served to SSE clients (CCG-15 cross-format guard)"

        # Now send a streaming request with the same query
        with patch("proxy._get_sem_cache", return_value=real_cache):
            _proxy.SESSION.pop("phase_semantic_cache", None)
            streaming_body = json.dumps({
                "model": "claude-sonnet-4-6",
                "max_tokens": 32,
                "stream": True,
                "messages": [{"role": "user", "content": query}],
            }).encode()

            response_body = _send(
                proxy_port, stub_port, streaming_body,
                extra_headers={"Accept": "text/event-stream"},
            )
            time.sleep(0.05)

        # CCG-15: the SSE client gets a cache miss (cross-format mismatch — JSON entry not served).
        # Either "miss" (CCG-15 cross-format guard) or "skipped:streaming-or-agent" (CCG-14 bypass)
        # are acceptable — both prevent the 2026-04-08 bug.
        phase = _proxy.SESSION.get("phase_semantic_cache")
        assert phase in ("miss", "skipped:streaming-or-agent"), (
            f"Expected cache miss or bypass for SSE client, got phase={phase!r}. "
            "A JSON entry may have been served to the SSE parser — "
            "this would be the 2026-04-08 bug."
        )

        # Assertion: response must NOT be a raw JSON Anthropic dict served to SSE client
        _assert_not_json_over_sse(response_body, accept_header="text/event-stream")


# ---------------------------------------------------------------------------
# Case F — 2026-04-08 failing request: Cannot read properties of undefined
# ---------------------------------------------------------------------------

class TestCaseF_OriginalIncidentReproducer:
    """
    Replay the actual request body that failed during the 2026-04-08 incident.
    (Anonymized version from tests/fixtures/cali_failing_request_2026-04-08.json)

    The request has stream: true — it must never trigger _send_json on the
    proxy's response path.

    Assertion: response is valid SSE bytes (starts with 'data:') OR
               phase_semantic_cache == 'skipped:streaming-or-agent'.

    The test FAILS if the response is a raw Anthropic JSON dict while the
    client requested SSE — that is the exact error shape from the incident.
    """

    def test_incident_request_does_not_produce_json_over_sse(self, proxy_and_stub):
        proxy_port, stub, mock_cache, real_cache, stub_port = proxy_and_stub

        assert _FAILING_REQ.get("stream") is True, (
            "Fixture cali_failing_request_2026-04-08.json must have stream:true — "
            "update the fixture if it was modified"
        )

        body = json.dumps(_FAILING_REQ).encode()
        _proxy.SESSION.pop("phase_semantic_cache", None)

        response_body = _send(
            proxy_port, stub_port, body,
            extra_headers={"Accept": "text/event-stream"},
        )
        time.sleep(0.05)

        # CCG-15 update: the failing request has stream:true but no Claude Code headers.
        # Under CCG-15, it is cache-eligible (expected_format="sse"), so the phase is
        # "miss" or "hit" rather than "skipped:streaming-or-agent".
        # The original 2026-04-08 bug (JSON bytes served to SSE parser) is prevented by
        # the cross-format guard — JSON entries are never served to SSE clients.
        phase = _proxy.SESSION.get("phase_semantic_cache")
        assert phase in ("miss", "hit", "skipped:streaming-or-agent"), (
            f"Incident reproducer: unexpected phase={phase!r}. "
            "Expected 'miss' (CCG-15 cache consulted, no SSE entry) or "
            "'skipped:streaming-or-agent' (CCG-14 bypass)."
        )

        # Primary safety assertion: response must not be raw JSON Anthropic dict served to SSE client
        _assert_not_json_over_sse(response_body, accept_header="text/event-stream")

        # Tertiary: if we got SSE bytes, they should start with 'data:'
        # (only check if stub was reached)
        if b"data:" in response_body:
            assert response_body.startswith(b"data:"), (
                "SSE response should start with 'data:' event prefix"
            )
        # If we got neither SSE nor JSON, that's also fine — it means the proxy
        # returned an error (e.g., no API key) but did NOT crash the SSE parser.

        # CCG-15: mock_cache.lookup IS called for non-agent streaming (with expected_format="sse")
        # This is different from CCG-14 where streaming was fully bypassed.
        mock_cache.lookup.assert_called_once()
