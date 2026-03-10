"""tokenpak.agent.dashboard.session_filter — DB-backed session filtering.

Queries the SQLite requests table (monitor.db) with server-side filtering
and pagination.

Supported filters (all optional):
    model   — exact model name match (e.g. "claude-sonnet-4-6")
    from    — ISO 8601 start datetime, inclusive (e.g. "2026-03-01T00:00:00")
    to      — ISO 8601 end datetime, inclusive
    status  — "all" | "success" | "error" | "partial"
              success → status_code 200-299
              error   → status_code >= 400
              partial → status_code 300-399

Pagination:
    limit   — max rows to return (default 50, max 500)
    offset  — row offset for pagination (default 0)

DB schema (requests table):
    id, timestamp, model, request_type, input_tokens, output_tokens,
    estimated_cost, latency_ms, status_code, endpoint, compilation_mode,
    protected_tokens, compressed_tokens, injected_tokens, injected_sources,
    cache_read_tokens, cache_creation_tokens
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB = Path.home() / ".openclaw" / "workspace" / ".ocp" / "monitor.db"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500

# Status filter → SQL fragment
_STATUS_SQL: Dict[str, str] = {
    "all": "",
    "success": "AND status_code BETWEEN 200 AND 299",
    "error": "AND status_code >= 400",
    "partial": "AND status_code BETWEEN 300 AND 399",
}

VALID_STATUSES = set(_STATUS_SQL.keys())

# Columns returned in each session row
SESSION_COLUMNS = [
    "id",
    "timestamp",
    "model",
    "request_type",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "latency_ms",
    "status_code",
    "endpoint",
    "compilation_mode",
]


# ---------------------------------------------------------------------------
# DB path helper
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    """Resolve the monitor DB path (env override or default)."""
    env = os.environ.get("TOKENPAK_DB", "")
    return Path(env) if env else _DEFAULT_DB


# ---------------------------------------------------------------------------
# FilterParams — validated input
# ---------------------------------------------------------------------------


class FilterParams:
    """Parsed and validated filter parameters."""

    def __init__(
        self,
        model: Optional[str] = None,
        from_dt: Optional[str] = None,
        to_dt: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> None:
        self.model: Optional[str] = model or None
        self.from_dt: Optional[str] = from_dt or None
        self.to_dt: Optional[str] = to_dt or None
        self.status: str = (status or "all").lower()
        self.limit: int = min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT)
        self.offset: int = max(int(offset or 0), 0)

        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status {self.status!r}. Must be one of: {sorted(VALID_STATUSES)}"
            )

    @classmethod
    def from_query_string(cls, qs: str) -> "FilterParams":
        """Parse from a URL query string (e.g. 'model=gpt-4o&status=success')."""
        from urllib.parse import parse_qs

        params = parse_qs(qs, keep_blank_values=False)

        def _first(key: str) -> Optional[str]:
            vals = params.get(key, [])
            return vals[0] if vals else None

        return cls(
            model=_first("model"),
            from_dt=_first("from"),
            to_dt=_first("to"),
            status=_first("status"),
            limit=int(_first("limit")) if _first("limit") else None,  # type: ignore[arg-type]
            offset=int(_first("offset")) if _first("offset") else None,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# SessionFilter — main query engine
# ---------------------------------------------------------------------------


class SessionFilter:
    """Server-side session filter backed by SQLite.

    Usage::

        sf = SessionFilter()
        result = sf.query(FilterParams(model="gpt-4o", status="success"))
        # result = {"sessions": [...], "total": N, "limit": 50, "offset": 0}
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _db_path()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, params: FilterParams) -> Dict[str, Any]:
        """Execute a filtered + paginated query.

        Returns::
            {
                "sessions": [list of row dicts],
                "total":    int,   # total matching rows (before pagination)
                "limit":    int,
                "offset":   int,
            }
        """
        if not self._db_path.exists():
            return {
                "sessions": [],
                "total": 0,
                "limit": params.limit,
                "offset": params.offset,
            }

        where_sql, args = self._build_where(params)

        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            total = self._count(conn, where_sql, args)
            rows = self._fetch(conn, where_sql, args, params.limit, params.offset)

        return {
            "sessions": rows,
            "total": total,
            "limit": params.limit,
            "offset": params.offset,
        }

    def distinct_models(self) -> List[str]:
        """Return sorted list of distinct model names in the DB."""
        if not self._db_path.exists():
            return []
        with sqlite3.connect(str(self._db_path)) as conn:
            cur = conn.execute(
                "SELECT DISTINCT model FROM requests WHERE model IS NOT NULL ORDER BY model"
            )
            return [row[0] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_where(self, params: FilterParams) -> Tuple[str, List[Any]]:
        """Build the WHERE clause and positional args list."""
        clauses: List[str] = ["1=1"]
        args: List[Any] = []

        if params.model:
            clauses.append("model = ?")
            args.append(params.model)

        if params.from_dt:
            clauses.append("timestamp >= ?")
            args.append(params.from_dt)

        if params.to_dt:
            clauses.append("timestamp <= ?")
            args.append(params.to_dt)

        status_clause = _STATUS_SQL.get(params.status, "")
        if status_clause:
            # Strip the leading "AND" — we join all clauses with AND ourselves
            clauses.append(status_clause.lstrip("AND ").strip())

        where_sql = " AND ".join(clauses)
        return where_sql, args

    def _count(self, conn: sqlite3.Connection, where_sql: str, args: List[Any]) -> int:
        cur = conn.execute(f"SELECT COUNT(*) FROM requests WHERE {where_sql}", args)
        return cur.fetchone()[0]

    def _fetch(
        self,
        conn: sqlite3.Connection,
        where_sql: str,
        args: List[Any],
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        col_list = ", ".join(SESSION_COLUMNS)
        sql = (
            f"SELECT {col_list} FROM requests "
            f"WHERE {where_sql} "
            f"ORDER BY timestamp DESC "
            f"LIMIT ? OFFSET ?"
        )
        cur = conn.execute(sql, args + [limit, offset])
        rows = cur.fetchall()
        return [dict(zip(SESSION_COLUMNS, row)) for row in rows]


# ---------------------------------------------------------------------------
# Module-level helpers (used by SessionFilterAPI)
# ---------------------------------------------------------------------------

_default_filter = SessionFilter()


def query_sessions(params: FilterParams) -> Dict[str, Any]:
    """Convenience wrapper: run a query with the default SessionFilter."""
    return _default_filter.query(params)


def get_distinct_models() -> List[str]:
    """Convenience wrapper: get distinct models from the default DB."""
    return _default_filter.distinct_models()
