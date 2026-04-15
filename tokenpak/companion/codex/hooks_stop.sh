#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# Codex Stop hook — session/turn closeout, journal persistence.
#
# Reads JSON from stdin with: session_id, transcript_path, cwd,
# hook_event_name, model, assistant_message.
#
# Actions:
#   - Writes closeout journal entry with assistant summary
#   - Records final cost estimate to budget tracker
#   - Always exits 0 (never blocks Stop)
# ──────────────────────────────────────────────────────────────

# Read stdin
INPUT=$(cat)

# Quick exit if companion disabled
[ "${TOKENPAK_COMPANION_ENABLED:-1}" = "0" ] && exit 0

JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"

# Parse JSON fields
if command -v jq >/dev/null 2>&1; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
    TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
    MODEL=$(echo "$INPUT" | jq -r '.model // empty' 2>/dev/null)
else
    SESSION_ID=$(echo "$INPUT" | sed -n 's/.*"session_id"\s*:\s*"\([^"]*\)".*/\1/p')
    TRANSCRIPT=$(echo "$INPUT" | sed -n 's/.*"transcript_path"\s*:\s*"\([^"]*\)".*/\1/p')
    MODEL=$(echo "$INPUT" | sed -n 's/.*"model"\s*:\s*"\([^"]*\)".*/\1/p')
fi

[ -z "$SESSION_ID" ] && exit 0

# Estimate final session size from transcript
TOKENS=0
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    FILE_SIZE=$(stat -c%s "$TRANSCRIPT" 2>/dev/null || stat -f%z "$TRANSCRIPT" 2>/dev/null || echo 0)
    TOKENS=$((FILE_SIZE / 4))
fi

TOKENS_FMT=$(printf '%d' "$TOKENS" | rev | sed 's/.\{3\}/&,/g' | rev | sed 's/^,//')

# Write closeout journal entry
JOURNAL_DB="$JOURNAL_DIR/journal.db"
if [ -f "$JOURNAL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    TIMESTAMP=$(date +%s)
    sqlite3 "$JOURNAL_DB" \
        "INSERT OR IGNORE INTO entries (session_id, timestamp, entry_type, content, metadata_json)
         VALUES ('$SESSION_ID', $TIMESTAMP, 'auto', 'session stopped (~${TOKENS_FMT} total tokens, model: ${MODEL:-unknown})', '{}');" 2>/dev/null

    # Update session end time if sessions table exists
    sqlite3 "$JOURNAL_DB" \
        "UPDATE sessions SET ended_at = $TIMESTAMP, total_requests = (
             SELECT COUNT(*) FROM entries WHERE session_id = '$SESSION_ID' AND entry_type = 'auto'
         ) WHERE session_id = '$SESSION_ID';" 2>/dev/null
fi

# Record final cost estimate to budget tracker
BUDGET_DB="$JOURNAL_DIR/budget.db"
if [ -f "$BUDGET_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    # Resolve model rate (same logic as pre_send)
    RATE=3
    case "${MODEL:-}" in
        gpt-4o-mini*)  RATE=0 ;;
        gpt-4o*)       RATE=3 ;;
        gpt-4.1-nano*) RATE=0 ;;
        gpt-4.1-mini*) RATE=0 ;;
        gpt-4.1*)      RATE=2 ;;
        o3-mini*)      RATE=1 ;;
        o4-mini*)      RATE=1 ;;
        o1-mini*)      RATE=1 ;;
        o3*)           RATE=10 ;;
        o1*)           RATE=15 ;;
        *opus*)        RATE=15 ;;
        *sonnet*)      RATE=3 ;;
        *haiku*)       RATE=1 ;;
    esac

    COST_MICRO=$((TOKENS * RATE / 1000))
    COST_DOLLARS="$((COST_MICRO / 1000)).$(printf '%06d' $((COST_MICRO % 1000000)))"
    TODAY=$(date +%Y-%m-%d)
    TIMESTAMP=$(date +%s)

    sqlite3 "$BUDGET_DB" \
        "INSERT INTO companion_costs (timestamp, date, session_id, model, input_tokens, cached_tokens, output_tokens, estimated_cost)
         VALUES ($TIMESTAMP, '$TODAY', '$SESSION_ID', '${MODEL:-unknown}', $TOKENS, 0, 0, $COST_DOLLARS);" 2>/dev/null
fi

if [ "${TOKENPAK_COMPANION_SHOW_COST:-1}" != "0" ]; then
    printf 'tokenpak: session closeout (~%s tokens)\n' "$TOKENS_FMT" >&2
fi

exit 0
