"""Request-dispatch backends — one contract, multiple transports.

A ``Backend`` is whatever actually forwards bytes to an upstream LLM
provider. The default backend is a plain HTTP client against the
provider's API. An alternate backend (``anthropic_oauth``) shells out
to Claude Code's CLI so subscription-billing quota (OAuth) is used
instead of API keys.

Backends are chosen per request by
:class:`tokenpak.services.routing_service.backend_selector.BackendSelector`
based on the active :class:`~tokenpak.core.routing.policy.Policy` and
the ``X-TokenPak-Backend`` request header.
"""

from __future__ import annotations

from tokenpak.services.routing_service.backends.base import Backend, BackendResponse

__all__ = ["Backend", "BackendResponse"]
