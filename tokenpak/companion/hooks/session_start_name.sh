#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# SessionStart hook — restores the tokenpak session label after
# /clear (and other SessionStart events) so the top-HR chat-header
# label stays branded across the full TUI lifetime.
#
# Why this is needed:
#   The launcher passes ``--name "[ 📦 TokenPak Claude Companion ]"``
#   at startup, but ``/clear`` creates a *new* session (new session_id);
#   the original ``--name`` value is per-session and is lost. Without
#   this hook, the new session has no display name and the top-HR
#   reverts to default white/gray chrome with no branding.
#
# Output contract:
#   Claude Code's hooks reference defines a ``hookSpecificOutput`` JSON
#   object with ``sessionTitle`` for SessionStart. The TUI reads that
#   field and paints it in the top-HR chat-header on every redraw.
#
# This script is intentionally pure-bash (no python3, no jq required)
# so it stays under ~10ms — same constraint as ``pre_send.sh``.
# ──────────────────────────────────────────────────────────────

LABEL="${TOKENPAK_SESSION_LABEL:-[ 📦 TokenPak Claude Companion ]}"

# Emit the SessionStart hookSpecificOutput. Hand-rolled JSON keeps
# this jq-free; LABEL is a fixed/env-controlled literal so quoting
# is safe (no untrusted interpolation).
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "sessionTitle": "${LABEL}"
  }
}
EOF

exit 0
