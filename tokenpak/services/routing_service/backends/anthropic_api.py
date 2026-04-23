"""Default Anthropic API backend — httpx client.

This backend is the default for ``RouteClass.ANTHROPIC_SDK`` and is
also the fallback when no other backend is selected. It forwards the
request body verbatim to ``target_url`` (resolved by the proxy's
provider router) with header passthrough.

Current status: this module is a thin formal wrapper around what the
proxy server already does via its httpx ``pool.request`` / ``pool.stream``
calls. Phase γ only introduces the Backend contract so γ+δ can route
through it; the proxy continues to run its own client in parallel
until the pipeline-dispatch rewrite lands (a later initiative).

Kept intentionally minimal — no compression, no cache lookups, no
policy handling. Those live in their respective pipeline stages upstream.
"""

from __future__ import annotations

import logging

from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.base import BackendResponse

logger = logging.getLogger(__name__)


class AnthropicAPIBackend:
    """Plain HTTP forwarder to ``api.anthropic.com`` (or mirror)."""

    name = "anthropic-api"

    def __init__(self, base_url: str = "https://api.anthropic.com") -> None:
        self._base_url = base_url.rstrip("/")
        self._client = None  # lazy

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=60.0, follow_redirects=False)
        return self._client

    def dispatch(self, request: Request) -> BackendResponse:
        """Forward the request to the configured base URL. Synchronous."""
        target = request.metadata.get("target_url") or self._base_url
        client = self._get_client()
        method = request.metadata.get("method", "POST").upper()
        try:
            resp = client.request(
                method,
                target,
                content=request.body or b"",
                headers=request.headers or {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("anthropic-api backend: dispatch failed: %s", exc)
            return BackendResponse(status=502, headers={}, body=str(exc).encode())

        return BackendResponse(
            status=resp.status_code,
            headers={k: v for k, v in resp.headers.items()},
            body=resp.content,
        )


__all__ = ["AnthropicAPIBackend"]
