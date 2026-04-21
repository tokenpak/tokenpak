"""Canonical error for the MCP bridge.

Wraps upstream MCP protocol errors + our own bridge-level violations
(unknown tool, unknown resource URI, transport failure). Translates to
the canonical TIP error envelope from ``core/contracts/errors.py``
when it leaves the control plane.
"""

from __future__ import annotations


class MCPBridgeError(Exception):
    """Raised for any error inside services.mcp_bridge.

    Subclasses MAY be added as the upstream library is pinned (e.g.
    MCPTransportError, MCPHandshakeError, MCPDispatchError). For
    Phase 2 the single class is enough.
    """
