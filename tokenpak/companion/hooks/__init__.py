# SPDX-License-Identifier: Apache-2.0
"""Hook pipeline — automatic pre-send processing for Claude Code TUI.

Hooks fire on ``UserPromptSubmit`` before each prompt is sent to the API.
The pipeline runs: token estimation → cost simulation → budget gate → journal.

Probe validation (2026-04-13):
    - Hooks fire in TUI (interactive) mode only, NOT in -p (print) mode
    - exit code 2 blocks the send (budget gating)
    - stderr output is visible in the TUI
    - Hook receives JSON on stdin with session_id, transcript_path, message
"""
