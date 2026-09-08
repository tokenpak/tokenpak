#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Continuous tmux reader. Only pre-rendered cache reads and an expiry check.
source "$(dirname "${BASH_SOURCE[0]}")/cache.sh"
directory="${1:-}"
while [ -d "$directory" ]; do
    sid=""
    IFS= read -r sid 2>/dev/null < "$directory/current-session" || true
    columns=$(tp_columns)
    [ "$columns" -gt 1 ] && columns=$((columns - 1))
    tp_read_line "$sid" "$columns" "$directory"
    printf '\033[H\033[2K'
    tp_print_line "$columns"
    sleep 1
done
