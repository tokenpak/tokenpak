# SPDX-License-Identifier: Apache-2.0
"""Bounded upstream retry policy for proxy send failures.

The policy is deliberately request-boundary scoped: it may retry before any
client-visible output, but it never restarts a stream after headers or chunks
have been emitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

from tokenpak.proxy.handlers.rate_limit import RateLimitBackoff

RETRYABLE_UPSTREAM_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.RemoteProtocolError,
    httpx.LocalProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.TimeoutException,
)

RETRYABLE_UPSTREAM_STATUSES = frozenset({429, 502, 503, 504})
NON_RETRYABLE_UPSTREAM_STATUSES = frozenset({400, 401, 403, 404})

_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "openai-api-key",
        "anthropic-api-key",
        "x-goog-api-key",
        "cookie",
        "set-cookie",
    }
)


@dataclass(frozen=True)
class RetryDecision:
    """Decision returned by :class:`UpstreamRetryPolicy`."""

    should_retry: bool
    delay_seconds: float = 0.0
    reason: str = ""


class UpstreamTruncatedJSONError(Exception):
    """Raised when upstream returns truncated JSON after retries are exhausted."""


def _header_get(headers: Mapping[str, object] | None, name: str) -> Optional[str]:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _parse_retry_after(raw: object) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _looks_jsonish(headers: Mapping[str, object] | None, body: bytes | None) -> bool:
    content_type = (_header_get(headers, "content-type") or "").lower()
    if "json" in content_type:
        return True
    stripped = (body or b"").lstrip()
    return stripped.startswith((b"{", b"["))


def local_json_body_is_valid(
    body: bytes | None, headers: Mapping[str, object] | None = None
) -> bool:
    """Return False only for clearly JSON request bodies that fail local parse."""

    if not body or not _looks_jsonish(headers, body):
        return True
    try:
        json.loads(body.decode("utf-8"))
    except Exception:
        return False
    return True


def request_is_deterministic(
    body: bytes | None, headers: Mapping[str, object] | None = None
) -> bool:
    """Detect request-level deterministic mode without mutating the body."""

    deterministic_header = _header_get(headers, "x-tokenpak-deterministic")
    if deterministic_header and deterministic_header.strip().lower() in {"1", "true", "on"}:
        return True
    if not body:
        return False
    lowered = body[:8192].lower()
    if b"[tip:" in lowered and b"deterministic=on" in lowered:
        return True
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    tokenpak = data.get("tokenpak")
    if isinstance(tokenpak, dict) and tokenpak.get("deterministic") is True:
        return True
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        md_tokenpak = metadata.get("tokenpak")
        if isinstance(md_tokenpak, dict) and md_tokenpak.get("deterministic") is True:
            return True
        if metadata.get("deterministic") is True:
            return True
    return False


def extract_tip_plan_id(
    headers: Mapping[str, object] | None,
    body: bytes | None,
    request_id: str,
) -> str:
    """Extract a stable plan id when present, else derive one from request id."""

    for name in (
        "x-tokenpak-plan-id",
        "x-tip-plan-id",
        "x-tokenpak-tip-plan-id",
        "tip-plan-id",
    ):
        value = _header_get(headers, name)
        if value:
            return value
    if body:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict):
            for key in ("tip_plan_id", "plan_id", "request_plan_id"):
                value = data.get(key)
                if value:
                    return str(value)
            metadata = data.get("metadata")
            if isinstance(metadata, dict):
                for key in ("tip_plan_id", "plan_id", "request_plan_id"):
                    value = metadata.get(key)
                    if value:
                        return str(value)
    return f"tip-plan-{request_id}"


def response_has_truncated_json(
    status_code: int,
    headers: Mapping[str, object] | None,
    body: bytes | None,
) -> bool:
    """Return True for invalid/truncated upstream JSON received before output."""

    content_encoding = (_header_get(headers, "content-encoding") or "").lower()
    if content_encoding and content_encoding != "identity":
        return False
    if status_code >= 400 or not body or not _looks_jsonish(headers, body):
        return False
    try:
        json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        msg = exc.msg.lower()
        return (
            "unterminated" in msg
            or "expecting value" in msg
            or "expecting ',' delimiter" in msg
            or "expecting property name" in msg
        )
    except UnicodeDecodeError:
        return True
    return False


@dataclass
class UpstreamRetryPolicy:
    """Shared bounded retry behavior for streaming and non-streaming sends."""

    max_attempts: int = 3
    retry_enabled: bool = True
    deterministic: bool = False
    local_request_valid: bool = True
    backoff: RateLimitBackoff | None = None

    @classmethod
    def from_env(
        cls,
        body: bytes | None = None,
        headers: Mapping[str, object] | None = None,
    ) -> "UpstreamRetryPolicy":
        raw_attempts = os.environ.get("TOKENPAK_UPSTREAM_RETRIES", "3")
        try:
            max_attempts = max(1, int(raw_attempts))
        except ValueError:
            max_attempts = 3
        deterministic = request_is_deterministic(body, headers)
        local_valid = local_json_body_is_valid(body, headers)
        return cls(
            max_attempts=max_attempts,
            retry_enabled=not deterministic and local_valid,
            deterministic=deterministic,
            local_request_valid=local_valid,
            backoff=RateLimitBackoff(
                base_wait=_float_env("TOKENPAK_UPSTREAM_RETRY_BASE_WAIT", 0.2),
                max_wait=_float_env("TOKENPAK_UPSTREAM_RETRY_MAX_WAIT", 60.0),
                jitter_factor=0.0,
            ),
        )

    @property
    def retryable_exceptions(self) -> tuple[type[Exception], ...]:
        return RETRYABLE_UPSTREAM_EXCEPTIONS

    def is_retryable_exception(self, exc: Exception) -> bool:
        return isinstance(exc, RETRYABLE_UPSTREAM_EXCEPTIONS)

    def _blocked_reason(self, stream_started: bool) -> Optional[str]:
        if self.deterministic:
            return "deterministic_mode"
        if not self.local_request_valid:
            return "invalid_local_request_json"
        if stream_started:
            return "client_output_already_started"
        if not self.retry_enabled:
            return "retry_disabled"
        return None

    def _delay(self, attempt: int, retry_after: object = None) -> float:
        backoff = self.backoff or RateLimitBackoff(base_wait=0.2, max_wait=2.5, jitter_factor=0.0)
        return backoff.wait_time(attempt, retry_after=_parse_retry_after(retry_after))

    def retry_for_exception(
        self,
        exc: Exception,
        attempt: int,
        *,
        stream_started: bool,
    ) -> RetryDecision:
        blocked = self._blocked_reason(stream_started)
        if blocked:
            return RetryDecision(False, reason=blocked)
        if not self.is_retryable_exception(exc):
            return RetryDecision(False, reason="non_retryable_exception")
        if attempt >= self.max_attempts - 1:
            return RetryDecision(False, reason="attempts_exhausted")
        return RetryDecision(
            True,
            delay_seconds=self._delay(attempt),
            reason=type(exc).__name__,
        )

    def retry_for_response(
        self,
        status_code: int,
        headers: Mapping[str, object] | None,
        attempt: int,
        *,
        stream_started: bool,
    ) -> RetryDecision:
        blocked = self._blocked_reason(stream_started)
        if blocked:
            return RetryDecision(False, reason=blocked)
        if status_code in NON_RETRYABLE_UPSTREAM_STATUSES:
            return RetryDecision(False, reason=f"non_retryable_http_{status_code}")
        if status_code not in RETRYABLE_UPSTREAM_STATUSES:
            return RetryDecision(False, reason=f"non_retryable_http_{status_code}")
        if attempt >= self.max_attempts - 1:
            return RetryDecision(False, reason="attempts_exhausted")
        retry_after = _header_get(headers, "retry-after") if status_code == 429 else None
        return RetryDecision(
            True,
            delay_seconds=self._delay(attempt, retry_after=retry_after),
            reason=f"http_{status_code}",
        )

    def retry_for_truncated_json(
        self,
        attempt: int,
        *,
        stream_started: bool,
    ) -> RetryDecision:
        blocked = self._blocked_reason(stream_started)
        if blocked:
            return RetryDecision(False, reason=blocked)
        if attempt >= self.max_attempts - 1:
            return RetryDecision(False, reason="attempts_exhausted")
        return RetryDecision(
            True,
            delay_seconds=self._delay(attempt),
            reason="truncated_upstream_json",
        )


def build_terminal_recovery_payload(
    *,
    request_id: str,
    tip_plan_id: str,
    error_type: str,
    message: str,
    stream_started: bool,
    recovery_record: str | None = None,
) -> dict[str, Any]:
    """Build the structured terminal recovery error envelope."""

    error = {
        "type": error_type,
        "message": message,
        "request_id": request_id,
        "tip_plan_id": tip_plan_id,
        "recovery_status": "terminally_failed",
        "retryable": False,
        "stream_started": stream_started,
        "continue_later": True,
    }
    if recovery_record:
        error["recovery_record"] = recovery_record
    return {"error": error}


def _redact_headers(headers: Mapping[str, object] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if str(key).lower() not in _SENSITIVE_HEADERS
    }


def _recovery_dir() -> Path:
    path = Path(
        os.path.expanduser(
            os.environ.get(
                "TOKENPAK_UPSTREAM_RECOVERY_DIR",
                "~/.tokenpak/recovery/upstream",
            )
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def persist_failed_request_metadata(
    *,
    request_id: str,
    tip_plan_id: str,
    target_url: str,
    method: str,
    headers: Mapping[str, object] | None,
    body: bytes | None,
    stream_started: bool,
    recovery_status: str,
    error_type: str,
    error_message: str,
) -> Optional[Path]:
    """Persist redacted metadata for a later explicit drain/continue command."""

    try:
        body_bytes = body or b""
        payload = {
            "request_id": request_id,
            "tip_plan_id": tip_plan_id,
            "target_url": target_url,
            "method": method,
            "headers": _redact_headers(headers),
            "body_sha256": hashlib.sha256(body_bytes).hexdigest() if body else "",
            "body_bytes": len(body_bytes),
            "body_preview_utf8": body_bytes[:2048].decode("utf-8", errors="replace"),
            "stream_started": stream_started,
            "recovery_status": recovery_status,
            "error_type": error_type,
            "error_message": error_message[:1000],
            "created_at": time.time(),
            "supports_hidden_replay": False,
            "continue_requires_visible_turn": stream_started,
        }
        if os.environ.get("TOKENPAK_RETRY_PERSIST_BODY", "0") == "1":
            payload["body_utf8"] = body_bytes.decode("utf-8", errors="replace")
        path = _recovery_dir() / f"{request_id}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path
    except OSError:
        return None
