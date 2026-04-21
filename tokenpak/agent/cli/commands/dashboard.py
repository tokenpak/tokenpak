"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.dashboard``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.dashboard is a deprecated re-export; "
    "import from tokenpak.cli.commands.dashboard instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.dashboard import *  # noqa: F401,F403,E402

__all__ = ["AUTH_PROFILES_FILE", "Any", "Dict", "FLEET_CONFIG_FILE", "List", "Optional", "PROXY_PORT", "Path", "REFRESH_INTERVAL", "collect_fleet_data", "collect_local_data", "dashboard_cmd", "datetime", "run_dashboard", "timezone"]
