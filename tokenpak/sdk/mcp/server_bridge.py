"""Glue between the sdk.mcp client bridge and companion's MCP server.

Used when an sdk-hosted framework adapter (e.g. an agent framework that
wants to expose TokenPak status/preview tools to its own UI) needs to
round-trip to the companion's MCP server surface.

Phase 2 scaffold. Real implementation lands in task P2-12.
"""

from __future__ import annotations
