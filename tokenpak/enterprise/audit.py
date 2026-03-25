"""Enterprise Audit Log — immutable append-only structured log.

Tracks *who* did *what* *when* on *which model* with *which data* for
compliance and forensic purposes.

Usage::

    from tokenpak.enterprise.audit import AuditLog

    log = AuditLog(".tokenpak/audit.db")
    log.record(
        user_id="alice",
        action="proxy_request",
        model="openai/gpt-4o",
        data_classification="internal",
        metadata={"prompt_tokens": 200},
    )

    for entry in log.list(since="2026-01-01", user_id="alice"):
        print(entry)

    log.export("report.json", fmt="json")
    log.export("report.csv",  fmt="csv")
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Union

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS tp_audit_log (
    id              TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    ts_iso          TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL DEFAULT '',
    action          TEXT NOT NULL,
    model           TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    data_class      TEXT NOT NULL DEFAULT 'unclassified',
    outcome         TEXT NOT NULL DEFAULT 'ok',
    source_ip       TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    prev_hash       TEXT NOT NULL DEFAULT '',
    entry_hash      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tp_audit_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO tp_audit_config VALUES ('retention_days', '90');
INSERT OR IGNORE INTO tp_audit_config VALUES ('schema_version',  '1');
"""

# Actions that should always be recorded even in low-verbosity mode
_ALWAYS_RECORD = frozenset(
    {
        "auth_success",
        "auth_failure",
        "permission_denied",
        "config_change",
        "user_created",
        "user_deactivated",
        "data_export",
        "key_rotation",
        "compliance_report",
    }
)


def _now() -> float:
    return time.time()


def _ts_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _row_factory(cur: sqlite3.Cursor, row: tuple) -> dict:
    cols = [d[0] for d in cur.description]
    d = dict(zip(cols, row))
    # Deserialise JSON metadata
    if "metadata" in d:
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


class AuditLog:
    """Immutable append-only audit log backed by SQLite WAL.

    The log is *append-only*: rows are never updated or deleted during normal
    operation (only the retention pruner removes rows older than the configured
    retention window).  Each row carries a SHA-256 hash of its own content
    chained to the previous row's hash, making tampering detectable.

    Parameters
    ----------
    path:
        File-system path to the SQLite database, or ``":memory:"`` for tests.
    retention_days:
        How long to keep entries (default: 90 days, configurable).
    """

    def __init__(
        self,
        path: Union[str, Path] = ".tokenpak/audit.db",
        retention_days: int = 90,
    ) -> None:
        self.path = str(path)
        self._retention_days = retention_days
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = _row_factory
        self._apply_ddl()
        # Persist retention setting
        self._conn.execute(
            "INSERT OR REPLACE INTO tp_audit_config VALUES ('retention_days', ?)",
            (str(retention_days),),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_ddl(self) -> None:
        self._conn.executescript(_DDL)
        self._conn.commit()

    def _last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT entry_hash FROM tp_audit_log ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return "0" * 64  # genesis
        return row["entry_hash"] if isinstance(row, dict) else row[0]

    def _compute_hash(
        self,
        entry_id: str,
        ts: float,
        user_id: str,
        action: str,
        model: str,
        data_class: str,
        prev_hash: str,
    ) -> str:
        payload = f"{entry_id}:{ts}:{user_id}:{action}:{model}:{data_class}:{prev_hash}"
        return hashlib.sha256(payload.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        action: str,
        user_id: str = "",
        agent_id: str = "",
        model: str = "",
        provider: str = "",
        data_classification: str = "unclassified",
        outcome: str = "ok",
        source_ip: str = "",
        session_id: str = "",
        metadata: Optional[dict] = None,
    ) -> str:
        """Append a new audit entry. Returns the entry ``id``."""
        entry_id = str(uuid.uuid4())
        ts = _now()
        prev_hash = self._last_hash()
        entry_hash = self._compute_hash(
            entry_id, ts, user_id, action, model, data_classification, prev_hash
        )
        self._conn.execute(
            """INSERT INTO tp_audit_log
               (id, ts, ts_iso, user_id, agent_id, action, model, provider,
                data_class, outcome, source_ip, session_id, prev_hash, entry_hash, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entry_id,
                ts,
                _ts_iso(ts),
                user_id,
                agent_id,
                action,
                model,
                provider,
                data_classification,
                outcome,
                source_ip,
                session_id,
                prev_hash,
                entry_hash,
                json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()
        return entry_id

    def list(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        model: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """Return audit entries matching the given filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if since:
            ts_since = _parse_date(since)
            clauses.append("ts >= ?")
            params.append(ts_since)
        if until:
            ts_until = _parse_date(until)
            clauses.append("ts <= ?")
            params.append(ts_until)
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if model:
            clauses.append("model = ?")
            params.append(model)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tp_audit_log {where} ORDER BY ts ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self._conn.execute(sql, params).fetchall()

    def count(
        self,
        since: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Count matching audit entries."""
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("ts >= ?")
            params.append(_parse_date(since))
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) as n FROM tp_audit_log {where}", params
        ).fetchone()
        return row["n"] if isinstance(row, dict) else row[0]

    def export(self, path: Union[str, Path], fmt: str = "json", **list_kwargs) -> int:
        """Export audit log to *path* in *fmt* (``'json'`` or ``'csv'``).

        Returns the number of rows exported.
        """
        rows = self.list(**list_kwargs)
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        elif fmt == "csv":
            if not rows:
                out.write_text("", encoding="utf-8")
            else:
                with out.open("w", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
        else:
            raise ValueError(f"Unsupported format: {fmt!r}. Use 'json' or 'csv'.")

        return len(rows)

    def verify_chain(self) -> tuple[bool, List[str]]:
        """Verify the hash chain integrity.

        Returns ``(ok, errors)`` where *errors* is a list of violation messages.
        """
        rows = self._conn.execute("SELECT * FROM tp_audit_log ORDER BY ts ASC").fetchall()
        errors: list[str] = []
        prev_hash = "0" * 64

        for row in rows:
            expected = self._compute_hash(
                row["id"],
                row["ts"],
                row["user_id"],
                row["action"],
                row["model"],
                row["data_class"],
                prev_hash,
            )
            if row["entry_hash"] != expected:
                errors.append(f"Hash mismatch on entry {row['id']} (ts={row['ts_iso']})")
            if row["prev_hash"] != prev_hash:
                errors.append(
                    f"Chain break on entry {row['id']}: "
                    f"prev_hash={row['prev_hash']!r} expected={prev_hash!r}"
                )
            prev_hash = row["entry_hash"]

        return (len(errors) == 0, errors)

    def prune(self, retention_days: Optional[int] = None) -> int:
        """Delete entries older than *retention_days*. Returns rows deleted."""
        days = retention_days or self._retention_days
        cutoff = _now() - (days * 86400)
        cur = self._conn.execute("DELETE FROM tp_audit_log WHERE ts < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    def summary(self, since: Optional[str] = None) -> dict:
        """Return aggregate summary stats for the audit log."""
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("ts >= ?")
            params.append(_parse_date(since))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        total = self._conn.execute(
            f"SELECT COUNT(*) as n FROM tp_audit_log {where}", params
        ).fetchone()
        by_action = self._conn.execute(
            f"SELECT action, COUNT(*) as n FROM tp_audit_log {where} GROUP BY action ORDER BY n DESC",
            params,
        ).fetchall()
        by_user = self._conn.execute(
            f"SELECT user_id, COUNT(*) as n FROM tp_audit_log {where} GROUP BY user_id ORDER BY n DESC LIMIT 20",
            params,
        ).fetchall()
        by_outcome = self._conn.execute(
            f"SELECT outcome, COUNT(*) as n FROM tp_audit_log {where} GROUP BY outcome",
            params,
        ).fetchall()
        return {
            "total": total["n"] if isinstance(total, dict) else total[0],
            "by_action": by_action,
            "by_user": by_user,
            "by_outcome": by_outcome,
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> float:
    """Parse ISO date/datetime string to UNIX timestamp."""
    fmts = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def default_audit_path() -> Path:
    """Return the default audit DB path (.tokenpak/audit.db)."""
    import os

    base = Path(os.environ.get("TOKENPAK_HOME", Path.home() / ".tokenpak"))
    return base / "audit.db"
