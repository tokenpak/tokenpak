#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Bind only a managed launch. Quiet on every event, including /clear.
directory="${TOKENPAK_COMPANION_SESSION_DIR:-}"
[ -n "$directory" ] && [ -d "$directory" ] || { cat >/dev/null; exit 0; }
command -v jq >/dev/null 2>&1 || { cat >/dev/null; exit 0; }
sid=$(jq -er '.session_id | select(type == "string" and test("^[A-Za-z0-9_-]{1,64}$"))' 2>/dev/null)
[ -n "$sid" ] || exit 0
umask 077
temporary=$(mktemp "$directory/.session-XXXXXXXX") || exit 0
printf '%s\n' "$sid" > "$temporary" && mv -f "$temporary" "$directory/current-session"
[ -f "$temporary" ] && rm -f "$temporary"
exit 0
