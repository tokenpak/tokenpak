"""MCP protocol plumbing shared by companion (server) and sdk.mcp (client).

Option C from the TIP-1.0 Phase 1 design decision (Kevin 2026-04-20):
MCP machinery lives in ``services/mcp_bridge/`` so that ``companion/``
(which hosts the TokenPak MCP server surface) and ``sdk/mcp/`` (which
hosts the MCP client bridge consumed by third-party MCP clients) both
consume the same adapter. Neither subsystem re-implements MCP state.

``mcp_bridge`` is an adapter over an upstream MCP library, not a fork
(Architecture §1.4 plane rule 4). Owns:

- Transport dispatch (stdio, Streamable HTTP)
- Lifecycle (connect, negotiate, heartbeat, shutdown)
- Capability negotiation (intersects TIP capability sets against peer)
- Tool/resource/prompt routing to TIP-specified handlers
- Error-envelope translation (services errors <-> MCP error frames)

Control-plane only. No model inference happens here (Architecture §1.4
plane rule 2).

Phase 2 scaffold. Real adapter lands in task P2-11.
"""

from __future__ import annotations
