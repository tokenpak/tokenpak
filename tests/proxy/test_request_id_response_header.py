"""End-to-end tests for the X-TokenPak-Request-ID response header.

The proxy stamps an opaque per-request correlation id on every response it
returns to clients:

  - non-streaming forwarded responses
  - streaming (SSE) forwarded responses — the header is sent with the
    response head, before the first body chunk
  - proxy-generated error responses (e.g. upstream connection failure)
  - proxy-owned utility endpoints (e.g. /health)

The forwarding path reuses its existing per-request id (the same value
echoed as X-Request-ID), so both headers correlate; every other response
path mints an opaque uuid4 hex. Response bodies are byte-preserved: the
header is additive and the forwarded body must be bit-for-bit identical
to what the upstream returned.

Requested in https://github.com/tokenpak/tokenpak/issues/3.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest

from tokenpak.proxy.server import ProxyServer

pytestmark = [pytest.mark.needs_proxy, pytest.mark.timeout(120)]

HEADER = "X-TokenPak-Request-ID"

# Canned upstream fixtures — the same bytes the stub_upstream fixture
# (tests/proxy/conftest.py) replays, used here for byte-identity assertions.
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
_SSE_BODY = (_FIXTURES_DIR / "sse_response_message_delta.txt").read_bytes()
_JSON_BODY = (_FIXTURES_DIR / "json_response_messages.json").read_bytes()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def proxy_port() -> Generator[int, None, None]:
    """A real in-process ProxyServer on a free port."""
    port = _free_port()
    server = ProxyServer(host="127.0.0.1", port=port)
    server.start(blocking=False)
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            break
        except OSError:
            time.sleep(0.05)
    yield port
    server.stop()


def _message_payload(*, stream: bool = False) -> bytes:
    payload: dict[str, object] = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "request id header probe"}],
    }
    if stream:
        payload["stream"] = True
    return json.dumps(payload).encode()


def _post_via_proxy(
    proxy_port: int,
    target_url: str,
    body: bytes,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """POST an absolute-form request through the proxy, return the response.

    Returns (status, headers, body). Headers are captured from the parsed
    response head — i.e. before the body is read — so streaming assertions
    genuinely prove the header arrived ahead of the body bytes.
    """
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=60)
    try:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": "sk-tokenpak-request-id-header-test",
        }
        headers.update(extra_headers or {})
        conn.request("POST", target_url, body=body, headers=headers)
        resp = conn.getresponse()
        # Snapshot headers before reading any body bytes.
        head = resp.headers
        status = resp.status
        data = resp.read()
        return status, head, data
    finally:
        conn.close()


class TestForwardedResponses:
    def test_non_streaming_response_carries_header_and_body_is_unchanged(
        self, proxy_port, stub_upstream
    ):
        status, headers, body = _post_via_proxy(
            proxy_port,
            f"http://127.0.0.1:{stub_upstream.server_port}/v1/messages",
            _message_payload(),
        )

        assert status == 200
        rid = headers.get(HEADER)
        assert rid, f"{HEADER} missing from non-streaming response"
        # Byte-preservation: the forwarded body must be exactly the canned
        # upstream bytes — the header must not perturb the body path.
        assert body == _JSON_BODY

    def test_streaming_response_carries_header_before_body(self, proxy_port, stub_upstream):
        status, headers, body = _post_via_proxy(
            proxy_port,
            f"http://127.0.0.1:{stub_upstream.server_port}/v1/messages",
            _message_payload(stream=True),
        )

        assert status == 200
        assert headers.get("Content-Type", "").startswith("text/event-stream")
        # The header was parsed from the response head, so its presence here
        # proves it was emitted before the first SSE chunk.
        rid = headers.get(HEADER)
        assert rid, f"{HEADER} missing from streaming response"
        # Streamed body bytes are relayed unchanged.
        assert body == _SSE_BODY

    def test_header_reuses_the_forwarding_request_id(self, proxy_port, stub_upstream):
        status, headers, _ = _post_via_proxy(
            proxy_port,
            f"http://127.0.0.1:{stub_upstream.server_port}/v1/messages",
            _message_payload(),
        )

        assert status == 200
        # The forwarding path already echoes its per-request correlation id
        # as X-Request-ID; the new header must carry the same value rather
        # than minting a second, uncorrelated id.
        assert headers.get(HEADER) == headers.get("X-Request-ID")
        # When the proxy generates the id itself it must be an opaque UUID.
        assert uuid.UUID(headers.get(HEADER))

    def test_client_supplied_request_id_is_honoured(self, proxy_port, stub_upstream):
        supplied = uuid.uuid4().hex
        status, headers, _ = _post_via_proxy(
            proxy_port,
            f"http://127.0.0.1:{stub_upstream.server_port}/v1/messages",
            _message_payload(),
            extra_headers={"X-Request-ID": supplied},
        )

        assert status == 200
        assert headers.get(HEADER) == supplied

    def test_each_request_gets_its_own_id(self, proxy_port, stub_upstream):
        target = f"http://127.0.0.1:{stub_upstream.server_port}/v1/messages"
        _, first_headers, _ = _post_via_proxy(proxy_port, target, _message_payload())
        _, second_headers, _ = _post_via_proxy(proxy_port, target, _message_payload())

        assert first_headers.get(HEADER)
        assert second_headers.get(HEADER)
        assert first_headers.get(HEADER) != second_headers.get(HEADER)


class TestProxyGeneratedResponses:
    def test_upstream_connection_failure_error_carries_header(self, proxy_port, monkeypatch):
        # Keep the connect-refused retry loop short so the 502 arrives fast.
        monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRIES", "1")
        monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRY_BASE_WAIT", "0.01")
        closed_port = _free_port()  # nothing is listening here

        status, headers, body = _post_via_proxy(
            proxy_port,
            f"http://127.0.0.1:{closed_port}/v1/messages",
            _message_payload(),
        )

        assert status == 502
        rid = headers.get(HEADER)
        assert rid, f"{HEADER} missing from proxy-generated error response"
        # Error envelope stays intact JSON — the header is purely additive.
        assert json.loads(body)["error"]

    def test_health_endpoint_carries_opaque_generated_id(self, proxy_port):
        conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=30)
        try:
            conn.request("GET", "/health")
            resp = conn.getresponse()
            rid = resp.headers.get(HEADER)
            resp.read()
        finally:
            conn.close()

        assert rid, f"{HEADER} missing from /health response"
        # Locally-minted ids are opaque uuid4 hex — parseable as a UUID and
        # free of any host, user, or path information.
        assert uuid.UUID(hex=rid).version == 4
