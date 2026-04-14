#!/usr/bin/env bash
# post-edit-validate.sh — Claude Code PostToolUse hook (Edit|Write)
# Runs project-specific validators on the single edited file. Advisory only:
#   exit 0 = ok (or disabled)
#   exit 1 = validator warning (non-blocking)
# Default-disabled. Enable by setting userConfig.enable_validation_hook=true
# in ~/.claude/settings.json, or via ENABLE_VALIDATION_HOOK=true env var.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(dirname "$SCRIPT_DIR")"
VALIDATORS_JSON="${PLUGIN_DIR}/validators.json"
DEFAULT_TIMEOUT=5

# ---------------------------------------------------------------------------
# Short-circuit: check enabled state before consuming stdin
# ---------------------------------------------------------------------------

# 1) Env var override — used for testing without touching settings.json.
#    ENABLE_VALIDATION_HOOK=false → disabled; =true → enabled (bypasses file check).
if [[ -n "${ENABLE_VALIDATION_HOOK:-}" ]]; then
    if [[ "$ENABLE_VALIDATION_HOOK" != "true" ]]; then
        exit 0
    fi
    # ENABLE_VALIDATION_HOOK=true → skip settings.json check
else
    # 2) Read ~/.claude/settings.json userConfig key (production path).
    SETTINGS_FILE="${HOME}/.claude/settings.json"
    enabled=false
    if [[ -f "$SETTINGS_FILE" && -r "$SETTINGS_FILE" ]]; then
        enabled=$(jq -r '.userConfig.enable_validation_hook // false' "$SETTINGS_FILE" 2>/dev/null) || enabled=false
    fi
    if [[ "$enabled" != "true" ]]; then
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# Parse hook context from stdin
# ---------------------------------------------------------------------------

context=$(cat)
file_path=$(printf '%s' "$context" | jq -r '.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Look up validator for this file's extension
# ---------------------------------------------------------------------------

if [[ ! -f "$VALIDATORS_JSON" ]]; then
    exit 0
fi

# Extract extension (without leading dot, matching validators.json keys)
ext=""
basename_part="${file_path##*/}"
if [[ "$basename_part" == *.* ]]; then
    ext="${basename_part##*.}"
fi

if [[ -z "$ext" ]]; then
    exit 0
fi

validator=$(jq -r --arg ext "$ext" '.[$ext].command // empty' "$VALIDATORS_JSON" 2>/dev/null) || validator=""
timeout_val=$(jq -r --arg ext "$ext" '.[$ext].timeout // '"$DEFAULT_TIMEOUT" "$VALIDATORS_JSON" 2>/dev/null) || timeout_val="$DEFAULT_TIMEOUT"

if [[ -z "$validator" ]]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Run validator with timeout — advisory failure (exit 1, not exit 2)
# Per design: this hook is a warning, not a block.
# 5s is the UPPER BOUND on the budget, not a target. See README warning list.
# ---------------------------------------------------------------------------

# Capture both stdout and stderr; use || to prevent set -e from exiting early
# when the validator fails.
output=""
rc=0
output=$(timeout "${timeout_val}" bash -c "$validator \"\$1\"" _ "$file_path" 2>&1) || rc=$?

if [[ $rc -eq 0 ]]; then
    exit 0
elif [[ $rc -eq 124 ]]; then
    printf 'post-edit-validate: validator timed out after %ss: %s %s\n' \
        "$timeout_val" "$validator" "$file_path" >&2
    exit 1
else
    printf 'post-edit-validate: validator failed (exit %s): %s %s\n%s\n' \
        "$rc" "$validator" "$file_path" "$output" >&2
    exit 1
fi
