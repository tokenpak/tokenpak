# SPDX-License-Identifier: Apache-2.0
"""Transcript parser — reads Claude Code's live session JSONL files.

This is the foundation layer.  Most companion features (token estimation,
capsule building, context pruning, journal writing) depend on being able to
read and analyze the current conversation.

Validated (2026-04-13 probe + 2026-04-14 COMP-02):
    - Transcript is readable mid-session from MCP tools
    - Format is newline-delimited JSON with ``type`` field
    - Includes system prompts (``attachment`` type), user messages, assistant
      responses, tool calls, queue operations
    - Live session file is NOT locked — concurrent reads are safe
    - Hooks receive ``transcript_path`` in both TUI and -p mode
"""
