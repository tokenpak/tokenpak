#!/usr/bin/env bash
# session_start_name.sh — TokenPak Companion SessionStart hook.
#
# Claude Code fires SessionStart on session-creation events (startup,
# clear, resume, compact). The launcher passes the branded label via
# ``--name`` at startup, but ``--name`` is per-session: a /clear creates
# a new session that inherits no name, so the top-HR chat-header reverts
# to default chrome.
#
# This hook re-asserts the branded label by emitting
# ``hookSpecificOutput.sessionTitle``. Real ESC bytes are invalid in
# JSON strings, so the ANSI escapes are emitted as ``\u001b`` literals —
# the consumer's JSON parser decodes them back to ESC.
#
# Matcher: registered for ``"clear"`` (and reasonable to extend to
# ``"resume"`` / ``"compact"``) by the launcher.

# Branded session label — must stay in sync with
# ``tokenpak/companion/launcher.py::_DEFAULT_SESSION_LABEL``.
#
#   teal brackets + "Pak"   = \u001b[38;2;0;180;170m
#   white "📦 Token"         = \u001b[38;2;255;255;255m
#   gray "Claude Companion" = \u001b[38;2;90;94;105m
#   reset                   = \u001b[0m

cat <<'JSON'
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "sessionTitle": "\u001b[38;2;0;180;170m[ \u001b[38;2;255;255;255m📦 Token\u001b[38;2;0;180;170mPak\u001b[38;2;90;94;105m Claude Companion\u001b[38;2;0;180;170m ]\u001b[0m"
  }
}
JSON
