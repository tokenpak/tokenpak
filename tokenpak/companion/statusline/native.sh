#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Claude-native reader. No HTTP, database scan, Python or cache writes on redraw.
source "$(dirname "${BASH_SOURCE[0]}")/cache.sh"
columns=$(tp_columns)
[ "$columns" -gt 4 ] && columns=$((columns - 4))
TP_LINE="TokenPak | status unavailable"
if command -v jq >/dev/null 2>&1; then
    input=$(head -c 65537)
    cat >/dev/null
    if [ "${#input}" -le 65536 ]; then
        sid=$(printf '%s' "$input" | jq -er 'select(type == "object") | .session_id | select(type == "string" and test("^[A-Za-z0-9_-]{1,64}$"))' 2>/dev/null)
        tp_read_line "$sid" "$columns" "${TOKENPAK_COMPANION_SESSION_DIR:-}"
    fi
else
    cat >/dev/null
fi
tp_print_line "$columns"
exit 0
