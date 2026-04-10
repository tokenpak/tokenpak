"""
End-to-end test: verify cache determinism across 3 identical requests.

Spins up a local stub Anthropic-compatible HTTP server and a real ProxyServer
instance — no real Anthropic API key required.

What is verified
----------------
1. ``test_stable_prefix_identical``
   The same request sent 3 times produces an identical ``X-Tokenpak-Cache-Prefix-Hash``
   response header each time — the stable prefix is byte-deterministic.

2. ``test_cache_reuse_on_repeated_requests``
   The stub backend simulates Anthropic prompt-cache behaviour:
   - Request 1: cold start → ``cache_read_input_tokens = 0``
   - Requests 2 & 3: warm hits → ``cache_read_input_tokens > 0``
   The test verifies the proxy forwards those token counts faithfully.

Bonus unit-level tests (no proxy required)
-------------------------------------------
3. ``test_stable_prefix_hash_computation_determinism``
   Direct call to ``_compute_stable_prefix_hash`` returns the same value
   100 times in a row for the same body.

4. ``test_volatile_blocks_excluded_from_hash``
   Blocks that contain volatile patterns (timestamps, retrieved context) do
   NOT affect the stable prefix hash — only truly static blocks influence it.

5. ``test_empty_system_no_crash``
   A request with no system prompt returns an empty hash string gracefully.

Run everything:
    pytest tests/test_cache_e2e_determinism.py -v

Run only integration tests (requires stub server + proxy spin-up):
    pytest tests/test_cache_e2e_determinism.py -v -m integration

Skip slow tests:
    pytest tests/test_cache_e2e_determinism.py -v -m "not slow"
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared test payload
# ---------------------------------------------------------------------------

_TEST_REQUEST = {
    "model": "claude-sonnet-4-6",
    "max_tokens": 128,
    "system": "You are a helpful assistant. Answer questions concisely.",
    "messages": [{"role": "user", "content": "What is machine learning?"}],
}


# ---------------------------------------------------------------------------
# Stub Anthropic-compatible HTTP server
# ---------------------------------------------------------------------------

class _StubAnthropicHandler(BaseHTTPRequestHandler):
    """
    Minimal stub that mimics Anthropic's /v1/messages endpoint.

    - Request 1 to each path: returns cache_read_input_tokens = 0 (cold)
    - Requests 2+:            returns cache_read_input_tokens = 1500 (warm hit)
    """

    def log_message(self, *args, **kwargs) -> None:
        pass  # silence default request logging in tests

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        _body = self.rfile.read(content_length) if content_length else b""

        # Track call count per path (shared via server instance)
        server: _StubAnthropicServer = self.server  # type: ignore[assignment]
        with server._lock:
            server._call_count += 1
            call_no = server._call_count

        # Simulate cache warm-up: first call is cold, subsequent are warm
        cache_read = 0 if call_no == 1 else 1500
        cache_creation = 1500 if call_no == 1 else 0

        resp = {
            "id": f"msg_stub_{call_no:03d}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Machine learning is a subset of AI."}],
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 20,
                "output_tokens": 10,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        }
        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _StubAnthropicServer(HTTPServer):
    def __init__(self, host: str, port: int) -> None:
        super().__init__((host, port), _StubAnthropicHandler)
        self._call_count: int = 0
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._call_count = 0


def _free_port() -> int:
    """Return an unused TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Port {port} did not open within {timeout}s")


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stub_backend():
    """Start the stub Anthropic server, yield (host, port), tear down."""
    port = _free_port()
    server = _StubAnthropicServer("127.0.0.1", port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    _wait_port(port)
    yield "127.0.0.1", port, server
    server.shutdown()


@pytest.fixture(scope="module")
def proxy_against_stub(stub_backend):
    """
    Start a real ProxyServer pointing at the stub backend.
    Returns the proxy port.
    """
    from tokenpak.proxy.server import ProxyServer
    from tokenpak.proxy.router import ProviderRouter

    stub_host, stub_port, _ = stub_backend
    stub_base = f"http://{stub_host}:{stub_port}"

    proxy_port = _free_port()
    proxy = ProxyServer(host="127.0.0.1", port=proxy_port)
    # Redirect all Anthropic traffic to the stub
    proxy.router = ProviderRouter(custom_urls={"anthropic": stub_base})
    proxy.start(blocking=False)
    _wait_port(proxy_port)
    yield proxy_port, stub_base
    proxy.stop()


# ---------------------------------------------------------------------------
# Helper: send a single request through the proxy
# ---------------------------------------------------------------------------

def _post_via_proxy(proxy_port: int, payload: dict) -> tuple[int, dict, dict]:
    """
    Send *payload* to the proxy's /v1/messages endpoint.

    Returns (status_code, response_headers_dict, response_json).
    """
    import urllib.request
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{proxy_port}/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": "sk-ant-test-key",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        status = resp.status
        headers = dict(resp.headers)
        data = json.loads(resp.read())
    return status, headers, data


# ---------------------------------------------------------------------------
# Integration Tests — require proxy + stub server
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.slow
class TestProxyCacheDeterminism:
    """Full pipeline: stub backend + real ProxyServer."""

    def test_stable_prefix_identical(
        self, proxy_against_stub, stub_backend
    ) -> None:
        """
        Same request sent 3 times must produce the same X-Tokenpak-Cache-Prefix-Hash.

        The stable prefix is the part of the system prompt that does NOT contain
        volatile patterns (timestamps, retrieved context, etc.).  It must be
        byte-identical across all requests so Anthropic's cache can reuse it.
        """
        proxy_port, _ = proxy_against_stub
        _host, _port, stub = stub_backend
        stub.reset()

        hashes: List[str] = []
        for i in range(3):
            status, headers, _data = _post_via_proxy(proxy_port, _TEST_REQUEST)
            assert status == 200, f"Request {i+1} returned HTTP {status}"
            prefix_hash = headers.get("X-Tokenpak-Cache-Prefix-Hash", "MISSING")
            hashes.append(prefix_hash)

        assert hashes[0] != "MISSING", (
            "X-Tokenpak-Cache-Prefix-Hash header not present in response. "
            "Did you add the debug header to server.py?"
        )
        assert hashes[0] == hashes[1] == hashes[2], (
            f"Stable prefix is NOT deterministic across 3 requests:\n"
            f"  Request 1: {hashes[0]}\n"
            f"  Request 2: {hashes[1]}\n"
            f"  Request 3: {hashes[2]}"
        )

    def test_cache_reuse_on_repeated_requests(
        self, proxy_against_stub, stub_backend
    ) -> None:
        """
        Requests 2 and 3 should show cache_read_input_tokens > 0.

        The stub backend simulates Anthropic's prompt-cache behaviour:
          - Call 1 → cold start: cache_read_input_tokens = 0
          - Calls 2+ → warm hit: cache_read_input_tokens = 1500

        The proxy must faithfully forward the provider's usage data.
        """
        proxy_port, _ = proxy_against_stub
        _host, _port, stub = stub_backend
        stub.reset()

        cache_reads: List[int] = []
        for i in range(3):
            status, _headers, data = _post_via_proxy(proxy_port, _TEST_REQUEST)
            assert status == 200, f"Request {i+1} returned HTTP {status}"
            usage = data.get("usage", {})
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_reads.append(cache_read)

        assert cache_reads[1] > 0, (
            f"Request 2 expected cache hit (cache_read_input_tokens > 0), got {cache_reads[1]}. "
            f"All reads: {cache_reads}"
        )
        assert cache_reads[2] > 0, (
            f"Request 3 expected cache hit (cache_read_input_tokens > 0), got {cache_reads[2]}. "
            f"All reads: {cache_reads}"
        )

    def test_response_structure_identical_across_requests(
        self, proxy_against_stub, stub_backend
    ) -> None:
        """
        The proxy must not mutate the response structure.
        Content shape (keys) must be identical across all 3 responses.
        """
        proxy_port, _ = proxy_against_stub
        _host, _port, stub = stub_backend
        stub.reset()

        structures: List[set] = []
        for _ in range(3):
            _status, _headers, data = _post_via_proxy(proxy_port, _TEST_REQUEST)
            structures.append(set(data.keys()))

        assert structures[0] == structures[1] == structures[2], (
            f"Response key sets differ across requests: {structures}"
        )


# ---------------------------------------------------------------------------
# Unit Tests — no proxy required, fast
# ---------------------------------------------------------------------------

class TestStablePrefixHashComputation:
    """Direct unit tests for _compute_stable_prefix_hash."""

    def _get_helper(self):
        """Import the helper under test."""
        from tokenpak.proxy.server import _compute_stable_prefix_hash
        return _compute_stable_prefix_hash

    def test_stable_prefix_hash_computation_determinism(self) -> None:
        """
        _compute_stable_prefix_hash returns identical value 100 times in a row
        for the same input body.
        """
        fn = self._get_helper()
        body = json.dumps(_TEST_REQUEST).encode()
        hashes = [fn(body) for _ in range(100)]
        unique = set(hashes)
        assert len(unique) == 1, (
            f"Hash not deterministic — got {len(unique)} unique values: {unique}"
        )
        assert hashes[0], "Hash must not be empty string"

    def test_volatile_blocks_excluded_from_hash(self) -> None:
        """
        Blocks containing volatile patterns (timestamps, retrieved context) must
        NOT be included in the stable prefix hash.  Adding/changing volatile
        content should NOT change the hash.
        """
        fn = self._get_helper()
        static_system = "You are a helpful assistant. Answer concisely."

        # Request with only static system
        base_req = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "system": [
                {"type": "text", "text": static_system},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        # Same request + volatile block appended
        volatile_req = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "system": [
                {"type": "text", "text": static_system},
                # Volatile: contains <retrieved_context> pattern
                {"type": "text", "text": "<retrieved_context>doc123</retrieved_context>"},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        hash_base = fn(base_req)
        hash_volatile = fn(volatile_req)

        assert hash_base == hash_volatile, (
            f"Volatile block changed the stable prefix hash!\n"
            f"  Base hash:     {hash_base}\n"
            f"  Volatile hash: {hash_volatile}\n"
            "Volatile blocks must be excluded from the stable prefix."
        )

    def test_different_static_content_produces_different_hash(self) -> None:
        """Different stable system prompts produce different prefix hashes."""
        fn = self._get_helper()

        body_a = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "system": "You are assistant A.",
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        body_b = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "system": "You are assistant B — entirely different.",
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        assert fn(body_a) != fn(body_b), (
            "Different static system prompts should produce different hashes"
        )

    def test_empty_system_no_crash(self) -> None:
        """A request with no system prompt returns an empty hash string gracefully."""
        fn = self._get_helper()

        body_no_system = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        result = fn(body_no_system)
        assert result == "", f"Expected '' for no-system request, got {result!r}"

    def test_none_body_no_crash(self) -> None:
        """None body returns an empty hash string gracefully."""
        fn = self._get_helper()
        assert fn(None) == ""

    def test_invalid_json_no_crash(self) -> None:
        """Non-JSON bytes return an empty hash string gracefully."""
        fn = self._get_helper()
        assert fn(b"not valid json {{{{") == ""

    def test_hash_length(self) -> None:
        """Prefix hash must be exactly 16 hex characters."""
        fn = self._get_helper()
        body = json.dumps(_TEST_REQUEST).encode()
        h = fn(body)
        assert len(h) == 16, f"Expected 16-char hash, got {len(h)}: {h!r}"
        assert all(c in "0123456789abcdef" for c in h), (
            f"Hash contains non-hex characters: {h!r}"
        )

    def test_string_system_prompt_hashed(self) -> None:
        """String-form system prompt (not list) is hashed correctly."""
        fn = self._get_helper()
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "system": "Static string system prompt.",
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        h1 = fn(body)
        h2 = fn(body)
        assert h1 == h2, "String-form system prompt hash not deterministic"
        assert h1, "Hash must not be empty for non-empty system prompt"

    def test_only_volatile_blocks_returns_empty_hash(self) -> None:
        """
        When ALL system blocks are volatile, the stable set is empty.
        The result should be a hash of an empty string (or empty — implementation-defined).
        The key invariant: it must be deterministic.
        """
        fn = self._get_helper()
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 128,
            "system": [
                {"type": "text", "text": "<retrieved_context>all volatile</retrieved_context>"},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()

        h1 = fn(body)
        h2 = fn(body)
        assert h1 == h2, "All-volatile system blocks: hash must still be deterministic"


# ---------------------------------------------------------------------------
# Cross-process determinism check (subprocess-level)
# ---------------------------------------------------------------------------

class TestCrossProcessPrefixHash:
    """Verify stable prefix hash is identical across Python sub-processes."""

    _SCRIPT = """\
import sys, json
sys.path.insert(0, "{project_root}")
from tokenpak.proxy.server import _compute_stable_prefix_hash
body = json.dumps({{
    "model": "claude-sonnet-4-6",
    "max_tokens": 128,
    "system": "You are a helpful assistant. Answer questions concisely.",
    "messages": [{{"role": "user", "content": "What is machine learning?"}}],
}}).encode()
print(_compute_stable_prefix_hash(body), end="")
"""

    def test_cross_process_hash_determinism(self) -> None:
        """Same body hash from three independent sub-processes must be identical."""
        import subprocess
        import sys
        from pathlib import Path

        project_root = str(Path(__file__).parent.parent)
        script = self._SCRIPT.format(project_root=project_root)

        outputs = []
        for _ in range(3):
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"Subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            )
            outputs.append(result.stdout.strip())

        assert outputs[0] == outputs[1] == outputs[2], (
            f"Cross-process prefix hash is NOT deterministic:\n"
            f"  Run 1: {outputs[0]}\n"
            f"  Run 2: {outputs[1]}\n"
            f"  Run 3: {outputs[2]}"
        )
        assert outputs[0], "Cross-process hash must not be empty"
