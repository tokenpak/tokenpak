"""
Cursor-based pagination for large datasets.

Replaces offset-based pagination with cursor-based approach for O(1) pagination
of large datasets, avoiding expensive OFFSET scans.
"""

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypeVar

T = TypeVar("T")


def encode_cursor(**kwargs) -> str:
    """
    Encode cursor data as base64 JSON.

    Example:
        cursor = encode_cursor(timestamp='2026-03-01T12:00:00Z', trace_id='abc123')
    """
    data = json.dumps(kwargs, separators=(",", ":"), default=str)
    return base64.b64encode(data.encode()).decode()


def decode_cursor(cursor: str) -> Dict[str, Any]:
    """Decode cursor back to dict."""
    try:
        decoded = base64.b64decode(cursor).decode()
        return json.loads(decoded)
    except Exception:
        return {}


@dataclass
class PaginatedResponse:
    """Standard paginated response envelope."""

    data: List[Any]
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    total_count: int = 0
    has_more: bool = False
    page_size: int = 50

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "data": [item.dict() if hasattr(item, "dict") else item for item in self.data],
            "next_cursor": self.next_cursor,
            "prev_cursor": self.prev_cursor,
            "total_count": self.total_count,
            "has_more": self.has_more,
            "page_size": self.page_size,
        }


class CursorPaginationBuilder:
    """
    Builder for cursor-based pagination queries.

    Replaces LIMIT OFFSET with WHERE clause to avoid O(n) scans.
    """

    def __init__(
        self,
        table: str,
        cursor_fields: List[str],
        order_by: str = "timestamp",
        order: str = "desc",
    ):
        self.table = table
        self.cursor_fields = cursor_fields
        self.order_by = order_by
        self.order = order.upper()

    def build_query(
        self,
        cursor: Optional[str] = None,
        limit: int = 50,
        filters: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Build pagination query. Returns (sql, params)."""
        params = []
        where_clauses = []

        # Apply cursor (pagination)
        if cursor:
            cursor_data = decode_cursor(cursor)
            if cursor_data:
                if self.order == "DESC":
                    where_clauses.append(f"{self.order_by} < ?")
                    params.append(cursor_data.get(self.order_by))
                else:
                    where_clauses.append(f"{self.order_by} > ?")
                    params.append(cursor_data.get(self.order_by))

        # Apply filters
        if filters:
            for col, value in filters.items():
                where_clauses.append(f"{col} = ?")
                params.append(value)

        # Build WHERE clause
        where_sql = ""
        if where_clauses:
            where_sql = f"WHERE {' AND '.join(where_clauses)}"

        # Load one extra row to detect if there are more
        sql = f"""SELECT * FROM {self.table}
            {where_sql}
            ORDER BY {self.order_by} {self.order}
            LIMIT ?"""
        params.append(limit + 1)

        return sql.strip(), params

    def extract_cursor_from_row(self, row: Dict[str, Any]) -> str:
        """Extract cursor from a row."""
        cursor_values = {}
        for field in self.cursor_fields:
            if isinstance(row, dict):
                cursor_values[field] = row.get(field)
            else:
                cursor_values[field] = getattr(row, field, None)
        return encode_cursor(**cursor_values)
