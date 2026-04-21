"""Minimal MCP stdio JSON-RPC 2.0 server for the tokenpak companion.

Implements the MCP protocol over stdin/stdout using newline-delimited JSON.
Handles ``initialize`` and ``tools/list`` requests; returns method-not-found
for anything else.

Usage (as a module):
    python3 -m tokenpak.companion.mcp_server

The launcher wires this via ``--mcp-config`` pointing to an mcp.json that
invokes this module directly.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from tokenpak.companion.mcp.tools import LOAD_CAPSULE_SCHEMA, TOOL_HANDLERS

_SERVER_INFO = {"name": "tokenpak-companion", "version": "0.1.0"}
_PROTOCOL_VERSION = "2024-11-05"

# Tools advertised in tools/list — Wave 2 will add real handlers for all of them.
_TOOL_SCHEMAS = [
    LOAD_CAPSULE_SCHEMA,
    {
        "name": "estimate_tokens",
        "description": "Estimate the token count for a text string or file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to count tokens for."},
                "file_path": {"type": "string", "description": "File path to count tokens for."},
            },
            "required": [],
        },
    },
    {
        "name": "check_budget",
        "description": "Return current session and daily token/cost usage vs. budget.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "prune_context",
        "description": "Trim text to fit within a token limit using heuristic strategies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to prune."},
                "max_tokens": {"type": "integer", "description": "Target token ceiling."},
            },
            "required": ["text", "max_tokens"],
        },
    },
    {
        "name": "journal_read",
        "description": "Read journal entries for this session or all sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "entry_type": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "journal_write",
        "description": "Write a journal entry for the current session.",
        "inputSchema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },
    {
        "name": "session_info",
        "description": "Return metadata about the current companion session.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def _make_result(req_id: Any, result: Dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _make_error(req_id: Any, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _handle(msg: Dict[str, Any]) -> Optional[str]:
    """Dispatch a single JSON-RPC message and return a response line or None."""
    method = msg.get("method", "")
    req_id = msg.get("id")

    # Notifications have no id — no response required.
    if req_id is None:
        return None

    if method == "initialize":
        result = {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": _SERVER_INFO,
        }
        return _make_result(req_id, result)

    if method == "tools/list":
        return _make_result(req_id, {"tools": _TOOL_SCHEMAS})

    if method == "tools/call":
        params = msg.get("params") or {}
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}

        # Build a fast lookup of required params per tool from the advertised schemas.
        schema_by_name = {s["name"]: s for s in _TOOL_SCHEMAS}
        if tool_name not in schema_by_name:
            return _make_error(req_id, -32602, f"Unknown tool: {tool_name}")

        required = schema_by_name[tool_name].get("inputSchema", {}).get("required", [])
        missing = [p for p in required if p not in tool_args]
        if missing:
            return _make_error(
                req_id, -32602, f"Missing required parameter(s): {', '.join(missing)}"
            )

        handler = TOOL_HANDLERS[tool_name]
        handler_result = handler(tool_args)

        # Translate internal {"content": str, ...} to MCP tools/call result shape.
        content_text = handler_result.get("content", "")
        mcp_result: Dict[str, Any] = {
            "content": [{"type": "text", "text": content_text}],
        }
        if "error" in handler_result:
            mcp_result["isError"] = True
        return _make_result(req_id, mcp_result)

    # Unknown method
    return _make_error(req_id, -32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None) -> None:
    """Run the MCP server, reading from *stdin* and writing to *stdout*.

    Args:
        stdin: File-like object to read JSON-RPC messages from.  Defaults to
            ``sys.stdin``.
        stdout: File-like object to write JSON-RPC responses to.  Defaults to
            ``sys.stdout``.
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Emit a parse error for any id we can't even read
            resp = _make_error(None, -32700, "Parse error")
            print(resp, file=stdout, flush=True)
            continue

        response = _handle(msg)
        if response is not None:
            print(response, file=stdout, flush=True)


# The package's __main__.py handles `python -m tokenpak.companion.mcp_server`
# invocation. Leave this module import-safe.
