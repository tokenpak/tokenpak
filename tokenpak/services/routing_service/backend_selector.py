"""``BackendSelector`` — pick the transport backend for a request.

Selection rules (highest priority first):

1. **Explicit ``X-TokenPak-Backend`` header.** ``claude-code`` picks
   the OAuth backend. Other values are reserved for future backends.
2. **Policy preference.** Future: a ``Policy.preferred_backend``
   field could override here. Not in γ scope.
3. **Default** per ``RouteClass``:
   - Claude Code routes → ``anthropic-oauth`` (OAuth billing)
   - Anthropic SDK → ``anthropic-api`` (API key)
   - OpenAI SDK → (not implemented here; proxy server still handles)
   - Generic → ``anthropic-api``

The selector itself never raises. Unknown header values fall through
to the default.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.anthropic_api import (
    AnthropicAPIBackend,
)
from tokenpak.services.routing_service.backends.anthropic_oauth import (
    AnthropicOAuthBackend,
)
from tokenpak.services.routing_service.backends.base import Backend

logger = logging.getLogger(__name__)


def _loopback_active() -> bool:
    """True when TOKENPAK_PROVIDER_STUB=loopback is set.

    Gates the SC-03 LoopbackProvider — a deterministic, network-free
    stub used by the TIP self-conformance suite. When the env var is
    unset, behavior is identical to pre-SC-03 selection (production
    default).
    """
    return os.environ.get("TOKENPAK_PROVIDER_STUB", "").strip().lower() == "loopback"


class BackendSelector:
    """Resolve a :class:`Backend` for a given request + route_class."""

    def __init__(
        self,
        api_backend: Optional[Backend] = None,
        oauth_backend: Optional[Backend] = None,
    ) -> None:
        self._api = api_backend or AnthropicAPIBackend()
        self._oauth = oauth_backend or AnthropicOAuthBackend()

    def select(
        self, request: Request, route_class: RouteClass
    ) -> Backend:
        """Return the backend to use for this request."""
        # 0. SC-03 conformance-suite override. When
        # TOKENPAK_PROVIDER_STUB=loopback is set, the loopback stub
        # wins over every other selection rule. Gated by env var so
        # production paths are untouched when it's unset.
        if _loopback_active():
            # Lazy import — LoopbackProvider lives under
            # services.diagnostics, so the default import graph
            # doesn't touch it at all in the unset case.
            from tokenpak.services.diagnostics.conformance.loopback_provider import (
                get_loopback_provider,
            )
            return get_loopback_provider()

        # 1. Explicit header wins.
        hdr_val = self._get_header(request.headers or {}, "x-tokenpak-backend")
        if hdr_val:
            hdr_val = hdr_val.strip().lower()
            if hdr_val == "claude-code" or hdr_val == "oauth":
                return self._oauth
            if hdr_val == "api":
                return self._api
            logger.warning(
                "BackendSelector: unknown X-TokenPak-Backend=%r; using default",
                hdr_val,
            )

        # 3. RouteClass default.
        if route_class and route_class.is_claude_code:
            return self._oauth
        return self._api

    @staticmethod
    def _get_header(headers: dict, name: str) -> str:
        lname = name.lower()
        for k, v in headers.items():
            if k.lower() == lname:
                return v
        return ""


__all__ = ["BackendSelector"]
