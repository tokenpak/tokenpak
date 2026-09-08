#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Cache-only adapter helpers. ASCII output: one byte is one display column.
tp_valid_session() { [[ "$1" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; }

tp_columns() {
    local rows cols
    if { read -r rows cols < <(stty size </dev/tty 2>/dev/null); } 2>/dev/null; then
        [[ "$cols" =~ ^[0-9]{1,4}$ ]] && [ "$cols" -gt 0 ] && { printf '%s' "$cols"; return; }
    fi
    cols="${COLUMNS:-80}"
    [[ "$cols" =~ ^[0-9]{1,4}$ ]] || cols=80
    printf '%s' "$cols"
}

tp_read_line() {
    local sid="$1" columns="$2" directory="$3" expires width text best="" now
    TP_LINE="TokenPak | status unavailable"
    tp_valid_session "$sid" || return
    [ -n "$directory" ] || return
    TP_LINE="TP ${sid:0:8} | waiting for data"
    [ -L "$directory/status/$sid.line" ] && return
    [ -f "$directory/status/$sid.line" ] || return
    # One open descriptor sees one atomic generation, including its expiry.
    {
        IFS= read -r expires || return
        [[ "$expires" =~ ^[0-9]{1,12}$ ]] || return
        now=$(date +%s)
        if [ "$now" -ge "$expires" ]; then
            TP_LINE="TP ${sid:0:8} | stale"
            return
        fi
        while IFS='|' read -r width text; do
            [[ "$width" =~ ^[0-9]{1,3}$ ]] || continue
            [ "$width" -le "$columns" ] && best="$text"
        done
    } < "$directory/status/$sid.line"
    [ -n "$best" ] && TP_LINE="$best"
}

tp_print_line() {
    local columns="$1"
    # All emitted bytes are ASCII. Refuse unsafe/oversized cache contents.
    if [[ "$TP_LINE" =~ [^\ -\~] ]] || [ "${#TP_LINE}" -gt "$columns" ]; then
        TP_LINE="TokenPak"
    fi
    [ "$columns" -ge 8 ] && printf '%s' "$TP_LINE"
}
