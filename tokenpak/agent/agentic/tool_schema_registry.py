"""tool_schema_registry.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.features.proxy.tool_schema_registry import (
        ToolSchemaRegistry,
    )

    __all__ = [
        "ToolSchemaRegistry",
    ]
except ImportError:
    raise ImportError(
        "tool_schema_registry requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
