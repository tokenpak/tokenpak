#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# Codex Stop hook — session/turn closeout, journal persistence.
#
# Reads JSON from stdin with: session_id, transcript_path, cwd,
# hook_event_name, model, stop_hook_active, last_assistant_message.
#
# Actions:
#   - Writes closeout journal entry
#   - Records final cost estimate to budget tracker
#   - Always exits 0 (never blocks Stop)
# ──────────────────────────────────────────────────────────────

INPUT=$(cat)

[ "${TOKENPAK_COMPANION_ENABLED:-1}" = "0" ] && exit 0

JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"
SQLITE_TIMEOUT_SECONDS="${TOKENPAK_COMPANION_SQLITE_TIMEOUT_SECONDS:-2}"
SQLITE_BUSY_MS="${TOKENPAK_COMPANION_SQLITE_BUSY_MS:-1000}"

case "$SQLITE_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) SQLITE_TIMEOUT_SECONDS=2 ;;
esac
case "$SQLITE_BUSY_MS" in
    ''|*[!0-9]*) SQLITE_BUSY_MS=1000 ;;
esac

sqlite_best_effort() {
    db="$1"
    sql="$2"
    [ -f "$db" ] || return 0
    command -v sqlite3 >/dev/null 2>&1 || return 0

    if command -v timeout >/dev/null 2>&1; then
        timeout "$SQLITE_TIMEOUT_SECONDS" sqlite3 -cmd ".timeout $SQLITE_BUSY_MS" "$db" "$sql" >/dev/null 2>&1 || true
    else
        sqlite3 -cmd ".timeout $SQLITE_BUSY_MS" "$db" "$sql" >/dev/null 2>&1 || true
    fi
}

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

TOKENS=0
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    FILE_SIZE=$(stat -c%s "$TRANSCRIPT" 2>/dev/null || stat -f%z "$TRANSCRIPT" 2>/dev/null || echo 0)
    TOKENS=$((FILE_SIZE / 4))
fi

TOKENS_FMT=$(printf '%d' "$TOKENS" | rev | sed 's/.\{3\}/&,/g' | rev | sed 's/^,//')

JOURNAL_DB="$JOURNAL_DIR/journal.db"
if [ -f "$JOURNAL_DB" ]; then
    TIMESTAMP=$(date +%s)
    sqlite_best_effort "$JOURNAL_DB" \
        "INSERT OR IGNORE INTO entries (session_id, timestamp, entry_type, content, metadata_json)
         VALUES ('$SESSION_ID', $TIMESTAMP, 'auto', 'session stopped (~${TOKENS_FMT} total tokens, model: ${MODEL:-unknown})', '{}');"

    sqlite_best_effort "$JOURNAL_DB" \
        "UPDATE sessions SET ended_at = $TIMESTAMP, total_requests = (
             SELECT COUNT(*) FROM entries WHERE session_id = '$SESSION_ID' AND entry_type = 'auto'
         ) WHERE session_id = '$SESSION_ID';"
fi

BUDGET_DB="$JOURNAL_DIR/budget.db"
if [ -f "$BUDGET_DB" ]; then
    # Rate lookup shares the same TSV snapshot as pre_send (single source).
    RATES_FILE="${TOKENPAK_COMPANION_RATES_FILE:-$HOME/.tokenpak/companion/run/model_rates.tsv}"
    RATE=3
    if [ -n "$MODEL" ] && [ -f "$RATES_FILE" ]; then
        RATE=$(awk -F'\t' -v m="$MODEL" '$1 == m { print $2; exit }' "$RATES_FILE" 2>/dev/null)
        if [ -z "$RATE" ]; then
            RATE=$(awk -F'\t' -v m="$MODEL" '
                BEGIN { best_len = 0; best = "" }
                index(m, $1) == 1 && length($1) > best_len { best_len = length($1); best = $2 }
                END { if (best != "") print best }
            ' "$RATES_FILE" 2>/dev/null)
        fi
        [ -z "$RATE" ] && RATE=3
    fi

    COST_MICRO=$((TOKENS * RATE / 1000))
    COST_DOLLARS="$((COST_MICRO / 1000)).$(printf '%06d' $((COST_MICRO % 1000000)))"
    TODAY=$(date +%Y-%m-%d)
    TIMESTAMP=$(date +%s)

    sqlite_best_effort "$BUDGET_DB" \
        "INSERT INTO companion_costs (timestamp, date, session_id, model, input_tokens, cached_tokens, output_tokens, estimated_cost)
         VALUES ($TIMESTAMP, '$TODAY', '$SESSION_ID', '${MODEL:-unknown}', $TOKENS, 0, 0, $COST_DOLLARS);"
fi

if [ "${TOKENPAK_COMPANION_SHOW_COST:-1}" != "0" ]; then
    printf 'tokenpak: session closeout (~%s tokens)\n' "$TOKENS_FMT" >&2
fi

exit 0
