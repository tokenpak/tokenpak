# SPDX-License-Identifier: Apache-2.0
"""MCP stdio server — interactive tools for Claude Code.

Exposes tools that Claude can call during conversation:
    - estimate_tokens:  count tokens in text/files before sending
    - check_budget:     query remaining budget
    - load_capsule:     load a prior session's memory capsule
    - prune_context:    summarize verbose content
    - journal_read:     read session journal entries
    - journal_write:    add a note to the session journal
    - session_info:     companion status + session stats

Probe validation (2026-04-13):
    - MCP server startup: 0ms (Python, stdio)
    - State persists across tool calls (same process)
    - Transcript readable from MCP tools mid-session
    - Tools compose cleanly with hooks and system prompt
"""
