"""Anonymous Metrics — opt-in, content-free telemetry.

Collects only:
  - token counts (input, output, saved)
  - model name
  - compression ratio
  - latency_ms
  - date (UTC day bucket — no timestamp precision below day)
  - active_profile (loaded profile name, e.g. "balanced", "agentic")
  - consumption_mode (auto-detected mode: cli/tui/tmux/sdk/ide/cron)

NO prompt content, NO response content, NO user-identifiable data.

Local store: ~/.tokenpak/metrics.db (SQLite)
Batch sync via MetricsReporter → /v1/metrics/ingest
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

METRICS_DB = Path(os.path.expanduser("~/.tokenpak/metrics.db"))

# Version tag lets the ingest endpoint evolve schemas without breakage.
SCHEMA_VERSION = "1.1"


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass
class MetricsRecord:
    """One anonymised request record. No content fields allowed."""

    # Identity (local only; stripped before upload)
    local_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Timing — day-only bucket preserves privacy
    date_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # Token counts
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_saved: int = 0

    # Derived
    compression_ratio: float = 0.0  # tokens_saved / input_tokens

    # Performance
    latency_ms: float = 0.0

    # Routing
    model: str = ""

    # Consumption context (CCI-21 — anonymous categorical, no PII)
    active_profile: str = ""      # loaded profile name (e.g. "balanced", "agentic", "claude-code-cli")
    consumption_mode: str = ""    # auto-detected mode (cli/tui/tmux/sdk/ide/cron) — may differ from profile

    # Schema version
    schema_version: str = SCHEMA_VERSION

    # Upload state (local column only — not serialised for upload)
    synced: bool = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_upload_dict(self) -> dict:
        """Return a dict safe to send to the ingest endpoint (no local_id)."""
        d = asdict(self)
        # Strip local-only fields
        d.pop("local_id", None)
        d.pop("synced", None)
        # Omit empty mode fields to keep payload minimal for old installs
        if not d.get("active_profile"):
            d.pop("active_profile", None)
        if not d.get("consumption_mode"):
            d.pop("consumption_mode", None)
        return d

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MetricsRecord":
        keys = row.keys()
        return cls(
            local_id=row["local_id"],
            date_utc=row["date_utc"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            tokens_saved=row["tokens_saved"],
            compression_ratio=row["compression_ratio"],
            latency_ms=row["latency_ms"],
            model=row["model"],
            active_profile=row["active_profile"] if "active_profile" in keys else "",
            consumption_mode=row["consumption_mode"] if "consumption_mode" in keys else "",
            schema_version=row["schema_version"],
            synced=bool(row["synced"]),
        )

    # Guard: ensure no content fields are ever added accidentally
    _ALLOWED_FIELDS = frozenset(
        {
            "local_id",
            "date_utc",
            "input_tokens",
            "output_tokens",
            "tokens_saved",
            "compression_ratio",
            "latency_ms",
            "model",
            "active_profile",
            "consumption_mode",
            "schema_version",
            "synced",
        }
    )

    def __post_init__(self):
        # Validate no unexpected fields snuck in
        extra = set(asdict(self)) - self._ALLOWED_FIELDS
        if extra:
            raise ValueError(f"MetricsRecord contains disallowed fields: {extra}")


# ---------------------------------------------------------------------------
# Local store
# ---------------------------------------------------------------------------


class MetricsStore:
    """SQLite-backed local metrics store."""

    def __init__(self, db_path: Path = METRICS_DB):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    local_id        TEXT PRIMARY KEY,
                    date_utc        TEXT NOT NULL,
                    input_tokens    INTEGER NOT NULL DEFAULT 0,
                    output_tokens   INTEGER NOT NULL DEFAULT 0,
                    tokens_saved    INTEGER NOT NULL DEFAULT 0,
                    compression_ratio REAL NOT NULL DEFAULT 0.0,
                    latency_ms      REAL NOT NULL DEFAULT 0.0,
                    model           TEXT NOT NULL DEFAULT '',
                    active_profile  TEXT NOT NULL DEFAULT '',
                    consumption_mode TEXT NOT NULL DEFAULT '',
                    schema_version  TEXT NOT NULL DEFAULT '1.1',
                    synced          INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Migrate existing databases missing the new columns (CCI-21)
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(metrics)").fetchall()
            }
            if "active_profile" not in existing:
                conn.execute("ALTER TABLE metrics ADD COLUMN active_profile TEXT NOT NULL DEFAULT ''")
            if "consumption_mode" not in existing:
                conn.execute("ALTER TABLE metrics ADD COLUMN consumption_mode TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def record(self, rec: MetricsRecord) -> None:
        """Insert a new metrics record."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO metrics
                    (local_id, date_utc, input_tokens, output_tokens, tokens_saved,
                     compression_ratio, latency_ms, model,
                     active_profile, consumption_mode, schema_version, synced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    rec.local_id,
                    rec.date_utc,
                    rec.input_tokens,
                    rec.output_tokens,
                    rec.tokens_saved,
                    rec.compression_ratio,
                    rec.latency_ms,
                    rec.model,
                    rec.active_profile,
                    rec.consumption_mode,
                    rec.schema_version,
                ),
            )
            conn.commit()

    def get_pending(self, limit: int = 500) -> List[MetricsRecord]:
        """Return unsynced records."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM metrics WHERE synced=0 ORDER BY date_utc LIMIT ?", (limit,)
            ).fetchall()
        return [MetricsRecord.from_row(r) for r in rows]

    def mark_synced(self, local_ids: List[str]) -> None:
        """Mark records as successfully uploaded."""
        if not local_ids:
            return
        placeholders = ",".join("?" * len(local_ids))
        with self._connect() as conn:
            conn.execute(
                f"UPDATE metrics SET synced=1 WHERE local_id IN ({placeholders})",
                local_ids,
            )
            conn.commit()

    def history(self, days: int = 30, limit: int = 500) -> List[MetricsRecord]:
        """Return all records (synced + pending) for the last N days."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM metrics
                WHERE date_utc >= date('now', ?)
                ORDER BY date_utc DESC
                LIMIT ?
                """,
                (f"-{days} days", limit),
            ).fetchall()
        return [MetricsRecord.from_row(r) for r in rows]

    def daily_summary(self, days: int = 30) -> List[dict]:
        """Aggregate stats per day for CLI display."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    date_utc,
                    COUNT(*) as requests,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(tokens_saved) as tokens_saved,
                    AVG(compression_ratio) as avg_compression,
                    AVG(latency_ms) as avg_latency_ms,
                    SUM(CASE WHEN synced=1 THEN 1 ELSE 0 END) as synced_count
                FROM metrics
                WHERE date_utc >= date('now', ?)
                GROUP BY date_utc
                ORDER BY date_utc DESC
                """,
                (f"-{days} days",),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[MetricsStore] = None


def get_store() -> MetricsStore:
    global _store
    if _store is None:
        _store = MetricsStore()
    return _store


def detect_consumption_mode() -> str:
    """Best-effort detection of the current consumption mode.

    Returns one of: cli, tui, tmux, sdk, ide, cron, or empty string if unknown.
    Mirrors the shell logic in tokenpak-status/check.sh (CCI-09 heuristic).
    Never raises.
    """
    try:
        import os
        if os.environ.get("CRON_INVOCATION"):
            return "cron"
        term_program = os.environ.get("TERM_PROGRAM", "")
        if term_program in ("cursor", "Windsurf"):
            return "ide"
        if term_program == "vscode":
            return "ide"
        if os.environ.get("TMUX"):
            return "tmux"
        import sys
        if not sys.stdin.isatty():
            return "sdk"
        return "cli"
    except Exception:
        return ""


def record_request(
    *,
    input_tokens: int,
    output_tokens: int,
    tokens_saved: int,
    latency_ms: float,
    model: str,
    active_profile: str = "",
    consumption_mode: str = "",
) -> None:
    """Record one request. No-op if metrics are disabled. Never raises."""
    try:
        from tokenpak._internal.config import get_metrics_enabled

        if not get_metrics_enabled():
            return

        # Auto-detect mode if not supplied
        _mode = consumption_mode or detect_consumption_mode()
        # Auto-detect profile from env if not supplied
        _profile = active_profile
        if not _profile:
            import os
            _profile = os.environ.get("TOKENPAK_PROFILE", "").lower() or ""

        compression_ratio = round(tokens_saved / input_tokens, 4) if input_tokens > 0 else 0.0
        rec = MetricsRecord(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_saved=tokens_saved,
            compression_ratio=compression_ratio,
            latency_ms=round(latency_ms, 1),
            model=model,
            active_profile=_profile,
            consumption_mode=_mode,
        )
        get_store().record(rec)
    except Exception:
        pass  # telemetry must never break the proxy
