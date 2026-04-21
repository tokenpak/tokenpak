"""Plugin discovery via `tokenpak.commands` entry-points.

Paid-tier code (`tokenpak-paid` and any future first-party or third-party
plugin packages) registers CLI commands via the standard Python entry-point
mechanism. The OSS CLI dispatcher discovers those registrations at startup
and merges them into the command tree.

Feature-flagged via ``TOKENPAK_ENABLE_PLUGINS=1`` until Phase 5 flips the
default; this keeps OSS behavior unchanged during the rollout.

Behavior guarantees:
  - Broken entry-points (import error, misconfigured target) log a WARNING
    and are skipped; the CLI never crashes from a plugin failure.
  - Discovered commands carry ``_tp_plugin = True`` so help-text can render
    provenance annotations ("premium", "from X").
  - Collision with an OSS command name logs a WARNING and the OSS command
    wins (so a misbehaving plugin can't override core behavior).
"""

from __future__ import annotations

import logging
import os
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "tokenpak.commands"
_ENABLE_ENV = "TOKENPAK_ENABLE_PLUGINS"


def plugins_enabled() -> bool:
    """True when the plugin-discovery feature flag is on."""
    return os.environ.get(_ENABLE_ENV, "").strip().lower() in {"1", "true", "yes"}


def _load_one(ep: EntryPoint) -> Optional[Callable]:
    """Load a single entry-point target; return None on failure (logged)."""
    try:
        target = ep.load()
    except Exception as exc:
        logger.warning(
            "tokenpak plugin %r (from %s) failed to load: %s: %s",
            ep.name, _ep_dist(ep), type(exc).__name__, exc,
        )
        return None
    if not callable(target):
        logger.warning(
            "tokenpak plugin %r (from %s) did not resolve to a callable: %r",
            ep.name, _ep_dist(ep), target,
        )
        return None
    # Annotate for help-text + diagnostics
    try:
        setattr(target, "_tp_plugin", True)
        setattr(target, "_tp_plugin_dist", _ep_dist(ep))
    except (AttributeError, TypeError):
        # Some callables (built-ins, frozen functions) reject attribute writes.
        # That's fine — annotations are best-effort.
        pass
    return target


def _ep_dist(ep: EntryPoint) -> str:
    """Return a human-readable distribution name for an entry-point."""
    dist = getattr(ep, "dist", None)
    if dist is not None:
        name = getattr(dist, "name", None) or getattr(dist, "metadata", {}).get("Name")
        if name:
            return str(name)
    return "<unknown>"


def discover_plugin_commands(
    reserved_names: Optional[List[str]] = None,
) -> Dict[str, Callable]:
    """Return {command_name: callable} for every entry-point in the group.

    ``reserved_names`` lists OSS command names that must not be overridden;
    collisions are logged and the plugin is dropped.
    """
    if not plugins_enabled():
        return {}

    reserved_set = set(reserved_names or [])
    out: Dict[str, Callable] = {}

    try:
        eps = entry_points(group=_ENTRY_POINT_GROUP)
    except Exception as exc:
        logger.warning(
            "tokenpak plugin discovery failed at entry_points() call: %s: %s",
            type(exc).__name__, exc,
        )
        return {}

    for ep in eps:
        if ep.name in reserved_set:
            logger.warning(
                "tokenpak plugin %r (from %s) collides with an OSS command; "
                "OSS command wins — plugin skipped.",
                ep.name, _ep_dist(ep),
            )
            continue
        if ep.name in out:
            logger.warning(
                "tokenpak plugin %r registered twice (second from %s); "
                "first registration wins.",
                ep.name, _ep_dist(ep),
            )
            continue
        fn = _load_one(ep)
        if fn is not None:
            out[ep.name] = fn

    return out


def is_paid_command_available(command_name: str) -> bool:
    """Quick check: is this command currently registered by a plugin?

    Used by OSS command stubs to decide whether to defer to the plugin
    or print the 'install tokenpak-paid' upgrade message.
    """
    if not plugins_enabled():
        return False
    try:
        for ep in entry_points(group=_ENTRY_POINT_GROUP):
            if ep.name == command_name:
                return True
    except Exception:
        return False
    return False


__all__ = [
    "plugins_enabled",
    "discover_plugin_commands",
    "is_paid_command_available",
]
