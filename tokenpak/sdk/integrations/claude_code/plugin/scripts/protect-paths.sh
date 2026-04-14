#!/usr/bin/env bash
# protect-paths.sh — Claude Code PreToolUse hook (Edit|Write)
# Blocks writes to sensitive paths.  Exit 2 = block; exit 0 = allow.
# Claude reads stderr when exit 2 is returned.
set -euo pipefail

# --- Read file_path from Claude Code hook JSON on stdin ---
file_path=$(jq -r '.file_path // empty' 2>/dev/null) || file_path=""

if [[ -z "$file_path" ]]; then
    exit 0
fi

basename_part="${file_path##*/}"

# --- Pattern matching ---
# In bash [[ ]], '*' matches any string including '/'.
# Three strategies per pattern:
#   1. full path vs pattern
#   2. basename vs pattern
#   3. for patterns starting with '**/', strip that prefix and retry (1)
match_pattern() {
    local path="$1" bn="$2" pat="$3"
    # shellcheck disable=SC2254
    [[ "$path" == $pat ]] && return 0
    # shellcheck disable=SC2254
    [[ "$bn" == $pat ]] && return 0
    if [[ "$pat" == '**/'* ]]; then
        local suffix="${pat#**/}"
        # shellcheck disable=SC2254
        [[ "$path" == $suffix ]] && return 0
    fi
    return 1
}

# --- Default conservative deny list ---
DEFAULT_PATTERNS=(
    '.env*'
    '**/credentials*'
    '**/migrations/**'
    '**/secrets/**'
    '.git/**'
)

# --- Block helper ---
block_path() {
    echo "protected path: $file_path (override via .tokenpak-protected in project root)" >&2
    exit 2
}

# --- Check defaults ---
for pat in "${DEFAULT_PATTERNS[@]}"; do
    if match_pattern "$file_path" "$basename_part" "$pat"; then
        block_path
    fi
done

# --- Check project-local overrides (.tokenpak-protected, one glob per line) ---
# Silently skip if file is absent or unreadable.
if [[ -f ".tokenpak-protected" && -r ".tokenpak-protected" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip blank lines and comments
        if [[ -z "$line" || "$line" == '#'* ]]; then
            continue
        fi
        if match_pattern "$file_path" "$basename_part" "$line"; then
            block_path
        fi
    done < ".tokenpak-protected"
fi

exit 0
