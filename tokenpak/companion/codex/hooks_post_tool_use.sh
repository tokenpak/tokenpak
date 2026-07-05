#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# Codex PostToolUse hook — token-out journal.
#
# Reads JSON from stdin with: session_id, transcript_path, cwd,
# hook_event_name, model, turn_id, tool_name, tool_use_id,
# tool_input, tool_response.
#
# Actions:
#   - Estimate output token count from tool_response (length / 4)
#   - Insert a per-tool token-out journal entry (best-effort)
#   - Always exit 0 (PostToolUse should not block; on a hard-cap
#     breach we emit a structured PostToolUse JSON note but still
#     return success so Codex sees the tool result)
# ──────────────────────────────────────────────────────────────

INPUT=$(cat)

[ "${TOKENPAK_COMPANION_ENABLED:-1}" = "0" ] && exit 0

if command -v jq >/dev/null 2>&1; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
    TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
    TOOL_USE_ID=$(echo "$INPUT" | jq -r '.tool_use_id // empty' 2>/dev/null)
    TURN_ID=$(echo "$INPUT" | jq -r '.turn_id // empty' 2>/dev/null)
    # tool_response can be a string or structured; coerce to JSON string for length.
    RESPONSE_STR=$(echo "$INPUT" | jq -c '.tool_response // ""' 2>/dev/null)
else
    SESSION_ID=$(echo "$INPUT" | sed -n 's/.*"session_id"\s*:\s*"\([^"]*\)".*/\1/p')
    TOOL_NAME=$(echo "$INPUT" | sed -n 's/.*"tool_name"\s*:\s*"\([^"]*\)".*/\1/p')
    TOOL_USE_ID=$(echo "$INPUT" | sed -n 's/.*"tool_use_id"\s*:\s*"\([^"]*\)".*/\1/p')
    TURN_ID=$(echo "$INPUT" | sed -n 's/.*"turn_id"\s*:\s*"\([^"]*\)".*/\1/p')
    RESPONSE_STR="$INPUT"  # fall back to whole input length without jq
fi

RESPONSE_BYTES=${#RESPONSE_STR}
RESPONSE_TOKENS=$((RESPONSE_BYTES / 4))

JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"
JOURNAL_DB="$JOURNAL_DIR/journal.db"

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

# tool_use_id is part of the entry content, so the dedupe key is unique per
# tool use: retried deliveries of the same event collapse while distinct
# tool calls are all kept.
if [ -n "$SESSION_ID" ] && [ -f "$JOURNAL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    TIMESTAMP=$(date +%s)
    ENTRY_CONTENT="post_tool: ${TOOL_NAME:-unknown} (~${RESPONSE_TOKENS} tokens out, turn=${TURN_ID:-?}, use_id=${TOOL_USE_ID:-?})"
    ENTRY_HASH=$(_tp_entry_hash 'auto' "$ENTRY_CONTENT" '{}')
    _tp_ensure_dedupe_schema "$JOURNAL_DB"
    sqlite3 -cmd ".timeout 5000" "$JOURNAL_DB" \
        "INSERT OR IGNORE INTO entries (session_id, timestamp, entry_type, content, metadata_json, content_hash)
         VALUES ('$SESSION_ID', $TIMESTAMP, 'auto', '$ENTRY_CONTENT', '{}', NULLIF('$ENTRY_HASH', ''));" 2>/dev/null
fi

# Optional hard-cap note: if a hard-cap env is set and this tool's
# response pushed past it, emit a structured PostToolUse JSON
# additionalContext note (visible in the Codex transcript) but still
# return success — the tool result has already happened.
HARDCAP="${TOKENPAK_COMPANION_RESPONSE_HARDCAP_TOKENS:-0}"
if [ "$HARDCAP" -gt 0 ] 2>/dev/null && [ "$RESPONSE_TOKENS" -gt "$HARDCAP" ] 2>/dev/null; then
    MSG="tokenpak: ${TOOL_NAME:-tool} response ~${RESPONSE_TOKENS} tokens exceeds hard cap ${HARDCAP}"
    REASON=$(printf '%s' "$MSG" | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$REASON"
fi

exit 0
