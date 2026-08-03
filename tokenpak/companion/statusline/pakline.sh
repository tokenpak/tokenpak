#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────────────────
# PakLine v0 — TokenPak live telemetry line for Claude Code's statusLine.
#
# Truthful-first: renders ONLY fields sourceable today, from authoritative
# sources. No faked precision, no `n/a`, no TokenPak attribution it didn't earn.
#
#   📦 <task> · $<cost> · context <state> · active <duration>
#
# Sources (all O(1) — perf target <50ms, hard cap <100ms):
#   • task     — companion title-state file ($JOURNAL_DIR/titles/<session_id>),
#                written by the UserPromptSubmit title hook; else stdin model.
#   • cost     — statusLine stdin  cost.total_cost_usd        (exact, Claude Code)
#   • context  — statusLine stdin  exceeds_200k_tokens        (state, Claude Code)
#   • duration — statusLine stdin  cost.total_duration_ms     (exact, Claude Code)
#
# Deliberately NOT done here (see pakline provenance-spec.md):
#   • no transcript scan, no SQLite query, no live cache computation
#   • observed cache / TP saved / PAKs / todos are v1+/v2 and must come from a
#     precomputed, session-scoped, provenance-gated source — never live here.
#
# Disable with TOKENPAK_COMPANION_PAKLINE=0.
# ──────────────────────────────────────────────────────────────────────────

[ "${TOKENPAK_COMPANION_PAKLINE:-1}" = "0" ] && exit 0

INPUT=$(cat)

# jq required for safe parsing; degrade to silent (no statusline) without it.
command -v jq >/dev/null 2>&1 || exit 0

# Single jq pass extracts every field at once (parse the payload once). Each
# interpolation is forced to a string so @sh can shell-quote it for safe eval.
eval "$(printf '%s' "$INPUT" | jq -r '@sh "SESSION_ID=\(.session_id // "") COST=\((.cost.total_cost_usd // 0)|tostring) DUR_MS=\((.cost.total_duration_ms // 0)|tostring) MODEL=\(.model.display_name // "") OVER200=\((.exceeds_200k_tokens // false)|tostring)"' 2>/dev/null)"

# ── task ── title-state file (set by the title hook) → else model → "session"
TASK=""
TITLE_FILE="${TOKENPAK_COMPANION_JOURNAL_DIR:-$HOME/.tokenpak/companion}/titles/${SESSION_ID}"
if [ -n "$SESSION_ID" ] && [ -s "$TITLE_FILE" ]; then
    # First line only, capped — the file holds the prompt-derived short title.
    TASK=$(head -n1 "$TITLE_FILE" 2>/dev/null | cut -c1-48)
fi
[ -z "$TASK" ] && TASK="$MODEL"
[ -z "$TASK" ] && TASK="session"

# ── cost ── exact, from Claude Code. Always truthful (shows $0.00 at start).
case "$COST" in ''|*[!0-9.]*) COST=0 ;; esac
COST_FMT=$(printf '$%.2f' "$COST" 2>/dev/null)
[ -n "$COST_FMT" ] || COST_FMT='$0.00'

# ── context ── v0 is STATE ONLY (exact boolean). No fabricated percentage.
case "$OVER200" in
    true) CTX="context high" ;;
    *)    CTX="context OK" ;;
esac

# ── duration ── ms → human; omit when unknown/zero.
DUR=""
DUR_MS_INT="${DUR_MS%%.*}"
case "$DUR_MS_INT" in ''|*[!0-9]*) DUR_MS_INT=0 ;; esac
DUR_S=$(( DUR_MS_INT / 1000 ))
if [ "$DUR_S" -gt 0 ]; then
    if   [ "$DUR_S" -ge 3600 ]; then DUR="active $(( DUR_S / 3600 ))h$(( (DUR_S % 3600) / 60 ))m"
    elif [ "$DUR_S" -ge 60 ];   then DUR="active $(( DUR_S / 60 ))m"
    else                             DUR="active ${DUR_S}s"
    fi
fi

# ── compose ── omit empties cleanly; no separators around missing fields.
OUT="📦 ${TASK} · ${COST_FMT} · ${CTX}"
[ -n "$DUR" ] && OUT="${OUT} · ${DUR}"
printf '%s' "$OUT"
