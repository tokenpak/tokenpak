"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.tool_schema_registry``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.tool_schema_registry is a deprecated re-export; "
    "import from tokenpak.proxy.tool_schema_registry instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.tool_schema_registry import *  # noqa: F401,F403,E402

__all__ = ["FROZEN_TOOL_SCHEMAS", "ToolSchemaRegistry", "_get_frozen_tool_schemas", "get_registry"]
