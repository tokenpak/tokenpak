"""Proxy request and response types for the modular proxy architecture."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ProxyRequest:
    """Incoming proxy request — captures method, URL, headers, and body.

    Used by registry adapters to pass requests through the proxy pipeline
    without coupling to the HTTP server implementation.
    """

    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    session_id: Optional[str] = None
    source_platform: str = "unknown"

    def get_header(self, name: str, default: str = "") -> str:
        """Case-insensitive header lookup."""
        lower = name.lower()
        for k, v in self.headers.items():
            if k.lower() == lower:
                return v
        return default


@dataclass
class ProxyResponse:
    """Upstream proxy response — captures status, headers, and body."""

    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def get_header(self, name: str, default: str = "") -> str:
        """Case-insensitive header lookup."""
        lower = name.lower()
        for k, v in self.headers.items():
            if k.lower() == lower:
                return v
        return default


# Route constants for request classification
ROUTE_CLAUDE_CODE = "claude-code"
ROUTE_OPENCLAW = "openclaw"
ROUTE_SDK = "sdk"


class HTTPProxy:
    """Proxy dispatch interface for registry adapters.

    Provides a clean API for adapters to forward requests through the
    proxy pipeline. The actual pipeline logic lives in proxy.py (production)
    or can be overridden for testing.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def handle_request(
        self,
        request: ProxyRequest,
        route: str = ROUTE_SDK,
        model: Optional[str] = None,
    ) -> ProxyResponse:
        """Forward a request through the proxy pipeline.

        Args:
            request: The incoming proxy request.
            route: Route classification (ROUTE_CLAUDE_CODE, ROUTE_OPENCLAW, etc.)
                   Controls which pipeline path is used.
            model: Model name for session tracking.

        Returns:
            ProxyResponse from the upstream provider.
        """
        import urllib.request
        import urllib.error
        import json as _json

        # Build forwarded headers
        fwd_headers = dict(request.headers)
        fwd_headers.setdefault("Content-Type", "application/json")

        req = urllib.request.Request(
            request.url,
            data=request.body if request.body else None,
            headers=fwd_headers,
            method=request.method,
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                resp_body = resp.read()
                resp_headers = {k: v for k, v in resp.getheaders()}
                return ProxyResponse(
                    status_code=resp.status,
                    headers=resp_headers,
                    body=resp_body,
                )
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            return ProxyResponse(
                status_code=e.code,
                headers={},
                body=resp_body,
            )
        except Exception as e:
            error_body = _json.dumps({"error": {"type": "proxy_error", "message": str(e)}}).encode()
            return ProxyResponse(
                status_code=502,
                headers={},
                body=error_body,
            )
