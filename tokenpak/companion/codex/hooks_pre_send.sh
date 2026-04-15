#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# Codex UserPromptSubmit hook — pure bash, ~30ms target.
#
# Reads JSON from stdin with: session_id, transcript_path, cwd,
# hook_event_name, model, prompt.
#
# Outputs:
#   - Cost estimate to stderr (visible in Codex TUI)
#   - Budget block via exit code 2 if over limit
#   - hookSpecificOutput JSON to stdout on success
# ──────────────────────────────────────────────────────────────

# Read stdin
INPUT=$(cat)

# Quick exit if companion disabled
[ "${TOKENPAK_COMPANION_ENABLED:-1}" = "0" ] && exit 0

# Parse JSON fields — try jq first (fastest), fall back to sed (portable)
if command -v jq >/dev/null 2>&1; then
    TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
    MODEL=$(echo "$INPUT" | jq -r '.model // empty' 2>/dev/null)
else
    TRANSCRIPT=$(echo "$INPUT" | sed -n 's/.*"transcript_path"\s*:\s*"\([^"]*\)".*/\1/p')
    SESSION_ID=$(echo "$INPUT" | sed -n 's/.*"session_id"\s*:\s*"\([^"]*\)".*/\1/p')
    MODEL=$(echo "$INPUT" | sed -n 's/.*"model"\s*:\s*"\([^"]*\)".*/\1/p')
fi

# Token estimation from transcript file size (instant via stat)
TOKENS=0
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    FILE_SIZE=$(stat -c%s "$TRANSCRIPT" 2>/dev/null || stat -f%z "$TRANSCRIPT" 2>/dev/null || echo 0)
    TOKENS=$((FILE_SIZE / 4))
fi

[ "$TOKENS" -eq 0 ] && exit 0

# Format token count with thousands separators (pure bash)
TOKENS_FMT=$(printf '%d' "$TOKENS" | rev | sed 's/.\{3\}/&,/g' | rev | sed 's/^,//')

# Model-aware cost estimation (USD per 1M tokens, integer math in microdollars)
# Resolve rate based on model name
RATE=3  # default: sonnet-equivalent ($3/M)
case "${MODEL:-}" in
    gpt-4o-mini*)  RATE=0 ;;             # $0.15/M — rounds to 0 in microdollars at this scale
    gpt-4o*)       RATE=3 ;;             # $2.50/M — round to 3 for estimation
    gpt-4.1-nano*) RATE=0 ;;             # $0.10/M
    gpt-4.1-mini*) RATE=0 ;;             # $0.40/M
    gpt-4.1*)      RATE=2 ;;             # $2.00/M
    o3-mini*)      RATE=1 ;;             # $1.10/M
    o4-mini*)      RATE=1 ;;             # $1.10/M
    o1-mini*)      RATE=1 ;;             # $1.10/M
    o3*)           RATE=10 ;;            # $10.00/M
    o1*)           RATE=15 ;;            # $15.00/M
    *opus*)        RATE=15 ;;            # $15.00/M
    *sonnet*)      RATE=3 ;;             # $3.00/M
    *haiku*)       RATE=1 ;;             # $0.80/M
esac

COST_MICRO=$((TOKENS * RATE / 1000))
COST_DOLLARS="$((COST_MICRO / 1000)).$(printf '%04d' $((COST_MICRO % 1000)))"

# Budget check (only if TOKENPAK_COMPANION_BUDGET is set and > 0)
BUDGET="${TOKENPAK_COMPANION_BUDGET:-0}"
BUDGET_TAG=""

if [ "$BUDGET" != "0" ] && [ -n "$BUDGET" ]; then
    JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"
    BUDGET_DB="$JOURNAL_DIR/budget.db"
    TODAY=$(date +%Y-%m-%d)
    DAILY_TOTAL="0.0"

    if [ -f "$BUDGET_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
        DAILY_TOTAL=$(sqlite3 "$BUDGET_DB" \
            "SELECT COALESCE(SUM(estimated_cost), 0) FROM companion_costs WHERE date = '$TODAY';" 2>/dev/null || echo "0.0")
    fi

    # Compare using integer microdollars
    BUDGET_MICRO=$(echo "$BUDGET * 1000000" | bc 2>/dev/null | cut -d. -f1 || echo 0)
    DAILY_MICRO=$(echo "$DAILY_TOTAL * 1000000" | bc 2>/dev/null | cut -d. -f1 || echo 0)
    EST_MICRO=$((TOKENS * RATE))

    if [ "$((DAILY_MICRO + EST_MICRO))" -gt "${BUDGET_MICRO:-0}" ] 2>/dev/null; then
        echo "tokenpak: budget exceeded (\$$DAILY_TOTAL / \$$BUDGET daily)" >&2
        exit 2
    fi

    # Budget percentage tag
    if [ "${BUDGET_MICRO:-0}" -gt 0 ] 2>/dev/null; then
        PCT=$((DAILY_MICRO * 100 / BUDGET_MICRO))
        [ "$PCT" -gt 50 ] && BUDGET_TAG="  budget ${PCT}%"
    fi
fi

# Print cost estimate to stderr (visible in Codex TUI)
if [ "${TOKENPAK_COMPANION_SHOW_COST:-1}" != "0" ]; then
    MODEL_TAG=""
    [ -n "$MODEL" ] && MODEL_TAG=" ($MODEL)"
    printf 'tokenpak: ~%s tokens  est $%s%s%s\n' "$TOKENS_FMT" "$COST_DOLLARS" "$MODEL_TAG" "$BUDGET_TAG" >&2
fi

# Journal seed — write auto entry (non-blocking, fire and forget)
if [ -n "$SESSION_ID" ]; then
    JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"
    JOURNAL_DB="$JOURNAL_DIR/journal.db"
    if [ -f "$JOURNAL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
        TIMESTAMP=$(date +%s)
        sqlite3 "$JOURNAL_DB" \
            "INSERT OR IGNORE INTO entries (session_id, timestamp, entry_type, content, metadata_json)
             VALUES ('$SESSION_ID', $TIMESTAMP, 'auto', 'prompt submitted (~${TOKENS_FMT} tokens, est \$$COST_DOLLARS, model: ${MODEL:-unknown})', '{}');" 2>/dev/null &
    fi
fi

exit 0
