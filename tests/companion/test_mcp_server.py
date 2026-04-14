# SPDX-License-Identifier: Apache-2.0
"""Tests for the MCP server — JSON-RPC 2.0 over stdio.

Runs the server as a subprocess, pipes requests via stdin, verifies
responses from stdout.  No Claude Code or API calls required.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Repo root so subprocess can find the package
_REPO_ROOT = str(Path(__file__).parent.parent.parent)

_ALL_TOOL_NAMES = {
    "estimate_tokens",
    "check_budget",
    "load_capsule",
    "prune_context",
    "journal_read",
    "journal_write",
    "session_info",
}


def _run_server(requests: list[dict], extra_env: dict | None = None, tmp_path=None) -> list[dict]:
    """Pipe requests to the MCP server subprocess, return parsed JSON responses."""
    env = os.environ.copy()
    env["TOKENPAK_NO_THREADS"] = "1"
    if tmp_path is not None:
        env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    if extra_env:
        env.update(extra_env)

    stdin_data = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "tokenpak.companion.mcp.server"],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )
    responses = []
    for line in proc.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

def test_initialize_returns_protocol_version():
    """initialize returns correct MCP protocol version and server info."""
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    ])
    assert len(responses) == 1
    r = responses[0]
    assert r["id"] == 1
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "tokenpak-companion"
    assert "tools" in r["result"]["capabilities"]


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------

def test_tools_list_returns_all_seven_tools(tmp_path):
    """tools/list returns all 7 companion tools with required fields."""
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
        tmp_path=tmp_path,
    )
    resp = next(r for r in responses if r["id"] == 2)
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == _ALL_TOOL_NAMES
    for t in tools:
        assert "description" in t
        assert "inputSchema" in t


# ---------------------------------------------------------------------------
# tools/call — estimate_tokens
# ---------------------------------------------------------------------------

def test_tools_call_estimate_tokens_inline_text():
    """estimate_tokens with inline text returns token and char counts."""
    sample = "hello world"
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "estimate_tokens", "arguments": {"text": sample}},
        },
    ])
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert result["tokens"] > 0
    assert result["chars"] == len(sample)
    assert "method" in result


def test_tools_call_estimate_tokens_missing_file():
    """estimate_tokens with a nonexistent file returns an error."""
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "estimate_tokens", "arguments": {"file_path": "/nonexistent/no.txt"}},
        },
    ])
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert "error" in result


# ---------------------------------------------------------------------------
# tools/call — check_budget
# ---------------------------------------------------------------------------

def test_tools_call_check_budget(tmp_path):
    """check_budget returns required budget fields."""
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "check_budget", "arguments": {}},
            },
        ],
        tmp_path=tmp_path,
    )
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert "session_cost_usd" in result
    assert "daily_cost_usd" in result
    assert "daily_budget_usd" in result
    assert "remaining_usd" in result
    assert "session_requests" in result


# ---------------------------------------------------------------------------
# tools/call — prune_context
# ---------------------------------------------------------------------------

def test_tools_call_prune_context_truncates_long_text():
    """prune_context reduces text that exceeds max_tokens."""
    long_text = "word " * 5000  # ~25000 chars
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "prune_context", "arguments": {"text": long_text, "max_tokens": 100}},
        },
    ])
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert "pruned_text" in result
    assert result["reduction_pct"] > 0
    assert len(result["pruned_text"]) < len(long_text)


def test_tools_call_prune_context_short_text_unchanged():
    """prune_context does not modify text already within max_tokens."""
    short_text = "short"
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "prune_context", "arguments": {"text": short_text, "max_tokens": 2000}},
        },
    ])
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert result["pruned_text"] == short_text
    assert result["reduction_pct"] == 0.0


# ---------------------------------------------------------------------------
# tools/call — load_capsule
# ---------------------------------------------------------------------------

def test_tools_call_load_capsule_empty_dir(tmp_path):
    """load_capsule with no capsules returns empty list."""
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "load_capsule", "arguments": {}},
            },
        ],
        tmp_path=tmp_path,
    )
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert "capsules" in result
    assert result["capsules"] == []


def test_tools_call_load_capsule_missing_session(tmp_path):
    """load_capsule with unknown session_id returns error."""
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "load_capsule", "arguments": {"session_id": "no-such-session"}},
            },
        ],
        tmp_path=tmp_path,
    )
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert "error" in result


# ---------------------------------------------------------------------------
# tools/call — journal_read and journal_write
# ---------------------------------------------------------------------------

def test_tools_call_journal_read_no_session(tmp_path):
    """journal_read with no session_id lists recent sessions (empty initially)."""
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "journal_read", "arguments": {}},
            },
        ],
        tmp_path=tmp_path,
    )
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert "sessions" in result


def test_tools_call_journal_write_no_active_session(tmp_path):
    """journal_write with no active session returns error."""
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "journal_write", "arguments": {"content": "test note"}},
            },
        ],
        tmp_path=tmp_path,
    )
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert "error" in result


# ---------------------------------------------------------------------------
# tools/call — session_info
# ---------------------------------------------------------------------------

def test_tools_call_session_info(tmp_path):
    """session_info returns companion version and config block."""
    responses = _run_server(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "session_info", "arguments": {}},
            },
        ],
        tmp_path=tmp_path,
    )
    resp = next(r for r in responses if r["id"] == 2)
    result = json.loads(resp["result"]["content"][0]["text"])
    assert result["companion_version"] == "0.1.0"
    assert "config" in result
    assert "budget" in result


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_unknown_method_returns_error():
    """Unknown method with an id returns JSON-RPC -32601 error."""
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 42, "method": "nonexistent/method", "params": {}},
    ])
    resp = next(r for r in responses if r["id"] == 42)
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_unknown_tool_returns_error():
    """tools/call with unknown tool name returns JSON-RPC error."""
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        },
    ])
    resp = next(r for r in responses if r["id"] == 2)
    assert "error" in resp


def test_notification_without_id_is_silently_ignored():
    """Notification messages (no id) do not produce a response."""
    responses = _run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},  # no id
    ])
    # Only the initialize response should come back
    assert len(responses) == 1
    assert responses[0]["id"] == 1


def test_malformed_json_line_is_ignored():
    """Lines that are not valid JSON are skipped without crashing the server."""
    env = os.environ.copy()
    env["TOKENPAK_NO_THREADS"] = "1"
    stdin_data = (
        "{not json}\n"
        + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "tokenpak.companion.mcp.server"],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )
    responses = [json.loads(l) for l in proc.stdout.strip().split("\n") if l.strip()]
    assert len(responses) == 1
    assert responses[0]["id"] == 1
