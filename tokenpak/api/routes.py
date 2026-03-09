"""
tokenpak/api/routes.py

HTTP route definitions for the TokenPak proxy management API.

Currently exposes:
    GET /health   — Liveness + provider + cache status

Usage (standalone / testing)::

    from tokenpak.api.routes import HealthRoute

    checker = HealthRoute()          # or pass start_time=<float>
    payload = checker.handle()       # returns dict, HTTP status always 200

Integration with ProxyServer
-----------------------------
The ProxyServer (tokenpak/agent/proxy/server.py) already handles GET /health
via ``ProxyServer.health()``.  This module provides the *new* structured
endpoint that matches the documented schema (healthy|degraded|unhealthy +
per-provider checks + cache metrics).

To wire it in, add to ``_ProxyHandler.do_GET``::

    if path == "/health" or path.startswith("/health?"):
        self._send_json(ps._health_route.handle())
        return

and set ``ps._health_route = HealthRoute(start_time=ps.session['start_time'])``
in ``ProxyServer.__init__``.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

from tokenpak.monitoring.health import HealthChecker


class HealthRoute:
    """
    Handles GET /health requests.

    Parameters
    ----------
    start_time : float, optional
        Proxy start time (Unix epoch).  Defaults to module import time if not
        provided — useful for standalone/test usage.
    version : str, optional
        Override proxy version string.
    """

    def __init__(
        self,
        start_time: Optional[float] = None,
        version: Optional[str] = None,
    ) -> None:
        self._checker = HealthChecker(
            start_time=start_time or time.time(),
            version=version,
        )

    def handle(self) -> Dict[str, Any]:
        """
        Run all health checks and return the response dict.

        HTTP response is always 200; status detail is in the JSON body.
        """
        return self._checker.check()

    def handle_bytes(self) -> Tuple[bytes, int, Dict[str, str]]:
        """
        Return ``(body_bytes, http_status, headers)`` for direct HTTP handler use.

        status is always 200 — consumers must inspect ``payload["status"]``.
        """
        payload = self.handle()
        body = json.dumps(payload, indent=2).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        }
        return body, 200, headers


# ---------------------------------------------------------------------------
# Route registry — simple path-to-handler mapping
# ---------------------------------------------------------------------------

class RouteRegistry:
    """
    Minimal route registry for management API endpoints.

    Supports exact-path matching only (no regex/params).
    """

    def __init__(self) -> None:
        self._routes: Dict[str, Any] = {}

    def register(self, path: str, handler: Any) -> None:
        """Register *handler* for *path*."""
        self._routes[path] = handler

    def match(self, path: str) -> Optional[Any]:
        """Return the handler for *path*, or None if not registered."""
        # Strip query string for matching
        clean = path.split("?")[0]
        return self._routes.get(clean)

    def paths(self) -> list[str]:
        return list(self._routes.keys())


def build_default_registry(start_time: Optional[float] = None) -> RouteRegistry:
    """
    Build and return a RouteRegistry pre-populated with default management routes.

    Parameters
    ----------
    start_time : float, optional
        Passed through to HealthRoute for accurate uptime reporting.
    """
    registry = RouteRegistry()
    registry.register("/health", HealthRoute(start_time=start_time))
    return registry
