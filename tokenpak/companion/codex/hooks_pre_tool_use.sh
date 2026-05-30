#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# Codex PreToolUse hook — per-tool budget gate + trace stamp.
#
# Reads JSON from stdin with: session_id, transcript_path, cwd,
# hook_event_name, model, turn_id, tool_name, tool_use_id, tool_input.
#
# Actions:
#   - Stamp the tool call into the journal (best-effort)
#   - If TOKENPAK_COMPANION_BUDGET is set and the daily spend already
#     exceeds it, emit a hookSpecificOutput JSON block with
#     permissionDecision=deny + stderr reason + exit 2
#   - Otherwise: exit 0
#
# Block-output shape mirrors hooks_pre_send.sh's block delivery
# (commit ad968849d4); see L1 audit delta hooks #3 for the canonical
# reference shape on UserPromptSubmit.
# ──────────────────────────────────────────────────────────────

INPUT=$(cat)

[ "${TOKENPAK_COMPANION_ENABLED:-1}" = "0" ] && exit 0

if command -v jq >/dev/null 2>&1; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
    TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
    TOOL_USE_ID=$(echo "$INPUT" | jq -r '.tool_use_id // empty' 2>/dev/null)
    TURN_ID=$(echo "$INPUT" | jq -r '.turn_id // empty' 2>/dev/null)
else
    SESSION_ID=$(echo "$INPUT" | sed -n 's/.*"session_id"\s*:\s*"\([^"]*\)".*/\1/p')
    TOOL_NAME=$(echo "$INPUT" | sed -n 's/.*"tool_name"\s*:\s*"\([^"]*\)".*/\1/p')
    TOOL_USE_ID=$(echo "$INPUT" | sed -n 's/.*"tool_use_id"\s*:\s*"\([^"]*\)".*/\1/p')
    TURN_ID=$(echo "$INPUT" | sed -n 's/.*"turn_id"\s*:\s*"\([^"]*\)".*/\1/p')
fi

JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"
JOURNAL_DB="$JOURNAL_DIR/journal.db"
BUDGET_DB="$JOURNAL_DIR/budget.db"

# Trace stamp — non-blocking, best-effort.
if [ -n "$SESSION_ID" ] && [ -f "$JOURNAL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    TIMESTAMP=$(date +%s)
    sqlite3 "$JOURNAL_DB" \
        "INSERT OR IGNORE INTO entries (session_id, timestamp, entry_type, content, metadata_json)
         VALUES ('$SESSION_ID', $TIMESTAMP, 'auto', 'pre_tool: ${TOOL_NAME:-unknown} (turn=${TURN_ID:-?}, use_id=${TOOL_USE_ID:-?})', '{}');" 2>/dev/null &
fi

# Budget gate — only enforce if TOKENPAK_COMPANION_BUDGET is set.
BUDGET="${TOKENPAK_COMPANION_BUDGET:-0}"
if [ "$BUDGET" != "0" ] && [ -n "$BUDGET" ] && [ -f "$BUDGET_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    TODAY=$(date +%Y-%m-%d)
    DAILY_TOTAL=$(sqlite3 "$BUDGET_DB" \
        "SELECT COALESCE(SUM(estimated_cost), 0) FROM companion_costs WHERE date = '$TODAY';" 2>/dev/null || echo "0.0")
    BUDGET_MICRO=$(echo "$BUDGET * 1000000" | bc 2>/dev/null | cut -d. -f1 || echo 0)
    DAILY_MICRO=$(echo "$DAILY_TOTAL * 1000000" | bc 2>/dev/null | cut -d. -f1 || echo 0)

    if [ "${DAILY_MICRO:-0}" -ge "${BUDGET_MICRO:-0}" ] 2>/dev/null && [ "${BUDGET_MICRO:-0}" -gt 0 ] 2>/dev/null; then
        MSG="tokenpak: budget exceeded (\$$DAILY_TOTAL / \$$BUDGET daily) — blocking ${TOOL_NAME:-tool}"
        echo "$MSG" >&2
        REASON=$(printf '%s' "$MSG" | sed 's/\\/\\\\/g; s/"/\\"/g')
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$REASON"
        exit 2
    fi
fi

exit 0
