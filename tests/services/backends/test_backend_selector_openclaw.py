# SPDX-License-Identifier: Apache-2.0
"""BackendSelector — platform-bridge routing (2026-04-24).

Pins Kevin's 2026-04-24 ratification for OpenClaw + future platform traffic:

  - ``tokenpak-claude-code`` → always OAuth (companion subprocess path)
  - ``tokenpak-anthropic`` + ``x-api-key`` → api backend
  - ``tokenpak-anthropic`` + ``Authorization: Bearer …`` → OAuth backend
  - ``X-OpenClaw-Session`` alone (no explicit provider) → OAuth
    (openclaw's default_provider)
  - Explicit ``X-TokenPak-Provider`` always wins over platform default
  - Explicit ``X-TokenPak-Backend`` still wins over provider (preserved)
"""

from __future__ import annotations

from dataclasses import dataclass

from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.request import Request
from tokenpak.services.routing_service.backend_selector import BackendSelector
from tokenpak.services.routing_service.backends.base import BackendResponse


@dataclass
class _StubBackend:
    name: str

    def dispatch(self, request: Request) -> BackendResponse:
        return BackendResponse(status=200, headers={}, body=self.name.encode())


def _selector() -> BackendSelector:
    return BackendSelector(
        api_backend=_StubBackend(name="api-stub"),
        oauth_backend=_StubBackend(name="oauth-stub"),
    )


# ── tokenpak-claude-code provider ────────────────────────────────────────────


def test_openclaw_session_alone_selects_oauth():
    sel = _selector()
    req = Request(headers={"X-OpenClaw-Session": "sess-1"})
    # Route class is GENERIC — the bridge must upgrade routing anyway.
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "oauth-stub"


def test_explicit_provider_claude_code_selects_oauth():
    sel = _selector()
    req = Request(headers={"X-TokenPak-Provider": "tokenpak-claude-code"})
    b = sel.select(req, RouteClass.ANTHROPIC_SDK)
    assert b.name == "oauth-stub"


def test_claude_code_provider_beats_api_key_header():
    """tokenpak-claude-code always rides the companion path, even when
    the caller shipped an x-api-key. Auth is stripped + Claude CLI OAuth
    is used instead."""
    sel = _selector()
    req = Request(
        headers={
            "X-TokenPak-Provider": "tokenpak-claude-code",
            "x-api-key": "sk-ant-stray-key",
        }
    )
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "oauth-stub"


# ── tokenpak-anthropic provider (auth-shape dispatch) ────────────────────────


def test_anthropic_provider_with_api_key_selects_api():
    sel = _selector()
    req = Request(
        headers={
            "X-TokenPak-Provider": "tokenpak-anthropic",
            "x-api-key": "sk-ant-x",
        }
    )
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "api-stub"


def test_anthropic_provider_with_bearer_selects_oauth():
    sel = _selector()
    req = Request(
        headers={
            "X-TokenPak-Provider": "tokenpak-anthropic",
            "Authorization": "Bearer ocw_oauth_token",
        }
    )
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "oauth-stub"


def test_anthropic_provider_no_auth_selects_api_by_default():
    sel = _selector()
    req = Request(headers={"X-TokenPak-Provider": "tokenpak-anthropic"})
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "api-stub"


# ── Precedence: header > provider > route class ──────────────────────────────


def test_explicit_backend_header_still_wins():
    sel = _selector()
    req = Request(
        headers={
            "X-TokenPak-Backend": "api",
            "X-OpenClaw-Session": "sess-override-me",
        }
    )
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "api-stub"


def test_explicit_provider_beats_openclaw_default():
    sel = _selector()
    req = Request(
        headers={
            "X-OpenClaw-Session": "sess-1",
            "X-TokenPak-Provider": "tokenpak-anthropic",
            "x-api-key": "sk-ant-x",
        }
    )
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "api-stub"
