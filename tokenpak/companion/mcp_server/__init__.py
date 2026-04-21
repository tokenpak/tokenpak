"""Companion MCP server — package form.

Backwards-compatible with the historical ``companion/mcp_server.py`` file.
The ``serve`` function (and any future public helpers) are re-exported
from ``_impl`` so existing imports keep working.

``python -m tokenpak.companion.mcp_server`` still starts the server —
see ``__main__.py``.

Phase 2 reshape per DECISION-P2-03. Phase 2 follow-on work (P2-11) will
migrate the MCP protocol dispatch from this module's hand-rolled stdio
loop onto ``tokenpak.services.mcp_bridge.*``; the package shape set up
here is the home for that migration.
"""

from __future__ import annotations

from ._impl import serve

__all__ = ["serve"]
