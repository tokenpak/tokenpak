#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# ──────────────────────────────────────────────────────────────
# SessionStart hook — restores the tokenpak session label after
# /clear (and other SessionStart events) so the top-HR chat-header
# label stays branded across the full TUI lifetime.
#
# Why this is needed:
#   The launcher passes ``--name "<branded label>"`` at startup, but
#   ``/clear`` creates a *new* session (new session_id); the original
#   ``--name`` value is per-session and is lost. Without this hook the
#   new session has no display name and the top-HR reverts to default
#   chrome with no branding.
#
# Output contract:
#   Claude Code's hooks reference defines a ``hookSpecificOutput`` JSON
#   object with ``sessionTitle`` for SessionStart. The TUI reads that
#   field and paints it in the top-HR chat-header on every redraw.
#
# Color treatment (foreground-only, no background fill):
#   brackets ``[ ``/`` ]``  TokenPak teal  (0,180,170)
#   ``📦 Token``            white          (255,255,255)
#   ``Pak``                 TokenPak teal  (0,180,170)
#   ``Claude Companion``    muted gray     (90,94,105)
#
#   ANSI escapes are emitted as JSON ``\u001b`` literals so the JSON
#   parser decodes them into real ESC bytes when Claude Code reads
#   the hook output. Plain ``\033`` would land in the JSON as four
#   literal characters and never render.
#
# This script is intentionally pure-bash (no python3, no jq required)
# so it stays under ~10ms — same constraint as ``pre_send.sh``.
# ──────────────────────────────────────────────────────────────

# JSON-form ANSI escapes (\u001b decodes to ESC client-side).
TEAL='\u001b[38;2;0;180;170m'
WHITE='\u001b[38;2;255;255;255m'
GRAY='\u001b[38;2;90;94;105m'
RESET='\u001b[0m'

# Default styled label. Honors TOKENPAK_SESSION_LABEL env override —
# when set, the user's literal value is used as-is (no styling
# injected, so they're free to ship plain text or their own ANSI).
LABEL="${TOKENPAK_SESSION_LABEL:-${TEAL}[ ${WHITE}📦 Token${TEAL}Pak${GRAY} Claude Companion${TEAL} ]${RESET}}"

# Hand-rolled JSON keeps this jq-free; LABEL is a fixed/env-controlled
# literal so quoting is safe (no untrusted interpolation).
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "sessionTitle": "${LABEL}"
  }
}
EOF

exit 0
