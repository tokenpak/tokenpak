"""Platform detection — identify the calling agent/framework.

Mirrors what :class:`RouteClass` does for the request's client, but at a
coarser grain: "which agent framework is this request part of?" This
answer feeds telemetry grouping and dashboard panels.

Behavior preserved from the legacy ``tokenpak.agent.adapters.registry``
location (retired per the 18-subsystem memo). Adapters themselves still
live under ``agent/adapters/`` during the D1 migration; this module is
the canonical entry point new code should import from — the legacy
path remains as a deprecation shim.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Mapping, Optional

if TYPE_CHECKING:
    from tokenpak.agent.adapters.base import BaseAdapter


logger = logging.getLogger(__name__)


def detect_platform(
    request_headers: Mapping[str, str],
    env: Optional[Mapping[str, str]] = None,
) -> "BaseAdapter":
    """Return the adapter instance for the calling platform.

    Re-exports the legacy detection chain while we wait for adapters to
    follow suit into the canonical layout. The caller gets a
    ``BaseAdapter`` it can use for platform-specific request rewrites
    (e.g. OpenClaw, Cursor) without knowing the adapter class itself.

    Never raises. Falls back to ``GenericAdapter`` when no other
    adapter matches.
    """
    from tokenpak.agent.adapters.registry import detect_platform as _legacy

    return _legacy(dict(request_headers), dict(env) if env else dict(os.environ))


def detect_platform_name(
    request_headers: Mapping[str, str],
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Lightweight variant: just return the platform name, not the adapter.

    Useful for telemetry rows where the full adapter isn't needed.
    """
    try:
        adapter = detect_platform(request_headers, env)
        # Legacy adapters expose .name as a class attribute.
        return getattr(adapter, "name", None) or adapter.__class__.__name__.lower()
    except Exception:  # noqa: BLE001
        return "generic"


__all__ = ["detect_platform", "detect_platform_name"]
