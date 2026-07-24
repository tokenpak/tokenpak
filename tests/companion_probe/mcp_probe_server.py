#!/usr/bin/env python3
"""Minimal MCP stdio server probe for tokenpak companion validation.

Tests:
  4. MCP server startup time (logged to /tmp/tp-mcp-probe.log)
  5. Can MCP tools maintain state across calls?
  6. Can MCP tools read external files (transcript, SQLite)?

This is a bare-minimum MCP server using the stdio transport.
It implements the MCP protocol directly (no SDK dependency) to validate
that Claude Code can discover and call tools.

Protocol: JSON-RPC 2.0 over stdio (newline-delimited JSON)
"""

import json
import os
import sys
import time

LOG = "/tmp/tp-mcp-probe.log"
START_TIME = time.time()

# State — persists across tool calls within the session
_state = {
    "call_count": 0,
    "startup_time_ms": 0,
    "notes": [],
}


def _log(msg: str) -> None:
    with open(LOG, "a") as f:
        f.write(f"[{time.time() - START_TIME:.3f}s] {msg}\n")


def _send(obj: dict) -> None:
    """Send a JSON-RPC response to stdout."""
    line = json.dumps(obj)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _handle_initialize(req_id, params):
    """Handle initialize request — report server capabilities."""
    _state["startup_time_ms"] = int((time.time() - START_TIME) * 1000)
    _log(f"initialize: startup took {_state['startup_time_ms']}ms")
    _send(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tokenpak-companion-probe", "version": "0.0.1"},
            },
        }
    )


def _handle_tools_list(req_id, params):
    """List available tools."""
    _send(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "probe_status",
                        "description": "Returns probe validation status — call this to verify the tokenpak companion MCP server is working. Reports startup time, call count, and state persistence.",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    },
                    {
                        "name": "estimate_tokens",
                        "description": "Estimate token count for a given text string. Returns approximate token count using the ~4 chars/token heuristic. This is a probe — production version will use a real tokenizer.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "Text to estimate tokens for",
                                }
                            },
                            "required": ["text"],
                        },
                    },
                    {
                        "name": "read_transcript",
                        "description": "Read and summarize the current session transcript. Tests whether MCP tools can access Claude Code's transcript file.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "transcript_path": {
                                    "type": "string",
                                    "description": "Path to transcript JSONL file",
                                }
                            },
                            "required": ["transcript_path"],
                        },
                    },
                ]
            },
        }
    )


def _handle_tools_call(req_id, params):
    """Execute a tool call."""
    tool_name = params.get("name", "")
    args = params.get("arguments", {})
    _state["call_count"] += 1
    _log(f"tool_call #{_state['call_count']}: {tool_name}({json.dumps(args)[:200]})")

    if tool_name == "probe_status":
        result_text = json.dumps(
            {
                "status": "ok",
                "startup_time_ms": _state["startup_time_ms"],
                "call_count": _state["call_count"],
                "state_persistent": _state["call_count"] > 1,
                "notes_stored": len(_state["notes"]),
                "pid": os.getpid(),
            },
            indent=2,
        )
        _send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result_text}]},
            }
        )

    elif tool_name == "estimate_tokens":
        text = args.get("text", "")
        char_count = len(text)
        est_tokens = char_count // 4
        result_text = json.dumps(
            {
                "char_count": char_count,
                "estimated_tokens": est_tokens,
                "method": "char/4 heuristic (probe only)",
            },
            indent=2,
        )
        _send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result_text}]},
            }
        )

    elif tool_name == "read_transcript":
        path = args.get("transcript_path", "")
        try:
            if not path or not os.path.exists(path):
                raise FileNotFoundError(f"transcript not found: {path}")
            with open(path) as f:
                lines = f.readlines()
            msg_count = 0
            total_chars = 0
            roles = {}
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    msg_count += 1
                    total_chars += len(line)
                    role = obj.get("role", obj.get("type", "unknown"))
                    roles[role] = roles.get(role, 0) + 1
                except json.JSONDecodeError:
                    pass
            result_text = json.dumps(
                {
                    "readable": True,
                    "message_count": msg_count,
                    "total_chars": total_chars,
                    "estimated_tokens": total_chars // 4,
                    "role_breakdown": roles,
                    "file_size_bytes": os.path.getsize(path),
                },
                indent=2,
            )
        except Exception as e:
            result_text = json.dumps(
                {
                    "readable": False,
                    "error": str(e),
                },
                indent=2,
            )
        _log(f"  read_transcript result: {result_text[:200]}")
        _send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result_text}]},
            }
        )

    else:
        _send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        )


def _handle_notifications(method, params):
    """Handle notifications (no response needed)."""
    _log(f"notification: {method}")


def main():
    _log("MCP probe server starting")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _log(f"bad JSON: {line[:100]}")
            continue

        req_id = req.get("id")
        method = req.get("method", "")

        _log(f"<< {method} (id={req_id})")

        if method == "initialize":
            _handle_initialize(req_id, req.get("params", {}))
        elif method == "initialized":
            _handle_notifications(method, req.get("params", {}))
        elif method == "tools/list":
            _handle_tools_list(req_id, req.get("params", {}))
        elif method == "tools/call":
            _handle_tools_call(req_id, req.get("params", {}))
        elif method == "notifications/initialized":
            _handle_notifications(method, req.get("params", {}))
        elif req_id is not None:
            # Unknown method with an ID — send error
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )
        # else: notification we don't handle — ignore

    _log("MCP probe server exiting")


if __name__ == "__main__":
    main()
