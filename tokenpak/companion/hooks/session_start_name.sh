#!/usr/bin/env bash
# session_start_name.sh — TokenPak Companion SessionStart hook.
#
# Claude Code fires SessionStart on session-creation events (startup,
# clear, resume, compact). The launcher passes the branded label via
# ``--name`` at startup, but ``--name`` is per-session: a /clear creates
# a new session that inherits no name, so the chat-header reverts to
# default chrome.
#
# This hook re-asserts the label by printing the payload the launcher
# generated at ``<run_dir>/session_title.json``. It deliberately carries
# NO copy of the label or of its escape sequences: the colors have a
# single definition in ``tokenpak/_formatting/colors.py``, the launcher
# renders the label from that definition, and this hook only replays the
# result. A second hand-written copy here would drift the moment the
# palette changed — which is exactly what it used to do.
#
# The file is absent when no label fits the current terminal width, in
# which case this hook stays silent and Claude Code keeps its own default
# label. That is the intended narrow-terminal behavior, not an error.

set -u

# Drain stdin so the caller never blocks writing the event payload.
cat >/dev/null 2>&1 || true

# The launcher registers this hook with the absolute path to the payload
# as $1. Deriving it here instead would mean re-deriving the run dir, and
# two plausible-looking defaults exist on disk on long-lived hosts — the
# argument removes the guess.
TITLE_FILE="${1:-}"
if [ -z "$TITLE_FILE" ]; then
    RUN_DIR="${TOKENPAK_COMPANION_RUN_DIR:-}"
    [ -n "$RUN_DIR" ] || exit 0
    TITLE_FILE="$RUN_DIR/session_title.json"
fi

[ -f "$TITLE_FILE" ] || exit 0

cat "$TITLE_FILE"
exit 0
