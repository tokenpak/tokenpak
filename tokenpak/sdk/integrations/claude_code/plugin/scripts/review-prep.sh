#!/usr/bin/env bash
# review-prep.sh — PreToolUse: Bash hook (Pro-only)
#
# Blocks "git push" and "gh pr create" when the current branch has no recent
# prepare_review_packet artifact in ${CLAUDE_PLUGIN_DATA}/review-cache/.
#
# Requirements:
#   - tokenpak Pro license (excluded from OSS build by build-plugin CLI)
#   - Claude Code v2.1.85+  (if-field filtering in hooks.json)
#
# Bare-mode / CI note:
#   This hook does NOT fire in `claude -p --bare` mode because --bare skips
#   all plugin discovery.  Pro users running non-interactive / CI workflows
#   must pass --plugin-dir explicitly to opt the plugin in for that invocation.
#   See the README Pro section for a CI example.
#
# Agent SDK note:
#   This hook does NOT fire in the Anthropic Agent SDK unless the caller wires
#   up the equivalent callback via the tokenpak_hooks() helper (Pro path).
#
# Exit codes:
#   0  — allow the command through
#   2  — block: instruct the user to run /review-pack first
#
# Cache age:
#   Default 30 minutes.  Override with TOKENPAK_REVIEW_MAX_AGE_MINUTES env var
#   (configurable via userConfig.review_prep_max_age_minutes in a future task).
#
# Error policy:
#   Any internal failure (JSON parse error, git not found, etc.) fails OPEN
#   (exit 0) to avoid false-positive blocks.

# No -e: we fail open on errors rather than letting an unexpected exit code
# propagate as a block.
set -uo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_allow() { exit 0; }

_block() {
    echo "tokenpak review-prep: $*" >&2
    echo "  Run \`/review-pack\` to generate a review packet, then retry your command." >&2
    echo "  (Pro feature — requires Claude Code v2.1.85+ and a tokenpak Pro license)" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# 1. Read hook context from stdin
# ---------------------------------------------------------------------------
# Claude Code passes a JSON object to the hook's stdin:
#   {"tool_name":"Bash","command":"git push origin main", ...}
# or (newer format):
#   {"tool_name":"Bash","tool_input":{"command":"git push origin main"}, ...}

HOOK_JSON=$(cat 2>/dev/null) || _allow

# ---------------------------------------------------------------------------
# 2. Parse the command field
# ---------------------------------------------------------------------------

if command -v python3 >/dev/null 2>&1; then
    COMMAND=$(echo "$HOOK_JSON" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    # Support top-level 'command' and nested 'tool_input.command'
    cmd = d.get('command') or (d.get('tool_input') or {}).get('command', '')
    print(cmd)
except Exception:
    print('')
" 2>/dev/null) || _allow
else
    # Minimal fallback when python3 is unavailable (unlikely but safe)
    COMMAND=$(printf '%s' "$HOOK_JSON" \
        | grep -o '"command":"[^"]*"' \
        | head -1 \
        | sed 's/"command":"//;s/"$//') || true
fi

# ---------------------------------------------------------------------------
# 3. Belt-and-braces command-pattern check
# ---------------------------------------------------------------------------
# The hooks.json `if` field is the primary gate (v2.1.85+).  On older Claude
# Code versions that silently ignore `if`, this hook fires for every Bash call.
# This internal check ensures we never block a non-push/pr-create command.

echo "$COMMAND" | grep -qE '^git push|^gh pr create' || _allow

# ---------------------------------------------------------------------------
# 4. Determine the current git branch
# ---------------------------------------------------------------------------

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || _allow
[[ -n "$BRANCH" && "$BRANCH" != "HEAD" ]] || _allow   # detached HEAD — skip

# ---------------------------------------------------------------------------
# 5. Locate the review-cache directory
# ---------------------------------------------------------------------------
# The prepare_review_packet tool writes a cache file per branch:
#   ${CACHE_DIR}/<branch-name>        (plain marker)
#   ${CACHE_DIR}/<branch-name>.json   (JSON artifact)
# We accept either form.

CACHE_DIR="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/plugin-data/tokenpak-claude-code}/review-cache"
MAX_AGE_MINUTES="${TOKENPAK_REVIEW_MAX_AGE_MINUTES:-30}"

# Sanitise branch name for use as a filename (replace / with -)
SAFE_BRANCH="${BRANCH//\//-}"

CACHE_FILE=""
for candidate in \
        "${CACHE_DIR}/${SAFE_BRANCH}" \
        "${CACHE_DIR}/${SAFE_BRANCH}.json" \
        "${CACHE_DIR}/${BRANCH}" \
        "${CACHE_DIR}/${BRANCH}.json"; do
    if [[ -f "$candidate" ]]; then
        CACHE_FILE="$candidate"
        break
    fi
done

# ---------------------------------------------------------------------------
# 6. Check cache freshness
# ---------------------------------------------------------------------------

if [[ -n "$CACHE_FILE" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        FRESH=$(python3 -c "
import os, sys, time
try:
    age_min = (time.time() - os.path.getmtime(sys.argv[1])) / 60
    print('yes' if age_min < float(sys.argv[2]) else 'no')
except Exception:
    print('no')
" "$CACHE_FILE" "$MAX_AGE_MINUTES" 2>/dev/null) || FRESH="no"
    else
        # find -mmin returns the file if it was modified within N minutes
        RECENT=$(find "$CACHE_FILE" -mmin -"$MAX_AGE_MINUTES" 2>/dev/null || true)
        [[ -n "$RECENT" ]] && FRESH="yes" || FRESH="no"
    fi

    if [[ "$FRESH" == "yes" ]]; then
        _allow  # Fresh cache found — allow the push / pr-create
    fi

    _block "Review packet for branch '${BRANCH}' is older than ${MAX_AGE_MINUTES} minutes." \
        "Re-run \`/review-pack\` to refresh it."
fi

# ---------------------------------------------------------------------------
# 7. No cache file found — block and guide the user
# ---------------------------------------------------------------------------

_block "No review packet found for branch '${BRANCH}'."
