"""Segment CRUD mixin for TelemetryDB."""

from __future__ import annotations

import sqlite3
from typing import Any

from tokenpak.telemetry.models import Segment
from tokenpak.telemetry.storage_base import _row_to_dict


class SegmentsMixin:
    """Mixin providing Segment insert and query methods."""

    _conn: sqlite3.Connection

    def insert_segment(self, segment: Segment) -> None:
        """Persist a single :class:`Segment` record."""
        self._insert_segments([segment])

    def insert_segments(self, segments: list[Segment]) -> None:
        """Batch-insert a list of :class:`Segment` records."""
        self._insert_segments(segments)

    def _insert_segments(self, segments: list[Segment]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_segments
            (trace_id, segment_id, ord, segment_type, raw_hash, final_hash,
             raw_len, final_len, tokens_raw, tokens_after_qmd,
             tokens_after_tp, actions, relevance_score,
             segment_source, content_type, raw_len_chars, raw_len_bytes,
             final_len_chars, final_len_bytes, debug_ref)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                s.trace_id,
                s.segment_id,
                s.order,
                s.segment_type,
                s.raw_hash,
                s.final_hash,
                s.raw_len,
                s.final_len,
                s.tokens_raw,
                s.tokens_after_qmd,
                s.tokens_after_tp,
                s.actions,
                s.relevance_score,
                s.segment_source,
                s.content_type,
                s.raw_len_chars,
                s.raw_len_bytes,
                s.final_len_chars,
                s.final_len_bytes,
                s.debug_ref,
            )
            for s in segments
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Compound insert_trace (convenience)
    # ------------------------------------------------------------------

    def get_segments(self, trace_id: str) -> list[dict[str, Any]]:
        """Return all segment rows for *trace_id*, ordered by ``ord``.

        Parameters
        ----------
        trace_id:
            The trace identifier.
        """
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM tp_segments WHERE trace_id = ? ORDER BY ord",
            (trace_id,),
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(cur, r)
            # Remap DB column 'ord' → dataclass field 'order'
            if "ord" in d and "order" not in d:
                d["order"] = d.pop("ord")
            result.append(d)
        return result
