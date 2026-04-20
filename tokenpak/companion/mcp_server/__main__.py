"""Entry point for ``python -m tokenpak.companion.mcp_server``.

The companion launcher writes mcp.json referencing this module.
"""

from __future__ import annotations

from ._impl import serve

if __name__ == "__main__":
    serve()
