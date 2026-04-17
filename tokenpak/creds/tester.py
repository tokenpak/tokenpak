# SPDX-License-Identifier: Apache-2.0
"""Live credential verification — make one cheap call per platform.

Each platform function returns a :class:`TestResult`. Failures
distinguish "network broken" from "auth rejected" so the user can tell
what to fix.

Platforms we know how to test are declared in :data:`TESTABLE` so
adding one is "write a function, add a row, done." Unsupported
platforms return a TestResult with ``supported=False`` rather than
raising — keeps the CLI path uniform.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from .model import Credential, KIND_OAUTH
from .providers import resolve_secret


# Default timeout for the test call. Kept short because these are
# "does the key work at all" probes, not real traffic.
_TIMEOUT_SEC = 10


@dataclass(frozen=True)
class TestResult:
    ok: bool
    platform: str
    detail: str                      # human-readable single-line summary
    supported: bool = True           # False when the platform has no test impl
    http_status: Optional[int] = None


# ── per-platform probes ──────────────────────────────────────────────


def _http_get(url: str, headers: dict[str, str]) -> "tuple[int, bytes]":
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            return resp.status, resp.read(2048)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(2048) or b""
        except Exception:
            pass
        return exc.code, body


def _http_post(url: str, headers: dict[str, str], body: bytes) -> "tuple[int, bytes]":
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            return resp.status, resp.read(2048)
    except urllib.error.HTTPError as exc:
        body_out = b""
        try:
            body_out = exc.read(2048) or b""
        except Exception:
            pass
        return exc.code, body_out


def _test_openai(secret: str) -> TestResult:
    """GET https://api.openai.com/v1/models — cheap, auth-bearing."""
    try:
        status, body = _http_get(
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {secret}"},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return TestResult(False, "openai", f"network: {exc}")

    if status == 200:
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
            count = len(payload.get("data", []))
        except Exception:
            count = 0
        return TestResult(True, "openai", f"OK (saw {count} models)", http_status=200)
    return TestResult(False, "openai", f"HTTP {status}", http_status=status)


def _test_anthropic(secret: str) -> TestResult:
    """POST /v1/messages with max_tokens=1 — smallest billable call.

    Costs roughly $0.00001 per call on haiku-tier pricing. That's
    intentionally below the noise floor so running tests regularly
    doesn't distort budget reports.
    """
    payload = json.dumps(
        {
            "model": "claude-haiku-4-5",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "."}],
        }
    ).encode()
    try:
        status, body = _http_post(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": secret,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            payload,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return TestResult(False, "anthropic", f"network: {exc}")

    # Anthropic uses `Authorization: Bearer` for OAuth tokens and
    # `x-api-key` for api keys. If the first attempt hit 401 with an
    # oauth-looking token, retry the bearer form.
    if status == 401 and secret.startswith("sk-ant-oat"):
        try:
            status, body = _http_post(
                "https://api.anthropic.com/v1/messages",
                {
                    "Authorization": f"Bearer {secret}",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                payload,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return TestResult(False, "anthropic", f"network: {exc}")

    if status == 200:
        return TestResult(True, "anthropic", "OK (1-token probe accepted)", http_status=200)
    return TestResult(False, "anthropic", f"HTTP {status}", http_status=status)


def _test_google(secret: str) -> TestResult:
    """Google Generative Language API uses ?key=<APIKEY> query auth."""
    try:
        status, body = _http_get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={secret}",
            {},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return TestResult(False, "google", f"network: {exc}")
    if status == 200:
        return TestResult(True, "google", "OK", http_status=200)
    return TestResult(False, "google", f"HTTP {status}", http_status=status)


def _test_xai(secret: str) -> TestResult:
    try:
        status, body = _http_get(
            "https://api.x.ai/v1/models",
            {"Authorization": f"Bearer {secret}"},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return TestResult(False, "xai", f"network: {exc}")
    if status == 200:
        return TestResult(True, "xai", "OK", http_status=200)
    return TestResult(False, "xai", f"HTTP {status}", http_status=status)


# Platform → probe dispatch table. Add a row to support a new platform.
TESTABLE: dict[str, Callable[[str], TestResult]] = {
    "openai": _test_openai,
    "anthropic": _test_anthropic,
    "google": _test_google,
    "xai": _test_xai,
}


def test(cred: Credential) -> TestResult:
    """Run the probe registered for ``cred.platform``.

    Some (platform, kind) pairs have no cheap validation endpoint we
    can probe — e.g. OpenAI OAuth (ChatGPT-subscription JWT) is valid
    only for ``/backend-api/codex/responses`` which is unspecced and
    expensive. Those return ``supported=False`` with a hint rather
    than a misleading FAIL from the API-key endpoint.
    """
    if cred.platform == "openai" and cred.kind == KIND_OAUTH:
        return TestResult(
            False,
            cred.platform,
            "OpenAI OAuth (ChatGPT-subscription JWT) has no cheap probe — "
            "run `codex` directly to verify, or check expiry via `creds list`",
            supported=False,
        )

    probe = TESTABLE.get(cred.platform)
    if probe is None:
        return TestResult(
            False,
            cred.platform,
            f"no test implementation for platform {cred.platform!r}",
            supported=False,
        )

    secret = resolve_secret(cred)
    if not secret:
        return TestResult(
            False,
            cred.platform,
            "secret could not be resolved (file missing / env var unset / id not in config)",
        )

    return probe(secret)
