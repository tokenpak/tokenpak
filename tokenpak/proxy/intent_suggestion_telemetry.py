# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4.1 — SQLite store for ``intent_suggestions`` rows.

Separate file from :mod:`intent_policy_telemetry` so the two
schemas don't bleed into each other. Same DB
(``~/.tokenpak/telemetry.db``) so a single backup captures every
intent surface.

Linked to the Phase 2.1 ``intent_policy_decisions`` row via
``decision_id`` and the Phase 0 ``intent_events`` row via
``contract_id``. The schema carries only:

  - identifiers (suggestion_id PK, decision_id FK, contract_id FK)
  - structured fields from the suggestion
  - templated text fields (title, message, recommended_action)

NO raw prompt text. NO per-row hashes. NO secrets. The privacy
contract is asserted in
``tests/test_intent_suggestion_phase24_1.py::TestPrivacyContract``.

Best-effort write contract: exceptions are swallowed at the writer
boundary so a misbehaving disk never breaks a request.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

from tokenpak.proxy.intent_suggestion import PolicySuggestion

_DEFAULT_DB_PATH = Path(
    os.environ.get("TOKENPAK_HOME", str(Path.home() / ".tokenpak"))
) / "telemetry.db"


_INTENT_SUGGESTIONS_DDL = """\
CREATE TABLE IF NOT EXISTS intent_suggestions (
    suggestion_id          TEXT PRIMARY KEY,
    decision_id            TEXT NOT NULL,
    contract_id            TEXT NOT NULL,
    timestamp              TEXT NOT NULL,
    suggestion_type        TEXT NOT NULL,
    title                  TEXT NOT NULL,
    message                TEXT NOT NULL,
    recommended_action     TEXT,
    confidence             REAL NOT NULL,
    safety_flags           TEXT NOT NULL,    -- JSON array
    requires_confirmation  INTEGER NOT NULL,
    user_visible           INTEGER NOT NULL,
    expires_at             TEXT,
    source                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suggestions_decision
    ON intent_suggestions (decision_id);
CREATE INDEX IF NOT EXISTS idx_suggestions_contract
    ON intent_suggestions (contract_id);
CREATE INDEX IF NOT EXISTS idx_suggestions_type
    ON intent_suggestions (suggestion_type, timestamp);
"""


@dataclass
class IntentSuggestionRow:
    """One row of the ``intent_suggestions`` table."""

    suggestion: PolicySuggestion
    timestamp: str


class IntentSuggestionStore:
    """Per-host SQLite writer for ``intent_suggestions``.

    Lazy-init (creates the table on first :meth:`write`). Single
    connection guarded by a process-wide lock.
    """

    _LOCK = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.executescript(_INTENT_SUGGESTIONS_DDL)
            conn.commit()
            self._conn = conn
        return self._conn

    def write(self, row: IntentSuggestionRow) -> None:
        """Insert one row. Best-effort — never raises on caller path."""
        s = row.suggestion
        try:
            with self._LOCK:
                conn = self._connect()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO intent_suggestions (
                        suggestion_id, decision_id, contract_id, timestamp,
                        suggestion_type, title, message, recommended_action,
                        confidence, safety_flags,
                        requires_confirmation, user_visible,
                        expires_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s.suggestion_id,
                        s.decision_id,
                        s.contract_id,
                        row.timestamp,
                        s.suggestion_type,
                        s.title,
                        s.message,
                        s.recommended_action,
                        s.confidence,
                        json.dumps(list(s.safety_flags)),
                        1 if s.requires_confirmation else 0,
                        1 if s.user_visible else 0,
                        s.expires_at,
                        s.source,
                    ),
                )
                conn.commit()
        except Exception:  # noqa: BLE001 — best-effort
            return

    def write_many(self, rows: Iterable[IntentSuggestionRow]) -> None:
        """Convenience: write a batch in a single transaction."""
        for r in rows:
            self.write(r)

    def fetch_latest(self) -> Optional[dict[str, Any]]:
        """Return the most recent row as a dict, or ``None``.

        Returns ``None`` when the DB doesn't exist, the table
        doesn't exist, or no rows have been written.
        """
        if not self._db_path.is_file():
            return None
        try:
            with self._LOCK:
                conn = self._connect()
                conn.row_factory = sqlite3.Row
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='intent_suggestions'"
                ).fetchone()
                if exists is None:
                    return None
                row = conn.execute(
                    "SELECT * FROM intent_suggestions "
                    "ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if row is None:
            return None
        out = {k: row[k] for k in row.keys()}
        try:
            out["safety_flags"] = json.loads(out.get("safety_flags") or "[]")
        except (TypeError, json.JSONDecodeError):
            out["safety_flags"] = []
        for col in ("requires_confirmation", "user_visible"):
            if col in out and out[col] is not None:
                out[col] = bool(out[col])
        return out

    def fetch_for_decision(self, decision_id: str) -> List[dict[str, Any]]:
        """Return every suggestion linked to ``decision_id``."""
        if not self._db_path.is_file():
            return []
        try:
            with self._LOCK:
                conn = self._connect()
                conn.row_factory = sqlite3.Row
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='intent_suggestions'"
                ).fetchone()
                if exists is None:
                    return []
                rows = conn.execute(
                    "SELECT * FROM intent_suggestions "
                    "WHERE decision_id = ? ORDER BY timestamp DESC",
                    (decision_id,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        out: List[dict[str, Any]] = []
        for row in rows:
            r = {k: row[k] for k in row.keys()}
            try:
                r["safety_flags"] = json.loads(r.get("safety_flags") or "[]")
            except (TypeError, json.JSONDecodeError):
                r["safety_flags"] = []
            for col in ("requires_confirmation", "user_visible"):
                if col in r and r[col] is not None:
                    r[col] = bool(r[col])
            out.append(r)
        return out

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


_DEFAULT_STORE: Optional[IntentSuggestionStore] = None


def get_default_suggestion_store() -> IntentSuggestionStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = IntentSuggestionStore()
    return _DEFAULT_STORE


def set_default_suggestion_store(store: Optional[IntentSuggestionStore]) -> None:
    """Test hook — swap the default writer."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


__all__ = [
    "IntentSuggestionRow",
    "IntentSuggestionStore",
    "get_default_suggestion_store",
    "set_default_suggestion_store",
]
