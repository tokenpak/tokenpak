# SPDX-License-Identifier: Apache-2.0
"""Shared SQLite plumbing for the companion stores (journal.db, budget.db).

These databases are written by several concurrent OS processes: the
per-prompt hooks (python and bash variants), the proxy app plane, and the
long-lived MCP server. Historically each writer opened raw connections with
default settings (rollback journal, ``busy_timeout=0``), so a second
concurrent writer hit ``SQLITE_BUSY`` immediately and the best-effort
writers dropped the row silently.

Rules enforced by this module:

- Every python opener goes through :func:`connect`, which enables WAL and a
  >= 5s busy timeout so writers queue instead of failing. (The bash hook
  variants apply the equivalent ``.timeout`` via the sqlite3 CLI.)
- There is exactly ONE canonical DDL for the journal ``sessions`` /
  ``entries`` tables and the budget ``companion_costs`` table. Writers must
  not carry divergent private copies of these statements — first-writer-wins
  schema races were a real defect.
- ``entries`` rows carry a ``content_hash`` dedupe key with a partial
  UNIQUE index so retried/duplicated events collapse under
  ``INSERT OR IGNORE``. Legacy rows (``content_hash IS NULL``) are exempt
  from the index, so the migration is non-destructive and never needs to
  rewrite or dedupe existing data.
- ``companion_costs`` rows carry a ``kind`` column (``'estimate'`` |
  ``'actual'``) so daily-spend readers can count each message exactly once
  (preferring actuals when present). Legacy rows have ``kind IS NULL`` and
  are classified by ``model = ''`` (pre-send estimates never carried a
  model; the recording planes always do).
- Dropped best-effort writes are logged via :func:`note_dropped_write`
  instead of vanishing inside a bare ``except: pass``.

This module must stay stdlib-only: the per-prompt hook imports it on its
hot path.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import time
from pathlib import Path

#: Milliseconds a connection waits on a locked database before giving up.
BUSY_TIMEOUT_MS = 5000

#: Best-effort log of dropped writes, relative to the companion dir.
DROPPED_WRITES_LOG = "dropped-writes.log"


# ---------------------------------------------------------------------------
# Canonical DDL — the only copy. Writers import these; do not fork them.
# ---------------------------------------------------------------------------

JOURNAL_SESSIONS_DDL = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        started_at REAL NOT NULL,
        ended_at REAL,
        project_dir TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        total_requests INTEGER NOT NULL DEFAULT 0,
        total_cost_usd REAL NOT NULL DEFAULT 0.0,
        total_input_tokens INTEGER NOT NULL DEFAULT 0,
        total_output_tokens INTEGER NOT NULL DEFAULT 0,
        capsule_path TEXT
    )
"""

JOURNAL_ENTRIES_DDL = """
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        timestamp REAL NOT NULL,
        entry_type TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        content_hash TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    )
"""

#: Partial UNIQUE index: dedupes only rows that carry a hash, so it can be
#: created on legacy databases that already contain duplicate rows.
ENTRIES_DEDUPE_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_dedupe "
    "ON entries(session_id, entry_type, content_hash) "
    "WHERE content_hash IS NOT NULL"
)

JOURNAL_INDEX_DDLS = (
    "CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(timestamp)",
    ENTRIES_DEDUPE_INDEX_DDL,
)

COSTS_DDL = """
    CREATE TABLE IF NOT EXISTS companion_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        date TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        estimated_cost REAL NOT NULL DEFAULT 0.0,
        kind TEXT
    )
"""

#: Partial UNIQUE index backing the one-estimate-row-per-(session, day)
#: upsert. Legacy rows (kind IS NULL) are exempt, so creation succeeds on
#: databases that already contain the historical one-row-per-prompt series.
COSTS_ESTIMATE_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_companion_costs_estimate "
    "ON companion_costs(session_id, date) WHERE kind = 'estimate'"
)

#: Upsert for the pre-send estimate plane: a session contributes ONE
#: estimate row per day, refreshed in place to the latest full-transcript
#: estimate. Equivalent to recording per-turn deltas against a per-session
#: high-water mark — the daily gate reads true marginal spend instead of
#: summing a monotonically growing series (the historical over-count).
#: Parameters: (timestamp, date, session_id, input_tokens, estimated_cost).
COSTS_ESTIMATE_UPSERT_SQL = """
    INSERT INTO companion_costs
        (timestamp, date, session_id, model, input_tokens, cached_tokens,
         output_tokens, estimated_cost, kind)
    VALUES (?, ?, ?, '', ?, 0, 0, ?, 'estimate')
    ON CONFLICT(session_id, date) WHERE kind = 'estimate'
    DO UPDATE SET
        timestamp = excluded.timestamp,
        input_tokens = excluded.input_tokens,
        estimated_cost = excluded.estimated_cost
"""

#: Truthful daily-spend aggregation. Per (session, day): sum the ACTUAL
#: rows when any exist (the recording planes report real usage), otherwise
#: take the latest/largest ESTIMATE. This counts each message exactly once
#: — never estimate + actual for the same traffic, and never a summed
#: series of cumulative transcript estimates. ``kind`` classifies new rows;
#: legacy rows (kind IS NULL) are classified by ``model = ''``.
#: Parameter: (date,).
DAILY_SPEND_SQL = """
    SELECT COALESCE(SUM(session_spend), 0.0) FROM (
        SELECT CASE
            WHEN SUM(CASE WHEN COALESCE(kind,
                          CASE WHEN model = '' THEN 'estimate' ELSE 'actual' END
                      ) = 'actual' THEN 1 ELSE 0 END) > 0
            THEN SUM(CASE WHEN COALESCE(kind,
                          CASE WHEN model = '' THEN 'estimate' ELSE 'actual' END
                      ) = 'actual' THEN estimated_cost ELSE 0.0 END)
            ELSE MAX(estimated_cost)
        END AS session_spend
        FROM companion_costs
        WHERE date = ?
        GROUP BY session_id
    )
"""


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


def connect(
    db_path: Path | str,
    *,
    timeout: float = 5.0,
    check_same_thread: bool = True,
    foreign_keys: bool = False,
) -> sqlite3.Connection:
    """Open a companion database with concurrency-safe pragmas.

    ``busy_timeout`` is applied first so even the WAL switch itself waits
    for a lock instead of failing. Pragma application is best-effort: on
    surfaces where WAL is impossible (read-only mounts) the connection
    still works with the rollback journal — and a WAL switch that loses a
    cross-process race is harmless because whichever opener succeeds
    converts the file persistently for everyone.
    """
    conn = sqlite3.connect(str(db_path), timeout=timeout, check_same_thread=check_same_thread)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error:
        pass
    return conn


# ---------------------------------------------------------------------------
# Schema (create + additive migration)
# ---------------------------------------------------------------------------


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Additive column migration; never touches existing rows."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure_journal_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the journal schema (sessions + entries + indexes).

    Idempotent and non-destructive: legacy databases gain the
    ``content_hash`` column (NULL for existing rows) and the partial
    dedupe index; existing rows are never rewritten or deduplicated.
    """
    conn.execute(JOURNAL_SESSIONS_DDL)
    conn.execute(JOURNAL_ENTRIES_DDL)
    _ensure_column(conn, "entries", "content_hash", "TEXT")
    for ddl in JOURNAL_INDEX_DDLS:
        conn.execute(ddl)


def ensure_costs_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the budget schema (companion_costs + estimate index)."""
    conn.execute(COSTS_DDL)
    _ensure_column(conn, "companion_costs", "kind", "TEXT")
    conn.execute(COSTS_ESTIMATE_INDEX_DDL)


# ---------------------------------------------------------------------------
# Dedupe key
# ---------------------------------------------------------------------------


def entry_content_hash(entry_type: str, content: str, metadata_json: str = "{}") -> str:
    """Canonical dedupe key for a journal entry.

    sha256 over ``entry_type <US> content <US> metadata_json`` (US = 0x1f).
    The bash hook variants compute the same preimage via
    ``printf '%s\\037%s\\037%s' | sha256sum`` — keep the two in lockstep.
    ``timestamp`` is deliberately excluded: two deliveries of the same
    event differ only in arrival time and must collapse to one row.
    """
    preimage = "\x1f".join((entry_type, content, metadata_json or "{}"))
    return hashlib.sha256(preimage.encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------------------
# Dropped-write accounting
# ---------------------------------------------------------------------------


def note_dropped_write(db_path: Path | str, op: str, exc: BaseException) -> None:
    """Record a dropped best-effort write instead of losing it silently.

    Appends one line to ``run/dropped-writes.log`` next to the database and
    emits a single stderr note (visible in the TUI). Never raises — callers
    sit on fail-open hook paths.
    """
    db_path = Path(db_path)
    try:
        run_dir = db_path.parent / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        line = f"{time.time():.3f}\t{db_path.name}\t{op}\t{type(exc).__name__}: {exc}\n"
        with open(run_dir / DROPPED_WRITES_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        print(
            f"tokenpak: dropped {op} write to {db_path.name} ({type(exc).__name__})",
            file=sys.stderr,
        )
    except Exception:
        pass
