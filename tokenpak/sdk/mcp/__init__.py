"""MCP client bridge + capability mapping for third-party MCP clients.

Entrypoint-level (Architecture §2, Level 5). Consumes the shared MCP
plumbing in ``tokenpak.services.mcp_bridge`` - never re-implements MCP
primitives (Architecture §1.4 plane rule 4).

Modules:

    client.py             - MCP client helpers for outbound MCP traffic
    server_bridge.py      - glue between sdk.mcp and companion's MCP server
    capability_mapping.py - translation between TIP capability labels
                            (core/contracts.capabilities) and MCP
                            capability negotiation frames

Phase 2 scaffold. Real implementation lands in task P2-12.
"""

from __future__ import annotations

__all__ = ["client", "server_bridge", "capability_mapping"]
