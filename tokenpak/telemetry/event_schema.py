"""
TokenPak Event Schema & Data Pipeline (event_schema.py)

Defines the complete event schema, migration helpers, token-stage validation,
and the data flow pipeline.

Data flow:
    Proxy Layer
        → Event Capture (ProxyEvent)
        → Ingest Endpoint (/v1/telemetry/ingest)
        → Token Validation (validate_token_stages)
        → Cost Calculation (CostEngine.calculate)
        → Raw Event Store (tp_events + tp_usage + tp_costs)
        → Rollup Engine (tp_rollup_daily_*)
        → Dashboard API (/v1/summary, /v1/timeseries, ...)
        → UI

Schema version: 2026.02
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Schema version — bump when DDL changes
SCHEMA_VERSION = "2026.02"

# ---------------------------------------------------------------------------
# Token stage field names (in order of compression pipeline)
# ---------------------------------------------------------------------------
TOKEN_STAGE_ORDER = [
    "raw_input_tokens",  # tokens before any processing
    "qmd_tokens",  # tokens after QMD (quick markdown) pass
    "tokenpak_tokens",  # tokens after TokenPak compression
    "final_input_tokens",  # tokens actually sent to provider (billed)
]

# ---------------------------------------------------------------------------
# Required fields for a valid ingest event
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = frozenset(
    [
        "trace_id",
        "provider",
        "model",
        "status",
        "final_input_tokens",
    ]
)

# ---------------------------------------------------------------------------
# New columns to add to tp_events via migration
# ---------------------------------------------------------------------------
TP_EVENTS_MIGRATIONS: List[Tuple[str, str]] = [
    # Token pipeline stages
    ("raw_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("qmd_tokens", "INTEGER"),
    ("tokenpak_tokens", "INTEGER"),
    ("final_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    # Latency breakdown
    ("latency_ms", "INTEGER"),
    ("provider_latency_ms", "INTEGER"),
    ("proxy_latency_ms", "INTEGER"),
    # Retry tracking
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("retry_reason", "TEXT"),
    ("task_type", "TEXT"),
    # Cost fields (calculated at ingest)
    ("pricing_version", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("baseline_cost", "REAL NOT NULL DEFAULT 0"),
    ("actual_cost", "REAL NOT NULL DEFAULT 0"),
    ("savings_amount", "REAL NOT NULL DEFAULT 0"),
    ("savings_percent", "REAL NOT NULL DEFAULT 0"),
    # Version tracking
    ("data_source", "TEXT NOT NULL DEFAULT 'estimated'"),
    ("proxy_version", "TEXT"),
    ("tokenpak_version", "TEXT"),
    ("qmd_version", "TEXT"),
]

# Performance indexes to create on tp_events
TP_EVENTS_INDEXES = [
    ("idx_tp_events_ts", "CREATE INDEX IF NOT EXISTS idx_tp_events_ts ON tp_events(ts)"),
    (
        "idx_tp_events_provider",
        "CREATE INDEX IF NOT EXISTS idx_tp_events_provider ON tp_events(provider)",
    ),
    ("idx_tp_events_model", "CREATE INDEX IF NOT EXISTS idx_tp_events_model ON tp_events(model)"),
    (
        "idx_tp_events_trace",
        "CREATE INDEX IF NOT EXISTS idx_tp_events_trace ON tp_events(trace_id)",
    ),
    (
        "idx_tp_events_composite",
        "CREATE INDEX IF NOT EXISTS idx_tp_events_composite ON tp_events(ts, provider, model)",
    ),
    (
        "idx_tp_events_status",
        "CREATE INDEX IF NOT EXISTS idx_tp_events_status ON tp_events(status)",
    ),
    (
        "idx_tp_events_agent_id",
        "CREATE INDEX IF NOT EXISTS idx_tp_events_agent_id ON tp_events(agent_id)",
    ),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


def validate_required_fields(event: dict) -> ValidationResult:
    """Check that all required fields are present and non-empty."""
    errors = []
    for f in REQUIRED_FIELDS:
        if f not in event or event[f] is None or event[f] == "":
            errors.append(f"Missing required field: {f}")
    return ValidationResult(valid=not errors, errors=errors)


def validate_token_stages(event: dict) -> ValidationResult:
    """
    Enforce token stage ordering: raw ≥ qmd ≥ tokenpak ≥ final.

    Any stage can be None/0 if not applicable, but if present they must
    be monotonically non-increasing.
    """
    errors = []
    warnings = []

    stages = []
    for stage in TOKEN_STAGE_ORDER:
        val = event.get(stage)
        if val is not None and val > 0:
            stages.append((stage, val))

    # Check monotonically non-increasing
    for i in range(len(stages) - 1):
        name_a, val_a = stages[i]
        name_b, val_b = stages[i + 1]
        if val_b > val_a:
            errors.append(
                f"Token stage ordering violated: {name_b}={val_b} > {name_a}={val_a}. "
                f"Pipeline must be non-increasing: raw ≥ qmd ≥ tokenpak ≥ final."
            )

    # Warn if raw == final (no compression applied)
    raw = event.get("raw_input_tokens", 0) or 0
    final = event.get("final_input_tokens", 0) or 0
    if raw > 0 and final >= raw:
        warnings.append(
            f"No compression detected: raw_input_tokens={raw}, final_input_tokens={final}. "
            f"Compression may be disabled or bypassed."
        )

    # Warn on very large token counts
    if raw > 200_000:
        warnings.append(f"Unusually large raw_input_tokens: {raw}. Verify this is correct.")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def validate_event(event: dict) -> ValidationResult:
    """Full event validation: required fields + token stage ordering."""
    result = validate_required_fields(event)
    if not result.valid:
        return result

    stage_result = validate_token_stages(event)
    result.errors.extend(stage_result.errors)
    result.warnings.extend(stage_result.warnings)
    result.valid = not result.errors

    return result


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
def migrate_tp_events(conn: sqlite3.Connection) -> dict:
    """
    Apply schema migrations to tp_events:
      1. Add new columns (idempotent via ALTER TABLE).
      2. Create performance indexes.
      3. Backfill data_source for legacy rows.

    Returns:
        Summary dict with columns_added, indexes_created, rows_backfilled.
    """
    cur = conn.cursor()
    columns_added = 0
    indexes_created = 0

    # 1. Add columns
    cur.execute("PRAGMA table_info(tp_events)")
    existing_cols = {row[1] for row in cur.fetchall()}

    for col_name, typedef in TP_EVENTS_MIGRATIONS:
        if col_name not in existing_cols:
            try:
                cur.execute(f"ALTER TABLE tp_events ADD COLUMN {col_name} {typedef}")
                columns_added += 1
                logger.debug(f"Added column tp_events.{col_name}")
            except Exception as e:
                logger.warning(f"Could not add column {col_name}: {e}")

    # 2. Create indexes
    existing_indexes = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tp_events'"
        ).fetchall()
    }
    for idx_name, idx_sql in TP_EVENTS_INDEXES:
        if idx_name not in existing_indexes:
            try:
                cur.execute(idx_sql)
                indexes_created += 1
                logger.debug(f"Created index {idx_name}")
            except Exception as e:
                logger.warning(f"Could not create index {idx_name}: {e}")

    # 3. Backfill legacy rows
    rows_backfilled = 0
    try:
        # Set data_source = 'legacy' for rows with no final_input_tokens data
        result = cur.execute(
            "UPDATE tp_events SET data_source = 'legacy' "
            "WHERE data_source = 'estimated' AND final_input_tokens = 0 AND raw_input_tokens = 0"
        )
        rows_backfilled = result.rowcount
    except Exception as e:
        logger.warning(f"Backfill failed: {e}")

    conn.commit()

    logger.info(
        f"tp_events migration complete: {columns_added} columns added, "
        f"{indexes_created} indexes created, {rows_backfilled} rows backfilled"
    )

    return {
        "columns_added": columns_added,
        "indexes_created": indexes_created,
        "rows_backfilled": rows_backfilled,
        "schema_version": SCHEMA_VERSION,
    }


def verify_schema(conn: sqlite3.Connection) -> dict:
    """
    Verify tp_events schema is complete and all indexes exist.

    Returns:
        Dict with missing_columns, missing_indexes, is_complete.
    """
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(tp_events)")
    existing_cols = {row[1] for row in cur.fetchall()}
    expected_new_cols = {col for col, _ in TP_EVENTS_MIGRATIONS}
    missing_cols = expected_new_cols - existing_cols

    existing_indexes = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tp_events'"
        ).fetchall()
    }
    expected_indexes = {idx_name for idx_name, _ in TP_EVENTS_INDEXES}
    missing_indexes = expected_indexes - existing_indexes

    return {
        "missing_columns": sorted(missing_cols),
        "missing_indexes": sorted(missing_indexes),
        "is_complete": not missing_cols and not missing_indexes,
        "schema_version": SCHEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# Ingest event builder
# ---------------------------------------------------------------------------
def build_ingest_event(
    trace_id: str,
    provider: str,
    model: str,
    status: str,
    final_input_tokens: int,
    raw_input_tokens: int = 0,
    qmd_tokens: Optional[int] = None,
    tokenpak_tokens: Optional[int] = None,
    output_tokens: int = 0,
    latency_ms: Optional[int] = None,
    provider_latency_ms: Optional[int] = None,
    proxy_latency_ms: Optional[int] = None,
    retry_count: int = 0,
    retry_reason: Optional[str] = None,
    task_type: Optional[str] = None,
    agent_id: str = "",
    session_id: str = "",
    pricing_version: str = "unknown",
    baseline_cost: float = 0.0,
    actual_cost: float = 0.0,
    savings_amount: float = 0.0,
    savings_percent: float = 0.0,
    data_source: str = "estimated",
    proxy_version: Optional[str] = None,
    tokenpak_version: Optional[str] = None,
    qmd_version: Optional[str] = None,
    error_class: Optional[str] = None,
    payload: Optional[Any] = None,
    **extra,
) -> dict:
    """
    Build a normalized event dict suitable for ingest.
    All required fields are guaranteed present; optional fields default to None.
    """
    return {
        "trace_id": trace_id,
        "provider": provider,
        "model": model,
        "status": status,
        "raw_input_tokens": max(0, raw_input_tokens),
        "qmd_tokens": qmd_tokens,
        "tokenpak_tokens": tokenpak_tokens,
        "final_input_tokens": max(0, final_input_tokens),
        "output_tokens": max(0, output_tokens),
        "latency_ms": latency_ms,
        "provider_latency_ms": provider_latency_ms,
        "proxy_latency_ms": proxy_latency_ms,
        "retry_count": retry_count,
        "retry_reason": retry_reason,
        "task_type": task_type,
        "agent_id": agent_id,
        "session_id": session_id,
        "pricing_version": pricing_version,
        "baseline_cost": baseline_cost,
        "actual_cost": actual_cost,
        "savings_amount": savings_amount,
        "savings_percent": savings_percent,
        "data_source": data_source,
        "proxy_version": proxy_version,
        "tokenpak_version": tokenpak_version,
        "qmd_version": qmd_version,
        "error_class": error_class,
        "payload": payload,
        **extra,
    }
