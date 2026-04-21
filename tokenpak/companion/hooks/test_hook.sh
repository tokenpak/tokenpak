#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# test_hook.sh — minimal UserPromptSubmit validation hook
#
# Purpose: COMP-02 — verify hook pipeline fires in TUI mode.
# Writes an entry to /tmp/tokenpak-hook-test.log on every fire.
# Prints a one-line status to stderr (visible in TUI).
# Exits 2 if the prompt starts with "BLOCK_TEST" (budget-gate sim).
# ──────────────────────────────────────────────────────────────

LOG_FILE="${TOKENPAK_TEST_HOOK_LOG:-/tmp/tokenpak-hook-test.log}"

# Read stdin
INPUT=$(cat)

# Parse JSON fields — try jq first, fall back to sed
if command -v jq >/dev/null 2>&1; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "<missing>"' 2>/dev/null)
    TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // "<missing>"' 2>/dev/null)
    EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // "<missing>"' 2>/dev/null)
    PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""' 2>/dev/null | head -c 80)
else
    SESSION_ID=$(echo "$INPUT" | sed -n 's/.*"session_id"\s*:\s*"\([^"]*\)".*/\1/p' | head -1)
    TRANSCRIPT=$(echo "$INPUT" | sed -n 's/.*"transcript_path"\s*:\s*"\([^"]*\)".*/\1/p' | head -1)
    EVENT=$(echo "$INPUT" | sed -n 's/.*"hook_event_name"\s*:\s*"\([^"]*\)".*/\1/p' | head -1)
    PROMPT=$(echo "$INPUT" | sed -n 's/.*"prompt"\s*:\s*"\([^"]*\)".*/\1/p' | head -c 80)
    SESSION_ID="${SESSION_ID:-<missing>}"
    TRANSCRIPT="${TRANSCRIPT:-<missing>}"
    EVENT="${EVENT:-<missing>}"
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Write log entry
{
    echo "=== hook fired: $TIMESTAMP ==="
    echo "session_id:      $SESSION_ID"
    echo "transcript_path: $TRANSCRIPT"
    echo "hook_event_name: $EVENT"
    echo "prompt_snippet:  ${PROMPT:-(empty)}"
    echo ""
} >> "$LOG_FILE"

# Print one-line status to stderr — visible in TUI
printf '[tokenpak test-hook] fired | session=%s | event=%s\n' \
    "$SESSION_ID" "$EVENT" >&2

# Block simulation: if prompt starts with BLOCK_TEST, exit 2
PROMPT_TRIMMED=$(echo "$PROMPT" | sed 's/^[[:space:]]*//')
if [[ "$PROMPT_TRIMMED" == BLOCK_TEST* ]]; then
    echo "=== BLOCK: exit 2 at $TIMESTAMP ===" >> "$LOG_FILE"
    printf '[tokenpak test-hook] BLOCK_TEST detected — blocking send (exit 2)\n' >&2
    # Claude Code expects JSON on stdout when blocking
    printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","decision":"block","reason":"BLOCK_TEST trigger"}}\n'
    exit 2
fi

exit 0
