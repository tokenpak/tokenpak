"""Events CRUD and query methods mixin for TelemetryDB."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from tokenpak.telemetry.models import Cost, Segment, TelemetryEvent, Usage
from tokenpak.telemetry.storage_base import _now, _row_to_dict

# Type stub: these methods are provided by other mixins
# We declare them here to satisfy mypy when mixins are composed


class EventsMixin:
    """Mixin providing TelemetryEvent insert, insert_trace, and query methods."""

    _conn: sqlite3.Connection

    def insert_event(self, event: TelemetryEvent) -> None:
        """Persist a single :class:`TelemetryEvent`."""
        self._insert_events([event])

    def insert_events(self, events: list[TelemetryEvent]) -> None:
        """Batch-insert a list of :class:`TelemetryEvent` records."""
        self._insert_events(events)

    def _insert_events(self, events: list[TelemetryEvent]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_events
            (trace_id, request_id, event_type, ts, provider, model,
             agent_id, api, stop_reason, session_id, duration_ms,
             status, error_class, payload, span_id, node_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                e.trace_id,
                e.request_id,
                e.event_type,
                e.ts if e.ts else _now(),
                e.provider,
                e.model,
                e.agent_id,
                e.api,
                e.stop_reason,
                e.session_id,
                e.duration_ms,
                e.status,
                e.error_class,
                e.payload_json(),
                e.span_id,
                e.node_id,
            )
            for e in events
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    def insert_trace(
        self,
        event: TelemetryEvent,
        usage: Optional[Usage] = None,
        cost: Optional[Cost] = None,
        segments: Optional[list[Segment]] = None,
    ) -> None:
        """Insert all data for a single trace in one call.

        This is the preferred entry point for recording a completed LLM
        request/response cycle.  All four tables are updated atomically.

        Parameters
        ----------
        event:
            The lifecycle event for this trace.
        usage:
            Optional token-usage record.
        cost:
            Optional cost computation result.
        segments:
            Optional list of classified message segments.
        """
        self.insert_event(event)
        if usage is not None:
            self.insert_usage(usage)  # type: ignore  # type: ignore
        if cost is not None:
            self.insert_cost(cost)  # type: ignore  # type: ignore
        if segments:
            self.insert_segments(segments)  # type: ignore  # type: ignore

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        """Return all stored data for *trace_id* as a plain dict.

        Returns a dict with keys ``"event"``, ``"usage"``, ``"cost"``,
        ``"segments"``.  Missing tables return ``None`` / ``[]``.

        Parameters
        ----------
        trace_id:
            The trace identifier to look up.
        """
        cur = self._conn.cursor()

        cur.execute("SELECT * FROM tp_events WHERE trace_id = ? LIMIT 1", (trace_id,))
        event_row = cur.fetchone()
        event = _row_to_dict(cur, event_row) if event_row else None

        cur.execute("SELECT * FROM tp_usage WHERE trace_id = ?", (trace_id,))
        usage_row = cur.fetchone()
        usage = _row_to_dict(cur, usage_row) if usage_row else None

        cur.execute("SELECT * FROM tp_costs WHERE trace_id = ?", (trace_id,))
        cost_row = cur.fetchone()
        cost = _row_to_dict(cur, cost_row) if cost_row else None

        segments = self.get_segments(trace_id)  # type: ignore  # type: ignore

        return {
            "event": event,
            "usage": usage,
            "cost": cost,
            "segments": segments,
        }

    def get_trace_events(self, trace_id: str) -> list[dict[str, Any]]:
        """Return all pipeline events for a trace in chronological order.

        Parameters
        ----------
        trace_id:
            The trace identifier.

        Returns
        -------
        list of event dicts with event_id (request_id), event_type, ts, and payload
        """
        cur = self._conn.cursor()
        cur.execute(
            """SELECT request_id, event_type, ts, provider, model, agent_id,
                      api, stop_reason, session_id, duration_ms, status, error_class, payload
               FROM tp_events WHERE trace_id = ? ORDER BY ts ASC""",
            (trace_id,),
        )
        rows = cur.fetchall()
        events = []
        for r in rows:
            row_dict = _row_to_dict(cur, r)
            # Parse payload JSON
            payload_str = row_dict.pop("payload", "{}")
            try:
                payload = json.loads(payload_str) if payload_str else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            events.append(
                {
                    "event_id": row_dict["request_id"],
                    "event_type": row_dict["event_type"],
                    "timestamp": row_dict["ts"],
                    "provider": row_dict.get("provider"),
                    "model": row_dict.get("model"),
                    "agent_id": row_dict.get("agent_id"),
                    "duration_ms": row_dict.get("duration_ms"),
                    "status": row_dict.get("status"),
                    "payload": payload,
                }
            )
        return events

    def list_traces(
        self,
        limit: int = 100,
        offset: int = 0,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        since_ts: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Return a paginated list of trace event summaries.

        Parameters
        ----------
        limit:
            Maximum number of rows to return.
        offset:
            Row offset for pagination.
        provider:
            Filter by provider (exact match, case-sensitive).
        model:
            Filter by model (exact match, case-sensitive).
        agent_id:
            Filter by agent identifier.
        since_ts:
            Only return events with ``ts >= since_ts``.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if provider is not None:
            conditions.append("provider = ?")
            params.append(provider)
        if model is not None:
            conditions.append("model = ?")
            params.append(model)
        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if since_ts is not None:
            conditions.append("ts >= ?")
            params.append(since_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM tp_events {where} ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cur = self._conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]

    # ------------------------------------------------------------------
    # Pricing catalog snapshot
    # ------------------------------------------------------------------
