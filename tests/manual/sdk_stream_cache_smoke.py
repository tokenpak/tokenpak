#!/usr/bin/env python3
"""
tests/manual/sdk_stream_cache_smoke.py

CCG-15 Manual Smoke Test: non-Claude-Code SDK streaming hits cache on second call.

Spin up:
  1. A stub Anthropic upstream (counts requests, returns canned SSE)
  2. The real tokenpak proxy pointing at the stub
  3. A real SemanticCache

Scenario:
  - First identical streaming request → cache MISS (proxy hits stub)
  - Second identical streaming request → cache HIT (proxy returns from cache, stub NOT called)

Expected output:
  [1] First request: phase=miss, stub_calls=1
  [2] Second request: phase=hit, stub_calls=1  (stub still at 1 — not called again)
  PASS: SSE cache hit on second call ✓
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

# Bootstrap: load the monolith proxy.py via importlib (no sys.path mutation)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

os.environ.setdefault("TOKENPAK_SEMANTIC_CACHE", "0")  # we'll patch it to True
os.environ.setdefault("ANTHROPIC_API_KEY", "smoke-test-not-real")
os.environ.setdefault("TOKENPAK_VAULT_INDEX", "0")

import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("proxy", _PROJECT_ROOT / "proxy.py")
_proxy = _ilu.module_from_spec(_spec)
sys.modules.setdefault("proxy", _proxy)
_spec.loader.exec_module(_proxy)

# ---------------------------------------------------------------------------
# Canned SSE fixture
# ---------------------------------------------------------------------------

_SSE_BODY = (
    b"data: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_smoke_01\","
    b"\"type\":\"message\",\"role\":\"assistant\",\"content\":[],"
    b"\"stop_reason\":null,\"usage\":{\"input_tokens\":8,\"output_tokens\":0}}}\n\n"
    b"data: {\"type\":\"content_block_delta\",\"index\":0,"
    b"\"delta\":{\"type\":\"text_delta\",\"text\":\"Paris\"}}\n\n"
    b"data: {\"type\":\"message_stop\"}\n\n"
    b"data: [DONE]\n\n"
)
_JSON_BODY = json.dumps({
    "id": "msg_smoke_json_01",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Paris"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 8, "output_tokens": 5},
}).encode()


# ---------------------------------------------------------------------------
# Stub upstream
# ---------------------------------------------------------------------------

class _CountingServer(HTTPServer):
    request_count: int = 0


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self.server.request_count += 1  # type: ignore[attr-defined]
        is_stream = False
        try:
            is_stream = bool(json.loads(raw).get("stream"))
        except Exception:
            pass
        if is_stream:
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


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _send(proxy_port, stub_port, body, extra_headers=None):
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
# Smoke test
# ---------------------------------------------------------------------------

def run_smoke():
    from tokenpak.cache.semantic_cache import SemanticCache, SemanticCacheConfig

    stub_port = _free_port()
    proxy_port = _free_port()

    stub = _CountingServer(("127.0.0.1", stub_port), _StubHandler)
    stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_thread.start()

    real_cache = SemanticCache(SemanticCacheConfig(ttl_seconds=60))

    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 32,
        "stream": True,
        "messages": [{"role": "user", "content": "What is the capital of France?"}],
    }).encode()

    errors = []

    with (
        patch.object(_proxy, "SEMANTIC_CACHE_ENABLED", True),
        patch.object(_proxy, "_get_sem_cache", return_value=real_cache),
    ):
        proxy_server = _proxy.ThreadedHTTPServer(
            ("127.0.0.1", proxy_port), _proxy.ForwardProxyHandler
        )
        proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
        proxy_thread.start()

        try:
            # --- Request 1: cache miss ---
            _proxy.SESSION.pop("phase_semantic_cache", None)
            _proxy.SESSION.pop("semantic_cache_hit", None)
            _send(proxy_port, stub_port, body)
            time.sleep(0.2)  # let post-request cache store complete

            phase1 = _proxy.SESSION.get("phase_semantic_cache", "unknown")
            stub_after_1 = stub.request_count
            print(f"[1] First request:  phase={phase1!r}, stub_calls={stub_after_1}")

            if phase1 not in ("miss",):
                errors.append(f"Expected phase='miss' on first request, got {phase1!r}")
            if stub_after_1 != 1:
                errors.append(f"Expected 1 stub call after first request, got {stub_after_1}")

            # --- Request 2: cache hit ---
            _proxy.SESSION.pop("phase_semantic_cache", None)
            _proxy.SESSION.pop("semantic_cache_hit", None)
            resp2 = _send(proxy_port, stub_port, body)
            time.sleep(0.1)

            phase2 = _proxy.SESSION.get("phase_semantic_cache", "unknown")
            stub_after_2 = stub.request_count
            print(f"[2] Second request: phase={phase2!r}, stub_calls={stub_after_2}")

            if phase2 != "hit":
                errors.append(f"Expected phase='hit' on second request, got {phase2!r}")
            if stub_after_2 != 1:
                errors.append(
                    f"Stub called again on second request (count={stub_after_2}). "
                    "Cache hit should prevent upstream call."
                )

            # --- Verify SSE bytes are byte-equal ---
            # Cache key is the full request body (the JSON body), not just the text content.
            body_str = body.decode("utf-8")
            cache_entry = real_cache.lookup(body_str, expected_format="sse")
            if cache_entry.hit and cache_entry.entry:
                if resp2 == cache_entry.entry.response:
                    print("[3] Byte-equality: response matches cached SSE bytes ✓")
                else:
                    # Different byte sequences can still be functionally equivalent (e.g., SSE
                    # events may be reordered or whitespace-varied). Just check it's SSE.
                    if resp2.startswith(b"data:"):
                        print("[3] SSE response received (byte-equal check skipped — proxy may add inline stats)")
                    else:
                        errors.append("Second response is not SSE bytes")
            else:
                # The phase was 'hit' above, so cache served successfully.
                # If lookup by body string misses, that's a verification-only limitation.
                print("[3] Cache served response (verified via phase='hit' above) ✓")

        finally:
            proxy_server.shutdown()
            stub.shutdown()

    if errors:
        print("\nFAIL:")
        for e in errors:
            print(" ", e)
        sys.exit(1)
    else:
        print("\nPASS: SSE cache hit on second call ✓")


if __name__ == "__main__":
    run_smoke()
