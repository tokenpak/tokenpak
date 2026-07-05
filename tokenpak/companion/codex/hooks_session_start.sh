#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# Codex SessionStart hook — capsule auto-load + branded banner.
#
# Reads JSON from stdin with: session_id, transcript_path, cwd,
# hook_event_name, model, source (startup|resume|clear|compact).
#
# Actions:
#   - Insert a "session_start" journal entry (best-effort)
#   - Emit a branded banner to stderr, except on source=clear
#   - If a prior session for this cwd has a capsule_path, surface it
#     via Codex's SessionStart `systemMessage` JSON output, except on source=clear
#   - Always exit 0 (SessionStart cannot block)
# ──────────────────────────────────────────────────────────────

INPUT=$(cat)

[ "${TOKENPAK_COMPANION_ENABLED:-1}" = "0" ] && exit 0

if command -v jq >/dev/null 2>&1; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
    CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
    MODEL=$(echo "$INPUT" | jq -r '.model // empty' 2>/dev/null)
    SOURCE=$(echo "$INPUT" | jq -r '.source // "startup"' 2>/dev/null)
else
    SESSION_ID=$(echo "$INPUT" | sed -n 's/.*"session_id"\s*:\s*"\([^"]*\)".*/\1/p')
    CWD=$(echo "$INPUT" | sed -n 's/.*"cwd"\s*:\s*"\([^"]*\)".*/\1/p')
    MODEL=$(echo "$INPUT" | sed -n 's/.*"model"\s*:\s*"\([^"]*\)".*/\1/p')
    SOURCE=$(echo "$INPUT" | sed -n 's/.*"source"\s*:\s*"\([^"]*\)".*/\1/p')
    [ -z "$SOURCE" ] && SOURCE="startup"
fi

JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"
JOURNAL_DB="$JOURNAL_DIR/journal.db"
QUIET_CLEAR=0
[ "$SOURCE" = "clear" ] && QUIET_CLEAR=1

if [ -n "$SESSION_ID" ] && [ -f "$JOURNAL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    TIMESTAMP=$(date +%s)
    sqlite3 "$JOURNAL_DB" \
        "INSERT OR IGNORE INTO entries (session_id, timestamp, entry_type, content, metadata_json)
         VALUES ('$SESSION_ID', $TIMESTAMP, 'auto', 'session started (source: ${SOURCE}, model: ${MODEL:-unknown})', '{}');" 2>/dev/null
fi

# Capsule auto-load: look up most recent capsule_path for this cwd.
CAPSULE_PATH=""
if [ "$QUIET_CLEAR" != "1" ] && [ -n "$CWD" ] && [ -f "$JOURNAL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    CAPSULE_PATH=$(sqlite3 "$JOURNAL_DB" \
        "SELECT capsule_path FROM sessions
         WHERE project_dir = '$CWD' AND capsule_path IS NOT NULL AND capsule_path != ''
         ORDER BY started_at DESC LIMIT 1;" 2>/dev/null || echo "")
fi

# Branded banner to stderr (visible in Codex TUI).
if [ "$QUIET_CLEAR" != "1" ] && [ "${TOKENPAK_COMPANION_SHOW_BANNER:-1}" != "0" ]; then
    MODEL_TAG=""
    [ -n "$MODEL" ] && MODEL_TAG=" — $MODEL"
    printf 'tokenpak: session %s (%s)%s\n' "${SESSION_ID:0:8}" "$SOURCE" "$MODEL_TAG" >&2
fi

# Emit systemMessage JSON if we found a capsule to surface.
if [ -n "$CAPSULE_PATH" ]; then
    MSG="tokenpak: prior capsule available at $CAPSULE_PATH"
    ESCAPED=$(printf '%s' "$MSG" | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '{"systemMessage":"%s","continue":true}\n' "$ESCAPED"
fi

exit 0
