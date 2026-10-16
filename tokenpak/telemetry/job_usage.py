"""Read-only, content-free usage aggregates from the existing monitor ledger.

Missing optional columns remain unknown on historical rows. This reader never
initializes or migrates storage, and never selects prompt or response content.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from tokenpak.core.reasoning import REASONING_EFFORTS

_COUNTS = {
    "input_tokens": ("provider_input_tokens", "input_tokens"),
    "cached_tokens": ("provider_cache_read_tokens", "cache_read_tokens"),
    "output_tokens": ("provider_output_tokens", "output_tokens"),
    "reasoning_tokens": ("reasoning_tokens",),
}


def read_usage(
    db_path: str | Path,
    *,
    work_tag: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Aggregate requests by tag and by (tag, session), optionally filtering both.

    Token totals sum observed nonnegative counts; null means none were observed.
    The corresponding observed-request counts expose partial measurements.
    Cached tokens and reasoning tokens are subsets on some providers, so callers
    must not sum these counters to infer billing. Empty identities mean unknown.
    """
    path = Path(db_path).expanduser().resolve()
    empty = {"tags": [], "sessions": []}
    if not path.exists():
        return empty
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
        if not columns:
            return empty

        def field(name: str, fallback: str = "NULL") -> str:
            return f'"{name}"' if name in columns else fallback

        def text_field(name: str) -> str:
            return field(name, "''")

        selected = [
            f"{text_field(name)} AS {name}"
            for name in (
                "work_tag",
                "session_id",
                "served_model",
                "reasoning_effort",
                "reasoning_effort_raw",
                "reasoning_effort_source",
                "timestamp",
            )
        ]
        for name, candidates in _COUNTS.items():
            values = [field(candidate) for candidate in candidates]
            selected.append(f"COALESCE({', '.join([*values, 'NULL'])}) AS {name}")
        conditions, params = [], []
        for name, value in (
            ("work_tag", work_tag),
            ("session_id", session_id),
            ("user_id", user_id),
        ):
            if value is not None:
                conditions.append(f"{text_field(name)} = ?")
                params.append(value)
        sql = "SELECT " + ", ".join(selected) + " FROM requests"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        tags, sessions = {}, {}
        for row in conn.execute(sql, params):
            tag, session = row["work_tag"] or "", row["session_id"] or ""
            for groups, key, identity in (
                (tags, tag, {"work_tag": tag}),
                (sessions, (tag, session), {"work_tag": tag, "session_id": session}),
            ):
                group = groups.setdefault(key, _empty_group(identity))
                _add_row(group, row)
        return {
            "tags": [_finish(tags[key]) for key in sorted(tags)],
            "sessions": [_finish(sessions[key]) for key in sorted(sessions)],
        }


def _empty_group(identity: dict) -> dict:
    return {
        **identity,
        "request_count": 0,
        "served_models": set(),
        "unknown_model_requests": 0,
        "efforts": set(),
        "unknown_effort_requests": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        **{name: None for name in _COUNTS},
        **{name + "_observed_requests": 0 for name in _COUNTS},
    }


def _add_row(group: dict, row: sqlite3.Row) -> None:
    group["request_count"] += 1
    model = row["served_model"]
    if model:
        group["served_models"].add(model)
    else:
        group["unknown_model_requests"] += 1
    effort, raw, source = (
        row["reasoning_effort"] or "",
        row["reasoning_effort_raw"] or "",
        row["reasoning_effort_source"] or "",
    )
    if (
        not effort
        and raw in REASONING_EFFORTS
        and source in {"request_body_unrecognized", "provider_usage_object_unrecognized"}
    ):
        effort = raw
    if (
        effort in REASONING_EFFORTS
        and (not raw or raw == effort)
        and not (row["reasoning_effort"] and source.endswith("_unrecognized"))
    ):
        group["efforts"].add(effort)
    else:
        group["unknown_effort_requests"] += 1
    timestamp = row["timestamp"]
    if timestamp:
        group["first_timestamp"] = min(group["first_timestamp"] or timestamp, timestamp)
        group["last_timestamp"] = max(group["last_timestamp"] or timestamp, timestamp)
    for name in _COUNTS:
        value = row[name]
        if type(value) is int and value >= 0:
            group[name] = (group[name] or 0) + value
            group[name + "_observed_requests"] += 1


def _finish(group: dict) -> dict:
    return {
        **group,
        "served_models": sorted(group["served_models"]),
        "efforts": sorted(group["efforts"]),
    }
