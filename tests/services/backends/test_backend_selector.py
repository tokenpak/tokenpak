"""BackendSelector — 1.3.0-γ acceptance."""

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


def test_claude_code_route_selects_oauth():
    sel = _selector()
    req = Request(headers={})
    b = sel.select(req, RouteClass.CLAUDE_CODE_TUI)
    assert b.name == "oauth-stub"


def test_anthropic_sdk_selects_api():
    sel = _selector()
    req = Request(headers={})
    b = sel.select(req, RouteClass.ANTHROPIC_SDK)
    assert b.name == "api-stub"


def test_generic_selects_api():
    sel = _selector()
    req = Request(headers={})
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "api-stub"


def test_header_claude_code_overrides_route():
    """Explicit X-TokenPak-Backend=claude-code on an SDK-route
    request should still pick OAuth — header wins."""
    sel = _selector()
    req = Request(headers={"X-TokenPak-Backend": "claude-code"})
    b = sel.select(req, RouteClass.ANTHROPIC_SDK)
    assert b.name == "oauth-stub"


def test_header_api_overrides_route():
    sel = _selector()
    req = Request(headers={"X-TokenPak-Backend": "api"})
    b = sel.select(req, RouteClass.CLAUDE_CODE_CLI)
    assert b.name == "api-stub"


def test_unknown_header_falls_back_to_default():
    sel = _selector()
    req = Request(headers={"X-TokenPak-Backend": "something-weird"})
    b = sel.select(req, RouteClass.ANTHROPIC_SDK)
    assert b.name == "api-stub"


def test_case_insensitive_header_lookup():
    sel = _selector()
    req = Request(headers={"x-tokenpak-backend": "claude-code"})
    b = sel.select(req, RouteClass.GENERIC)
    assert b.name == "oauth-stub"


def test_every_claude_code_mode_routes_to_oauth():
    sel = _selector()
    for rc in RouteClass:
        if not rc.is_claude_code:
            continue
        req = Request(headers={})
        assert sel.select(req, rc).name == "oauth-stub"
