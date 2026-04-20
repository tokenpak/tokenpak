"""MCP client bridge for third-party MCP-aware tools reaching TokenPak.

Wraps ``tokenpak.services.mcp_bridge`` with the connection + lifecycle
shape an MCP client needs. IDEs, agent tools, and MCP-aware CLIs that
want to use TokenPak's control-plane surfaces go through here.

Phase 2 scaffold. Real implementation lands in task P2-12.
"""

from __future__ import annotations
