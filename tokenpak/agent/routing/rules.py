"""tokenpak.agent.routing.rules — re-export shim.

The canonical implementation lives at ``tokenpak.routing.rules``.
This module re-exports everything so that both import paths work:

    from tokenpak.routing.rules import RoutePattern, RouteRule, RouteEngine
    from tokenpak.agent.routing.rules import RoutePattern, RouteRule, RouteEngine
"""

from tokenpak.routing.rules import (  # noqa: F401
    DEFAULT_ROUTES_PATH,
    RouteEngine,
    RoutePattern,
    RouteRule,
    RouteStore,
)

__all__ = [
    "RoutePattern",
    "RouteRule",
    "RouteStore",
    "RouteEngine",
    "DEFAULT_ROUTES_PATH",
]
