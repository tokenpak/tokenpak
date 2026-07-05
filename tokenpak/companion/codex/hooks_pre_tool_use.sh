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

# Dedupe key — sha256(entry_type US content US metadata_json), matching the
# canonical hash computed by the python journal writers (companion/_sqlite.py).
# Empty output (no sha tool available) degrades to a NULL hash: the row is
# still written, just without dedupe protection.
_tp_entry_hash() {
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s\037%s\037%s' "$1" "$2" "$3" | sha256sum 2>/dev/null | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        printf '%s\037%s\037%s' "$1" "$2" "$3" | shasum -a 256 2>/dev/null | awk '{print $1}'
    fi
}

# Best-effort additive schema upgrade so INSERT OR IGNORE can carry the
# content_hash dedupe key on databases created before the column existed.
# The ALTER fails harmlessly once the column is present; nothing is ever
# rewritten or deleted.
_tp_ensure_dedupe_schema() {
    sqlite3 -cmd ".timeout 5000" "$1" \
        "ALTER TABLE entries ADD COLUMN content_hash TEXT;" >/dev/null 2>&1
    sqlite3 -cmd ".timeout 5000" "$1" \
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_dedupe ON entries(session_id, entry_type, content_hash) WHERE content_hash IS NOT NULL;" >/dev/null 2>&1
}

# Trace stamp — non-blocking, best-effort. tool_use_id is part of the entry
# content, so the dedupe key is unique per tool use: retried deliveries of
# the same event collapse while distinct tool calls are all kept.
if [ -n "$SESSION_ID" ] && [ -f "$JOURNAL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    TIMESTAMP=$(date +%s)
    ENTRY_CONTENT="pre_tool: ${TOOL_NAME:-unknown} (turn=${TURN_ID:-?}, use_id=${TOOL_USE_ID:-?})"
    ENTRY_HASH=$(_tp_entry_hash 'auto' "$ENTRY_CONTENT" '{}')
    {
        _tp_ensure_dedupe_schema "$JOURNAL_DB"
        sqlite3 -cmd ".timeout 5000" "$JOURNAL_DB" \
            "INSERT OR IGNORE INTO entries (session_id, timestamp, entry_type, content, metadata_json, content_hash)
             VALUES ('$SESSION_ID', $TIMESTAMP, 'auto', '$ENTRY_CONTENT', '{}', NULLIF('$ENTRY_HASH', ''));" 2>/dev/null
    } &
fi

# Budget gate — only enforce if TOKENPAK_COMPANION_BUDGET is set.
BUDGET="${TOKENPAK_COMPANION_BUDGET:-0}"
if [ "$BUDGET" != "0" ] && [ -n "$BUDGET" ] && [ -f "$BUDGET_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    TODAY=$(date +%Y-%m-%d)
    # Truthful daily spend: per (session, day) sum actual rows when any
    # exist (rows with a model are actuals), otherwise take the largest
    # estimate — counts each message once. Mirrors the python readers
    # (companion/_sqlite.py DAILY_SPEND_SQL) without referencing the 'kind'
    # column so it also works on not-yet-migrated databases.
    DAILY_TOTAL=$(sqlite3 -cmd ".timeout 5000" "$BUDGET_DB" \
        "SELECT COALESCE(SUM(session_spend), 0) FROM (
             SELECT CASE
                 WHEN SUM(CASE WHEN model != '' THEN 1 ELSE 0 END) > 0
                 THEN SUM(CASE WHEN model != '' THEN estimated_cost ELSE 0 END)
                 ELSE MAX(estimated_cost)
             END AS session_spend
             FROM companion_costs WHERE date = '$TODAY' GROUP BY session_id
         );" 2>/dev/null || echo "0.0")
    [ -z "$DAILY_TOTAL" ] && DAILY_TOTAL="0.0"
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
