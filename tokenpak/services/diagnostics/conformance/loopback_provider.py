"""LoopbackProvider — deterministic, network-free provider stub.

Used by the TIP self-conformance suite:

- **Layer A (pipeline)** and **Layer B (companion)** import this
  directly and call :meth:`LoopbackProvider.dispatch` against a
  synthetic :class:`~tokenpak.services.request.Request`. No server
  is bound.
- **Layer C (HTTP loopback smoke)** activates this provider via the
  ``TOKENPAK_PROVIDER_STUB=loopback`` environment variable at proxy
  startup. When the flag is set, the proxy short-circuits its httpx
  client and answers from this module instead — so loopback tests
  exercise the real HTTP handler + the real Monitor.log emission
  path without any outbound network.

The stub is **deterministic**: the same ``Request`` in always yields
the same ``BackendResponse`` out. Usage payloads are keyed by the
request's classified :class:`RouteClass` so tests can assert
route-specific telemetry emission without mocking the usage parser.

Phase TIP-SC / SC-03 (2026-04-22). Not intended for production.
Importing this module costs nothing at runtime; the env flag is the
sole activation signal.
"""
from __future__ import annotations

import json

from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.base import (
    BackendResponse,
)

# One canned Anthropic-shaped non-streaming response body per route
# class. Token counts are small but non-zero so telemetry rows have
# positive tokens_in/tokens_out. cache_read_input_tokens varies so
# cache_origin classification is exercisable.
_CANNED_USAGE: dict[RouteClass, dict] = {
    # Claude Code family — byte-preserve routes. Usage payload
    # includes cache_read to exercise cache_origin='client' path.
    RouteClass.CLAUDE_CODE_TUI: {
        "input_tokens": 120, "output_tokens": 30,
        "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0,
    },
    RouteClass.CLAUDE_CODE_CLI: {
        "input_tokens": 120, "output_tokens": 30,
        "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0,
    },
    RouteClass.CLAUDE_CODE_TMUX: {
        "input_tokens": 120, "output_tokens": 30,
        "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0,
    },
    RouteClass.CLAUDE_CODE_SDK: {
        "input_tokens": 120, "output_tokens": 30,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    },
    RouteClass.CLAUDE_CODE_IDE: {
        "input_tokens": 120, "output_tokens": 30,
        "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0,
    },
    RouteClass.CLAUDE_CODE_CRON: {
        "input_tokens": 120, "output_tokens": 30,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    },
    # Direct SDK consumers — no client-side cache, exercises
    # proxy-managed cache path + compression eligibility.
    RouteClass.ANTHROPIC_SDK: {
        "input_tokens": 150, "output_tokens": 40,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    },
    RouteClass.OPENAI_SDK: {
        # OpenAI-shaped usage; proxy reads these fields via its own
        # adapters, but for stub consistency we mirror the Anthropic
        # keys (the proxy's OpenAI adapter remaps).
        "input_tokens": 150, "output_tokens": 40,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    },
    # Generic fallthrough — minimum viable usage.
    RouteClass.GENERIC: {
        "input_tokens": 10, "output_tokens": 5,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    },
}


_DEFAULT_MODEL = "claude-opus-4-7"


def _body_for(route_class: RouteClass) -> bytes:
    """Build a canned response body for a given route class.

    Shape matches Anthropic Messages API: ``id``, ``type``, ``role``,
    ``model``, ``content``, ``usage``. Proxy adapters + usage parsers
    treat this as a regular provider response.
    """
    usage = _CANNED_USAGE.get(route_class, _CANNED_USAGE[RouteClass.GENERIC])
    payload = {
        "id": f"msg_loopback_{route_class.value}",
        "type": "message",
        "role": "assistant",
        "model": _DEFAULT_MODEL,
        "content": [{"type": "text", "text": "[loopback stub response]"}],
        "stop_reason": "end_turn",
        "usage": usage,
    }
    return json.dumps(payload).encode()


class LoopbackProvider:
    """Zero-network backend that returns canned responses by route class.

    Satisfies :class:`tokenpak.services.routing_service.backends.base.Backend`
    so it slots into :class:`BackendSelector` for in-process test flows.
    Also exposes :meth:`respond` as a shortcut for callers that don't
    want to build a full :class:`Request`.
    """

    name = "loopback"

    def dispatch(self, request: Request) -> BackendResponse:
        """Return a canned response; never touches the network."""
        rc = self._classify(request)
        return BackendResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=_body_for(rc),
        )

    def respond(self, request: Request) -> BackendResponse:
        """Alias for :meth:`dispatch` — kept for test readability."""
        return self.dispatch(request)

    @staticmethod
    def _classify(request: Request) -> RouteClass:
        """Pick a RouteClass from the request.

        Order:
        1. ``request.metadata['route_class']`` if present (caller
           already classified — honor it).
        2. Fall back to GENERIC.

        The stub does NOT re-run the classifier; callers that want a
        specific route class set ``metadata['route_class']`` on the
        synthetic request they build.
        """
        rc = (request.metadata or {}).get("route_class")
        if isinstance(rc, RouteClass):
            return rc
        if isinstance(rc, str):
            try:
                return RouteClass(rc)
            except ValueError:
                return RouteClass.GENERIC
        return RouteClass.GENERIC


# Single shared instance — callers shouldn't need more than one.
_SHARED: LoopbackProvider | None = None


def get_loopback_provider() -> LoopbackProvider:
    """Return a shared :class:`LoopbackProvider` instance."""
    global _SHARED
    if _SHARED is None:
        _SHARED = LoopbackProvider()
    return _SHARED


__all__ = ["LoopbackProvider", "get_loopback_provider"]
