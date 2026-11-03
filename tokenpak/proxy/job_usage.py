"""Authenticated usage endpoint returning only ledger counters and identities."""

import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

from tokenpak.telemetry.job_usage import read_usage


def handle_usage(handler, db_path) -> None:
    """Serve a request after the proxy's existing authentication gate."""
    query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
    status = 200
    if any(
        key not in {"work_tag", "session_id"} or len(values) != 1 for key, values in query.items()
    ):
        status, data = 400, {"error": "invalid_usage_filter"}
    else:
        try:
            data = read_usage(
                db_path,
                work_tag=query.get("work_tag", [None])[0],
                session_id=query.get("session_id", [None])[0],
                user_id=getattr(handler, "_tokenpak_user_id", None) or None,
            )
        except (sqlite3.Error, OSError):
            status, data = 503, {"error": "usage_ledger_unavailable"}
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def observed_model(body: bytes, *, streaming: bool = False) -> str:
    """Read the provider's model identity from a response copy, never the request."""
    from collections.abc import Mapping

    from tokenpak.proxy.streaming import iter_sse_events

    try:
        events = iter_sse_events(body) if streaming else (json.loads(body),)
        model = ""
        for event in events:
            if not isinstance(event, Mapping):
                continue
            for candidate in (event, event.get("response"), event.get("message")):
                if isinstance(candidate, Mapping):
                    value = candidate.get("model") or candidate.get("modelVersion")
                    if isinstance(value, str) and 0 < len(value) <= 256:
                        model = value
        return model
    except (ValueError, UnicodeError):
        return ""
