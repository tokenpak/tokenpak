# SPDX-License-Identifier: Apache-2.0
"""Transcript parser — reads Claude Code's live session JSONL files.

This is the foundation layer.  Most companion features (token estimation,
capsule building, context pruning, journal writing) depend on being able to
read and analyze the current conversation.

Probe validation (2026-04-13) confirmed:
    - Transcript is readable mid-session from MCP tools
    - Format is newline-delimited JSON with `type` field
    - Includes system prompts (`attachment` type), user messages, assistant
      responses, tool calls, queue operations
    - Live session file is NOT locked — concurrent reads are safe
"""
