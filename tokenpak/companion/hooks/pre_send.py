"""UserPromptSubmit hook — pre-send pipeline for the tokenpak companion.

Claude Code invokes this as a subprocess when the user submits a prompt.
Protocol:
- Input:  JSON object on stdin with fields ``session_id``, ``transcript_path``,
          ``hook_event_name``, ``message``.
- Stderr: Human-readable status line printed for TUI display.
- Stdout: JSON (only when blocking; exit code 2).
- Exit 0: Allow the send.
- Exit 2: Block the send (companion must print JSON to stdout first).

This is the Wave 1 skeleton.  Wave 2 (COMP-06) will add:
  * Token count via tiktoken
  * Cost estimate
  * Budget gate (exit 2 when over budget)
  * Journal write
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict


def _read_input() -> Dict[str, Any]:
    """Read and parse the JSON payload from stdin."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def run(payload: Dict[str, Any]) -> int:
    """Execute the pre-send pipeline.

    Args:
        payload: Parsed hook payload dict.

    Returns:
        Exit code (0 = allow, 2 = block).
    """
    session_id = payload.get("session_id", "unknown")
    event = payload.get("hook_event_name", "UserPromptSubmit")
    message_preview = str(payload.get("message", ""))[:60]

    # Wave 1: log receipt to stderr so TUI shows activity; always allow.
    print(
        f"[tokenpak-companion] {event} session={session_id} msg={message_preview!r}",
        file=sys.stderr,
    )
    return 0


def main() -> None:
    payload = _read_input()
    sys.exit(run(payload))


if __name__ == "__main__":
    main()
