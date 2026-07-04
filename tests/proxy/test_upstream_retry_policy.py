from __future__ import annotations

import json
import threading
import time

import httpx

from tokenpak.proxy.handlers.rate_limit import RateLimitBackoff
from tokenpak.proxy.upstream_retry import (
    UpstreamRetryPolicy,
    build_terminal_recovery_payload,
    extract_tip_plan_id,
    persist_failed_request_metadata,
    request_is_deterministic,
    response_has_truncated_json,
)


def _policy(max_attempts: int = 3) -> UpstreamRetryPolicy:
    return UpstreamRetryPolicy(
        max_attempts=max_attempts,
        backoff=RateLimitBackoff(base_wait=0.01, max_wait=60.0, jitter_factor=0.0),
    )


def test_dropped_pre_stream_connection_retries_safely() -> None:
    policy = _policy(max_attempts=2)

    decision = policy.retry_for_exception(
        httpx.ReadError("upstream connection dropped mid-stream"),
        0,
        stream_started=False,
    )

    assert decision.should_retry is True
    assert decision.reason == "ReadError"


def test_dropped_mid_stream_connection_fails_visibly_with_recovery_status() -> None:
    policy = _policy(max_attempts=2)

    decision = policy.retry_for_exception(
        httpx.ReadError("upstream connection dropped mid-stream"),
        0,
        stream_started=True,
    )
    payload = build_terminal_recovery_payload(
        request_id="req-123",
        tip_plan_id="tip-456",
        error_type="upstream_stream_terminal_failure",
        message="stream already started",
        stream_started=True,
    )

    assert decision.should_retry is False
    assert decision.reason == "client_output_already_started"
    assert payload["error"]["request_id"] == "req-123"
    assert payload["error"]["tip_plan_id"] == "tip-456"
    assert payload["error"]["recovery_status"] == "terminally_failed"
    assert payload["error"]["stream_started"] is True


def test_non_streaming_retry_succeeds_before_response_bytes_are_sent() -> None:
    policy = _policy(max_attempts=3)
    calls = 0
    sleeps: list[float] = []
    result = None

    for attempt in range(policy.max_attempts):
        try:
            calls += 1
            if calls == 1:
                raise httpx.RemoteProtocolError("server disconnected")
            result = b'{"ok": true}'
            break
        except policy.retryable_exceptions as exc:
            decision = policy.retry_for_exception(exc, attempt, stream_started=False)
            assert decision.should_retry is True
            sleeps.append(decision.delay_seconds)

    assert result == b'{"ok": true}'
    assert calls == 2
    assert sleeps == [0.01]


def test_429_honors_retry_after() -> None:
    policy = _policy(max_attempts=2)

    decision = policy.retry_for_response(
        429,
        {"Retry-After": "9"},
        0,
        stream_started=False,
    )

    assert decision.should_retry is True
    assert decision.delay_seconds == 9
    assert decision.reason == "http_429"


def test_deterministic_mode_never_retries() -> None:
    body = json.dumps(
        {"messages": [{"role": "user", "content": "[TIP: deterministic=on] run eval"}]}
    ).encode()
    policy = UpstreamRetryPolicy.from_env(
        body=body,
        headers={"Content-Type": "application/json"},
    )

    assert request_is_deterministic(body, {"Content-Type": "application/json"}) is True
    response_decision = policy.retry_for_response(
        429,
        {"Retry-After": "1"},
        0,
        stream_started=False,
    )
    assert response_decision.should_retry is False
    assert response_decision.reason == "deterministic_mode"
    assert (
        policy.retry_for_exception(
            httpx.ConnectTimeout("timeout"),
            0,
            stream_started=False,
        ).should_retry
        is False
    )


def test_truncated_upstream_json_is_retryable_before_output() -> None:
    assert response_has_truncated_json(
        200,
        {"Content-Type": "application/json"},
        b'{"message": "unterminated',
    )
    assert not response_has_truncated_json(
        400,
        {"Content-Type": "application/json"},
        b'{"message": "unterminated',
    )
    assert not response_has_truncated_json(
        200,
        {"Content-Type": "application/json", "Content-Encoding": "gzip"},
        b"\x1f\x8bcompressed",
    )


def test_extract_tip_plan_id_prefers_headers_then_body() -> None:
    assert (
        extract_tip_plan_id({"X-TIP-Plan-ID": "from-header"}, b"{}", "req")
        == "from-header"
    )
    assert (
        extract_tip_plan_id({}, b'{"metadata":{"tip_plan_id":"from-body"}}', "req")
        == "from-body"
    )
    assert extract_tip_plan_id({}, b"{}", "req") == "tip-plan-req"


def test_failed_request_metadata_persists_redacted_recovery_record(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RECOVERY_DIR", str(tmp_path))

    path = persist_failed_request_metadata(
        request_id="req-1",
        tip_plan_id="tip-1",
        target_url="https://api.example.test/v1/messages",
        method="POST",
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
        body=b'{"messages":[{"role":"user","content":"continue"}]}',
        stream_started=True,
        recovery_status="terminally_failed",
        error_type="ReadError",
        error_message="dropped",
    )

    assert path is not None
    data = json.loads(path.read_text())
    assert "Authorization" not in data["headers"]
    assert data["headers"]["Content-Type"] == "application/json"
    assert data["continue_requires_visible_turn"] is True
    assert data["supports_hidden_replay"] is False
    assert "body_utf8" not in data
    assert data["body_sha256"]
    assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_failed_request_metadata_persists_full_body_only_when_enabled(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RECOVERY_DIR", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_RETRY_PERSIST_BODY", "1")

    path = persist_failed_request_metadata(
        request_id="req-body",
        tip_plan_id="tip-body",
        target_url="https://api.example.test/v1/messages",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=b'{"messages":[{"role":"user","content":"continue"}]}',
        stream_started=False,
        recovery_status="terminally_failed",
        error_type="ReadError",
        error_message="dropped",
    )

    assert path is not None
    data = json.loads(path.read_text())
    assert data["body_utf8"] == '{"messages":[{"role":"user","content":"continue"}]}'


def test_high_concurrency_sends_queue_and_bound_correctly(monkeypatch) -> None:
    from tokenpak.proxy import server as proxy_server

    monkeypatch.setattr(proxy_server, "_UPSTREAM_CONCURRENCY", 1)
    proxy_server._upstream_semaphores.clear()
    proxy_server._upstream_inflight.clear()
    sem = proxy_server._get_upstream_semaphore("openai", "session-a")

    assert sem.acquire(timeout=0)
    events: list[object] = []

    def waiter() -> None:
        events.append("waiting")
        acquired = sem.acquire(timeout=0.5)
        events.append(acquired)
        if acquired:
            sem.release()

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    assert events == ["waiting"]

    sem.release()
    thread.join(timeout=1)
    assert events == ["waiting", True]


def test_max_upstream_retries_compat_alias() -> None:
    """The deprecated server-module alias stays importable and sane.

    MAX_UPSTREAM_RETRIES is retained for import compatibility only; it is
    non-authoritative (retry behavior comes from UpstreamRetryPolicy's own
    read of TOKENPAK_UPSTREAM_RETRIES). This pin keeps the alias from being
    dropped again outside an explicit major/minor API decision.
    """
    from tokenpak.proxy.server import MAX_UPSTREAM_RETRIES

    assert isinstance(MAX_UPSTREAM_RETRIES, int)
    assert MAX_UPSTREAM_RETRIES >= 1
