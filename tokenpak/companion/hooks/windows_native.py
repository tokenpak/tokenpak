# SPDX-License-Identifier: Apache-2.0
"""Native Claude session binding/title hooks and a content-free Windows footer."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tokenpak.status.binding import ENV, valid_session, write_session


def handle(payload: dict, action: str) -> int:
    sid = payload.get("session_id")
    if not valid_session(sid):
        raise ValueError("native Claude session ID is missing or invalid")
    directory = os.environ.get(ENV)
    if action == "bind":
        if payload.get("hook_event_name") != "SessionStart":
            raise ValueError("binding requires SessionStart")
        if directory:
            write_session(sid)
    elif action == "title":
        if directory and payload.get("source") == "clear":
            path = Path(directory) / "session_title.json"
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                print(json.dumps(data))
    elif action == "status":
        # Use the host's own current metrics. No network, database scan, background
        # worker or jq dependency on redraw; unknown values remain visibly unknown.
        context = payload.get("context_window") or {}
        used = context.get("used_percentage")
        cost = (payload.get("cost") or {}).get("total_cost_usd")
        parts = ["TokenPak", sid[:8]]
        if isinstance(used, (int, float)) and 0 <= used <= 100:
            parts.append(f"context {used:.0f}%")
        if isinstance(cost, (int, float)) and cost >= 0:
            parts.append(f"session ${cost:.4f}")
        print(" | ".join(parts))
    else:
        raise ValueError("unknown native hook action")
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("hook input exceeds 1 MiB")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be an object")
        return handle(payload, sys.argv[1])
    except Exception as exc:
        print(f"tokenpak: native Claude hook failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
