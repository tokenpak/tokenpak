#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""MCP stdio server for the tokenpak companion.

Implements the MCP protocol (JSON-RPC 2.0 over stdio) without external
dependencies.  Claude Code starts this as a child process via --mcp-config.

Usage::

    # Started automatically by `tokenpak claude` launcher
    # Or manually for testing:
    python3 -m tokenpak.companion.mcp.server
"""

from __future__ import annotations

import json
import sys

from .tools import TOOLS, CompanionState


def _send(obj: dict) -> None:
    """Write a JSON-RPC response to stdout."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _handle_initialize(req_id: int | str, state: CompanionState) -> None:
    _send({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "tokenpak-companion",
                "version": "0.1.0",
            },
        },
    })


def _handle_tools_list(req_id: int | str) -> None:
    _send({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in TOOLS
            ]
        },
    })


def _handle_tools_call(req_id: int | str, params: dict, state: CompanionState) -> None:
    tool_name = params.get("name", "")
    args = params.get("arguments", {})
    state.call_count += 1

    # Find the tool
    tool = None
    for t in TOOLS:
        if t.name == tool_name:
            tool = t
            break

    if tool is None:
        _send({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
        })
        return

    try:
        result_text = tool.handler(state, args)
    except Exception as e:
        result_text = json.dumps({"error": str(e)})

    _send({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": result_text}]
        },
    })


def main() -> None:
    """MCP server main loop — read JSON-RPC from stdin, dispatch, respond."""
    state = CompanionState()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method", "")

        if method == "initialize":
            _handle_initialize(req_id, state)
        elif method == "tools/list":
            _handle_tools_list(req_id)
        elif method == "tools/call":
            _handle_tools_call(req_id, req.get("params", {}), state)
        elif req_id is not None:
            # Unknown method with ID — error
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })
        # Notifications (no id) are silently ignored


if __name__ == "__main__":
    main()
