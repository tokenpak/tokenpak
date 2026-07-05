#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# Ultra-lean UserPromptSubmit hook — pure bash, ~30ms target.
#
# No python3 in the hot path. JSON fields extracted with grep.
# Budget check uses sqlite3 CLI (only if budget is set).
# ──────────────────────────────────────────────────────────────

# Read stdin
INPUT=$(cat)

# Quick exit if companion disabled
[ "${TOKENPAK_COMPANION_ENABLED:-1}" = "0" ] && exit 0

# Parse JSON fields — try jq first (fastest), fall back to sed (portable)
if command -v jq >/dev/null 2>&1; then
    TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
else
    # Portable sed extraction (no -P flag needed)
    TRANSCRIPT=$(echo "$INPUT" | sed -n 's/.*"transcript_path"\s*:\s*"\([^"]*\)".*/\1/p')
    SESSION_ID=$(echo "$INPUT" | sed -n 's/.*"session_id"\s*:\s*"\([^"]*\)".*/\1/p')
fi

# Session-binding marker (atomic tmp+mv): the companion MCP server — a
# separate long-lived process — binds its active session id from this
# run-dir file. Without it, a stale marker from an earlier session causes
# cross-session misattribution of journal/budget writes. Mirrors
# pre_send.py:_write_session_marker; must run BEFORE any early exit.
if [ -n "$SESSION_ID" ]; then
    RUN_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}/run"
    _TP_CUR=""
    if [ -f "$RUN_DIR/current-session" ]; then
        IFS= read -r _TP_CUR < "$RUN_DIR/current-session" 2>/dev/null
    fi
    # Hot-path guard: rewrite only on session change (builtin read, no spawns).
    if [ "$_TP_CUR" != "$SESSION_ID" ]; then
        mkdir -p "$RUN_DIR" 2>/dev/null
        if printf '%s' "$SESSION_ID" > "$RUN_DIR/current-session.$$.tmp" 2>/dev/null; then
            mv -f "$RUN_DIR/current-session.$$.tmp" "$RUN_DIR/current-session" 2>/dev/null \
                || rm -f "$RUN_DIR/current-session.$$.tmp" 2>/dev/null
        fi
    fi
fi

# Token estimation from file size (instant via stat)
TOKENS=0
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    FILE_SIZE=$(stat -c%s "$TRANSCRIPT" 2>/dev/null || stat -f%z "$TRANSCRIPT" 2>/dev/null || echo 0)
    TOKENS=$((FILE_SIZE / 4))
fi

[ "$TOKENS" -eq 0 ] && exit 0

# Format token count with thousands separators (pure bash)
TOKENS_FMT=$(printf '%d' "$TOKENS" | rev | sed 's/.\{3\}/&,/g' | rev | sed 's/^,//')

# Cost estimation (sonnet rate: $3/M tokens)
# Integer math in microdollars to avoid float
COST_MICRO=$((TOKENS * 3 / 1000))
COST_DOLLARS="$(( COST_MICRO / 1000 )).$(printf '%04d' $(( COST_MICRO % 1000 )) )"

# Budget check (only if TOKENPAK_COMPANION_BUDGET is set and > 0)
BUDGET="${TOKENPAK_COMPANION_BUDGET:-0}"
BUDGET_TAG=""

if [ "$BUDGET" != "0" ] && [ -n "$BUDGET" ]; then
    JOURNAL_DIR="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}"
    BUDGET_DB="$JOURNAL_DIR/budget.db"
    TODAY=$(date +%Y-%m-%d)
    DAILY_TOTAL="0.0"

    # Truthful daily spend: per (session, day) sum the actual rows when any
    # exist (rows with a model are actuals), otherwise take the largest
    # estimate — counts each message once instead of summing the cumulative
    # pre-send estimate series plus actuals. Mirrors the python readers
    # (companion/_sqlite.py DAILY_SPEND_SQL) without referencing the 'kind'
    # column so it also works on not-yet-migrated databases.
    if [ -f "$BUDGET_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
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
    fi

    # Compare using integer microdollars
    BUDGET_MICRO=$(echo "$BUDGET * 1000000" | bc 2>/dev/null | cut -d. -f1 || echo 0)
    DAILY_MICRO=$(echo "$DAILY_TOTAL * 1000000" | bc 2>/dev/null | cut -d. -f1 || echo 0)
    EST_MICRO=$((TOKENS * 3))

    if [ "$((DAILY_MICRO + EST_MICRO))" -gt "${BUDGET_MICRO:-0}" ] 2>/dev/null; then
        echo "tokenpak: budget exceeded (\$$DAILY_TOTAL / \$$BUDGET daily)" >&2
        printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","decision":"block","reason":"budget exceeded"}}\n'
        exit 2
    fi

    # Budget percentage tag
    if [ "${BUDGET_MICRO:-0}" -gt 0 ] 2>/dev/null; then
        PCT=$((DAILY_MICRO * 100 / BUDGET_MICRO))
        [ "$PCT" -gt 50 ] && BUDGET_TAG="  budget ${PCT}%"
    fi
fi

# Print cost estimate to stderr (visible in TUI)
if [ "${TOKENPAK_COMPANION_SHOW_COST:-1}" != "0" ]; then
    printf 'tokenpak: ~%s tokens  est $%s%s\n' "$TOKENS_FMT" "$COST_DOLLARS" "$BUDGET_TAG" >&2
fi

exit 0
