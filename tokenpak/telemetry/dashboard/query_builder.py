"""
Server-side query building for sorts, filters, and guardrails.

Applies all filtering/sorting on the server to avoid client-side data leaks
and performance issues.
"""

from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class SortSpec:
    """Sort specification."""

    column: str
    order: str  # 'asc' or 'desc'

    @property
    def is_valid(self) -> bool:
        return self.order.lower() in ("asc", "desc")


@dataclass
class FilterSpec:
    """Single filter condition."""

    column: str
    operator: str  # 'eq', 'gt', 'lt', 'gte', 'lte', 'contains'
    value: Any

    @property
    def is_valid(self) -> bool:
        return self.operator in ("eq", "gt", "lt", "gte", "lte", "contains", "in")


class QueryBuilder:
    """
    Safe server-side query builder with validation and indexing awareness.

    - Prevents SQL injection via column whitelist + parameterized queries
    - Validates sorts against indexed columns
    - Applies guardrails (max rows, export limits, rate limits)
    """

    # Indexed columns for fast sort/filter
    INDEXED_COLUMNS = {
        "timestamp",
        "created_at",
        "updated_at",
        "actual_cost",
        "savings_amount",
        "latency_ms",
        "provider",
        "model",
        "status",
        "user_id",
    }

    # Columns safe for filtering
    FILTERABLE_COLUMNS = {
        "provider",
        "model",
        "status",
        "user_id",
        "session_id",
        "actual_cost",
        "savings_amount",
        "latency_ms",
        "cache_read_tokens",
        "cache_creation_tokens",
    }

    # Guardrails
    MAX_ROWS_PER_REQUEST = 1000
    EXPORT_CONFIRMATION_THRESHOLD = 10000

    def __init__(self, table: str, export_mode: bool = False):
        self.table = table
        self.export_mode = export_mode
        self.where_clauses: List[str] = []
        self.params: List[Any] = []

    def add_filter(self, spec: FilterSpec) -> "QueryBuilder":
        """Add a WHERE condition."""
        if not spec.is_valid:
            raise ValueError(f"Invalid filter: {spec}")

        if spec.column not in self.FILTERABLE_COLUMNS:
            raise ValueError(f"Column not filterable: {spec.column}")

        if spec.operator == "eq":
            self.where_clauses.append(f"{spec.column} = ?")
            self.params.append(spec.value)
        elif spec.operator == "gt":
            self.where_clauses.append(f"{spec.column} > ?")
            self.params.append(spec.value)
        elif spec.operator == "lt":
            self.where_clauses.append(f"{spec.column} < ?")
            self.params.append(spec.value)
        elif spec.operator == "gte":
            self.where_clauses.append(f"{spec.column} >= ?")
            self.params.append(spec.value)
        elif spec.operator == "lte":
            self.where_clauses.append(f"{spec.column} <= ?")
            self.params.append(spec.value)
        elif spec.operator == "contains":
            self.where_clauses.append(f"{spec.column} LIKE ?")
            self.params.append(f"%{spec.value}%")
        elif spec.operator == "in":
            placeholders = ",".join("?" * len(spec.value))
            self.where_clauses.append(f"{spec.column} IN ({placeholders})")
            self.params.extend(spec.value)

        return self

    def add_sort(self, spec: SortSpec) -> "QueryBuilder":
        """Add ORDER BY (builds at end)."""
        if not spec.is_valid:
            raise ValueError(f"Invalid sort: {spec}")

        if spec.column not in self.INDEXED_COLUMNS:
            raise ValueError(
                f"Column not indexed: {spec.column}. Queries will be slow. Use: {self.INDEXED_COLUMNS}"
            )

        self.sort_column = spec.column
        self.sort_order = spec.order.upper()
        return self

    def build(self, limit: int = 50) -> tuple:
        """
        Build final query with guardrails.

        Returns:
            (sql, params, needs_confirmation)
        """
        # Enforce guardrails
        if self.MAX_ROWS_PER_REQUEST < limit <= self.EXPORT_CONFIRMATION_THRESHOLD:
            raise ValueError(f"Limit {limit} exceeds max {self.MAX_ROWS_PER_REQUEST}")

        needs_confirmation = limit > self.EXPORT_CONFIRMATION_THRESHOLD

        # Build WHERE
        where_sql = ""
        if self.where_clauses:
            where_sql = f"WHERE {' AND '.join(self.where_clauses)}"

        # Build ORDER BY (if set)
        order_sql = ""
        if hasattr(self, "sort_column"):
            order_sql = f"ORDER BY {self.sort_column} {self.sort_order}"

        sql = f"""SELECT * FROM {self.table}
            {where_sql}
            {order_sql}
            LIMIT ?"""

        self.params.append(limit + 1)  # Load one extra

        return sql.strip(), self.params, needs_confirmation


def parse_sort_param(sort_param: str) -> Optional[SortSpec]:
    """
    Parse ?sort=column:order format.

    Examples:
        'timestamp:desc' -> SortSpec('timestamp', 'desc')
        'cost' -> SortSpec('cost', 'asc')  [default]
    """
    if not sort_param:
        return None

    parts = sort_param.split(":")
    column = parts[0].strip()
    order = parts[1].strip().lower() if len(parts) > 1 else "asc"

    return SortSpec(column, order)


def parse_filter_param(filter_str: str) -> Optional[FilterSpec]:
    """
    Parse filter param: column:operator:value or column:value (default eq).

    Examples:
        'provider:eq:anthropic' -> FilterSpec('provider', 'eq', 'anthropic')
        'status:error' -> FilterSpec('status', 'eq', 'error')
        'latency:gt:1000' -> FilterSpec('latency', 'gt', 1000)
    """
    if not filter_str:
        return None

    parts = filter_str.split(":")
    column = parts[0].strip()

    if len(parts) == 2:
        # column:value → eq
        return FilterSpec(column, "eq", parts[1].strip())
    elif len(parts) >= 3:
        # column:op:value
        operator = parts[1].strip()
        value = parts[2].strip()

        # Coerce value type
        if value.isdigit():
            value = int(value)  # type: ignore[assignment]
        elif value in ("true", "false"):
            value = value == "true"  # type: ignore[assignment]

        return FilterSpec(column, operator, value)

    return None
