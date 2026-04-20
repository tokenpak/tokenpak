"""TokenPak companion — local pre-send optimizer for Claude Code.

Architecture §1 Level-5 entrypoint (local user-side helper + MCP server).

Per Architecture §2.4, LLM request execution routes through
``tokenpak.proxy.client``. Local-only operations (byte-preservation
per §5.2-A, pre-send simulation per §5.2-B, pure local helpers per
§5.2-C) are the three documented exception paths — each call site
carries a ``# tokenpak: §5.2-exception`` marker and a matching
allowlist entry in ``.importlinter``.

Companion's MCP server surface (companion/mcp_server/) consumes
``tokenpak.services.mcp_bridge`` per §1.4 plane rule 4 (shared MCP
plumbing) — this is NOT a §5.2 exception, it is by design.
"""

from __future__ import annotations

# §2.4 availability import — companion routes LLM requests through this.
from tokenpak.proxy import client as proxy_client  # noqa: F401

# §1.4 plane rule 4: companion's MCP server consumes the shared
# services.mcp_bridge. Legacy hand-rolled stdio MCP loop in
# mcp_server/_impl.py is the bridge of this import's future call
# site; migration to the shared plumbing is P2-11 follow-on work.
from tokenpak.services import mcp_bridge as _mcp_bridge  # noqa: F401
