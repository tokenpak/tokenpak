# SPDX-License-Identifier: Apache-2.0
"""Hook pipeline — automatic pre-send processing for Claude Code TUI.

Hooks fire on ``UserPromptSubmit`` before each prompt is sent to the API.
The pipeline runs: token estimation → cost simulation → budget gate → journal.

Validated (2026-04-14, COMP-02):
    - Hooks fire in BOTH TUI and -p mode (confirmed Claude Code v2.1.104+)
    - exit code 2 blocks the send (num_turns=0, cost=0)
    - stderr output is visible (captured in hook_response.stderr)
    - Hook receives JSON on stdin with 6 fields:
      session_id, transcript_path, cwd, permission_mode, hook_event_name, prompt
"""
