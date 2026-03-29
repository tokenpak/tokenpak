# SPDX-License-Identifier: MIT
"""TokenPak pro-hooks stub.

This module provides a lightweight plugin-registration API for the tokenpak-pro
extension system. Without tokenpak-pro installed all getters return None / empty.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any, Dict, Optional

# ── Internal registries ────────────────────────────────────────────────────

_compression: Dict[str, Any] = {}
_router: Dict[str, Any] = {}
_cli_commands: Dict[str, Any] = {}
_dashboard_pages: Dict[str, Any] = {}
_telemetry: Dict[str, Any] = {}
_agentic: Dict[str, Any] = {}
_adapters: Dict[str, Any] = {}


# ── Registration helpers ───────────────────────────────────────────────────

def register_compression(name: str, impl: Any) -> None:
    _compression[name] = impl


def register_router(name: str, impl: Any) -> None:
    _router[name] = impl


def register_cli_command(name: str, desc: str, impl: Any) -> None:
    _cli_commands[name] = {"desc": desc, "fn": impl}


def register_dashboard_page(name: str, impl: Any) -> None:
    _dashboard_pages[name] = impl


def register_telemetry(name: str, impl: Any) -> None:
    _telemetry[name] = impl


def register_agentic(name: str, impl: Any) -> None:
    _agentic[name] = impl


def register_adapter(name: str, impl: Any) -> None:
    _adapters[name] = impl


# ── Getters ───────────────────────────────────────────────────────────────

def get_compression(name: str) -> Optional[Any]:
    return _compression.get(name)


def get_router(name: str) -> Optional[Any]:
    return _router.get(name)


def get_cli_commands() -> Dict[str, Any]:
    return dict(_cli_commands)


def get_dashboard_pages() -> Dict[str, Any]:
    return dict(_dashboard_pages)


def get_telemetry(name: str) -> Optional[Any]:
    return _telemetry.get(name)


def get_agentic(name: str) -> Optional[Any]:
    return _agentic.get(name)


def get_adapters() -> Dict[str, Any]:
    return dict(_adapters)


# ── Plugin discovery (entry_points based) ─────────────────────────────────

def _load_plugins() -> None:
    """Discover and load tokenpak_pro plugins via entry_points."""
    try:
        eps = importlib.metadata.entry_points(group="tokenpak_pro")
        for ep in eps:
            try:
                plugin = ep.load()
                if callable(getattr(plugin, "register", None)):
                    plugin.register()
            except Exception:
                pass  # broken plugins must not crash core
    except Exception:
        pass


# Auto-load on import
_load_plugins()
