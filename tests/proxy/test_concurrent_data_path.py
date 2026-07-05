"""Concurrent data-path regressions against a real ProxyServer subprocess.

Covers:
  - N threads POST /v1/messages concurrently through a real proxy process
    wired to the canned stub upstream: every request must succeed, the
    monitor.db request ledger must hold exactly N rows, and the server
    must not log any exception.
  - Same-request-twice receipt semantics: two byte-identical requests
    carrying the same X-Request-ID produce TWO ledger rows. There is no
    request de-duplication today; this test pins that contract so any
    future dedupe behavior requires a deliberate test change.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.proxy._proxy_subprocess import (
    ProxyProc,
    assert_no_exceptions_in_stderr,
)

# Real proxy subprocess + real sockets. The generous timeout covers the
# proxy's cold-start lazy imports on loaded/shared hosts; on a healthy
# machine these tests finish in a few seconds.
pytestmark = [pytest.mark.needs_proxy, pytest.mark.timeout(120)]


@pytest.fixture()
def proxy(stub_upstream):
    p = ProxyProc(f"http://127.0.0.1:{stub_upstream.server_port}")
    try:
        p.wait_ready()
        yield p
    finally:
        p.cleanup()


def test_concurrent_posts_all_recorded(proxy, stub_upstream):
    """N concurrent POST /v1/messages: all 200, ledger rows == N, clean stderr."""
    n_requests = 8

    # First request alone: pays the lazy-import cost without inflating the
    # concurrency phase, and pins the row schema expectations early.
    status, headers, body = proxy.post_message("warmup-request")
    assert status == 200
    assert b"msg_" in body

    results: list[int] = []
    errors: list[str] = []
    lock = threading.Lock()

    def one(i: int) -> None:
        try:
            status, _, _ = proxy.post_message(f"concurrent-request-{i}", timeout=30)
            with lock:
                results.append(status)
        except Exception as exc:  # noqa: BLE001 — recorded and asserted below
            with lock:
                errors.append(f"request {i}: {exc!r}")

    with ThreadPoolExecutor(max_workers=n_requests) as ex:
        for i in range(n_requests):
            ex.submit(one, i)

    assert not errors, f"concurrent requests failed: {errors}"
    assert results == [200] * n_requests, f"unexpected statuses: {results}"

    # Ledger: warmup + n_requests rows, all recorded against the stub upstream.
    total = 1 + n_requests
    got = proxy.wait_row_count(total)
    assert got == total, (
        f"monitor.db has {got} rows, expected {total} "
        f"(async write queue lost or duplicated rows under concurrency)"
    )
    # Upstream saw every request exactly once (no proxy-side drops/dupes).
    assert stub_upstream.request_count == total

    assert_no_exceptions_in_stderr(proxy)


def test_same_request_id_twice_produces_two_rows(proxy):
    """Receipt semantics today: NO dedupe — same X-Request-ID means two rows.

    The proxy honours a client-supplied X-Request-ID (echoed on the
    response) but does not de-duplicate: replaying a byte-identical request
    with the same ID is billed/recorded twice. This test DOCUMENTS that
    at-least-once contract. If idempotent receipt handling is ever added,
    this test must be changed deliberately alongside that feature.
    """
    req_id = "regression-fixed-request-id-0001"

    status1, headers1, _ = proxy.post_message("identical-request", request_id=req_id)
    status2, headers2, _ = proxy.post_message("identical-request", request_id=req_id, timeout=30)

    assert status1 == 200 and status2 == 200
    # The proxy echoes the client-supplied ID on both responses.
    assert headers1.get("X-Request-ID") == req_id
    assert headers2.get("X-Request-ID") == req_id

    got = proxy.wait_row_count(2)
    assert got == 2, (
        f"expected 2 ledger rows for a replayed request (current no-dedupe "
        f"semantics), found {got}"
    )
    assert_no_exceptions_in_stderr(proxy)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PRODUCT BUG: client request-id echo is case-sensitive. "
        "RequestLogger.new_request_id only checks the literal spellings "
        "'X-Request-ID' and 'x-request-id'; any other casing (e.g. "
        "'X-request-id', which urllib.request produces via key.capitalize()) "
        "is ignored and a fresh UUID is echoed instead. HTTP header names "
        "are case-insensitive (RFC 9110 §5.1); the lookup should be too."
    ),
)
def test_request_id_header_lookup_is_case_insensitive(proxy):
    """A request-id sent as 'X-request-id' should still be echoed back."""
    req_id = "regression-case-insensitive-id-0001"
    status, headers, _ = proxy.post_message(
        "case-test", request_id=req_id, request_id_header="X-request-id"
    )
    assert status == 200
    assert headers.get("X-Request-ID") == req_id
