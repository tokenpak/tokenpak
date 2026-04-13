"""TokenPak extensions registry — adapter discovery and registration.

Registry adapters (e.g., Claude Code, OpenClaw) register themselves here
so the proxy and CLI can discover and use them by name.

Usage::

    from tokenpak import extensions

    # Register an adapter
    extensions.register("claude-code", my_adapter_instance)

    # Check / retrieve
    if extensions.is_loaded("claude-code"):
        adapter = extensions.get("claude-code")

    # Discover all installed adapters (via entry points)
    extensions.discover()
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

# Internal registry — maps adapter name → adapter instance
_EXTENSIONS: Dict[str, Any] = {}


def register(name: str, adapter: Any) -> None:
    """Register an adapter instance under *name*.

    Idempotent: overwrites any previous entry for *name* with a warning.
    """
    if name in _EXTENSIONS:
        _log.warning("extensions.register: overwriting existing adapter %r", name)
    _EXTENSIONS[name] = adapter
    _log.info("extensions.register: registered adapter %r", name)


def get(name: str) -> Optional[Any]:
    """Retrieve a registered adapter by name, or None if not found."""
    return _EXTENSIONS.get(name)


def is_loaded(name: str) -> bool:
    """Return True if an adapter is registered under *name*."""
    return name in _EXTENSIONS


def list_adapters() -> Dict[str, Any]:
    """Return a copy of the registry."""
    return dict(_EXTENSIONS)


def discover() -> int:
    """Discover and load adapters from installed entry points.

    Scans the ``tokenpak.adapters`` entry point group. Each entry point
    should resolve to a module with a ``register()`` function.

    Returns:
        Number of adapters discovered and loaded.
    """
    loaded = 0
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        # Python 3.12+ returns a SelectableGroups; 3.9 returns dict
        if hasattr(eps, "select"):
            adapter_eps = eps.select(group="tokenpak.adapters")
        else:
            adapter_eps = eps.get("tokenpak.adapters", [])

        for ep in adapter_eps:
            try:
                mod = ep.load()
                if hasattr(mod, "register"):
                    mod.register()
                    loaded += 1
                    _log.info("extensions.discover: loaded %s from entry point", ep.name)
            except Exception as exc:
                _log.warning("extensions.discover: failed to load %s: %s", ep.name, exc)
    except ImportError:
        _log.debug("extensions.discover: importlib.metadata not available")

    return loaded
