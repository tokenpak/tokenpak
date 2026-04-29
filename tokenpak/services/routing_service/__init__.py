"""Routing service — platform origin extraction and session mapping.

Sub-module of ``tokenpak/services/`` (per ``01-architecture-standard.md §1``,
the shared execution backbone at Level 3).

Exposes:
    * :class:`PlatformOrigin` — dataclass returned by extractors.
    * :func:`_openclaw_extract` — Path C reader that resolves the active
      OpenClaw session from ``~/.openclaw/sessions/active.json``.
"""

from tokenpak.services.routing_service.platform_bridge import (
    ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY,
    ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE,
    ATTRIBUTION_UNKNOWN,
    PlatformOrigin,
    _openclaw_extract,
    _read_active_json,
)

__all__ = [
    "PlatformOrigin",
    "_openclaw_extract",
    "_read_active_json",
    "ATTRIBUTION_OPENCLAW_ACTIVE_SESSION_FILE",
    "ATTRIBUTION_ANONYMOUS_USER_AGENT_ONLY",
    "ATTRIBUTION_UNKNOWN",
]
