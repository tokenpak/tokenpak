"""TokenPak routing rules — direct requests to specific models/providers."""

from .rules import RouteEngine, RouteRule, RouteStore  # noqa: F401

try:
    from .fallback import FallbackExhaustedError, FallbackRouter, fallback_call

    __all__ = [
        "RouteEngine",
        "RouteRule",
        "RouteStore",
        "FallbackRouter",
        "FallbackExhaustedError",
        "fallback_call",
        "fallback",
        "rules",
    ]
except ImportError:
    __all__ = ["RouteEngine", "RouteRule", "RouteStore", "fallback", "rules"]
