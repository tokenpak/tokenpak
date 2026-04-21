"""TokenPak SDK — framework adapters + provider adapters + MCP client bridge.

Architecture §1 Level-5 entrypoint (translation layer).

Subpackages:
    mcp/         — MCP client bridge consuming services.mcp_bridge
    langchain/   — LangChain framework bridge
    llamaindex/  — LlamaIndex framework bridge
    crewai/      — CrewAI framework bridge
    autogen/     — AutoGen framework bridge
    local/       — local SDK surfaces
    integrations/— per-framework integration helpers

Per Architecture §2.4, framework adapters reach the TokenPak
execution backbone through ``tokenpak.proxy.client``; direct imports
of ``tokenpak.services`` or pipeline primitives for request execution
are forbidden except under §5.2 exceptions.

``sdk/mcp/`` consumes ``tokenpak.services.mcp_bridge`` per §1.4 plane
rule 4 (shared MCP plumbing) — this is NOT a §5.2 exception, it is by
design and allowlisted in the importlinter contract.
"""

from __future__ import annotations

# §2.4 availability import — framework bridges route execution through
# the proxy client contract.
from tokenpak.proxy import client as proxy_client  # noqa: F401

# §1.4 plane rule 4: sdk.mcp is the client bridge consuming shared
# MCP plumbing. Importing it here makes the services.mcp_bridge
# dependency visible to importlinter and to consumers.
from tokenpak.sdk import mcp as _sdk_mcp  # noqa: F401
