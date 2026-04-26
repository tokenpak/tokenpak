# SPDX-License-Identifier: Apache-2.0
"""Phase PI-1 — SQLite store for ``intent_patches`` rows.

Same DB (``~/.tokenpak/telemetry.db``) so a single backup
captures every Intent Layer surface. Schema is **strictly hashes
/ IDs / templated text only** per PI-0 spec § 4.3 + § 9.

Linked to the PI-x dependency chain by:

  - ``contract_id`` → ``intent_events`` (Phase 0)
  - ``decision_id`` → ``intent_policy_decisions`` (Phase 2.1)
  - ``suggestion_id`` → ``intent_suggestions`` (Phase 2.4.1)

NO raw prompt text. NO per-row hashes of prompt content. The
``original_hash`` column is a sha256 of the suggestion's
identity tuple (contract_id || suggestion_id ||
suggestion_type) — explicitly NOT a hash of the user's prompt.

Best-effort write contract: exceptions are swallowed at the
writer boundary. PI-1 has no production write path (the builder
is library code; PI-2+ surfaces will integrate it), so this
contract is mostly forward-compat with the rest of the Intent
Layer's telemetry stores.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

from tokenpak.proxy.intent_prompt_patch import PromptPatch

_DEFAULT_DB_PATH = Path(
    os.environ.get("TOKENPAK_HOME", str(Path.home() / ".tokenpak"))
) / "telemetry.db"


_INTENT_PATCHES_DDL = """\
CREATE TABLE IF NOT EXISTS intent_patches (
    patch_id              TEXT PRIMARY KEY,
    contract_id           TEXT NOT NULL,
    decision_id           TEXT NOT NULL,
    suggestion_id         TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    mode                  TEXT NOT NULL,
    target                TEXT NOT NULL,
    original_hash         TEXT NOT NULL,
    patch_text            TEXT NOT NULL,
    reason                TEXT NOT NULL,
    confidence            REAL NOT NULL,
    safety_flags          TEXT NOT NULL,    -- JSON array
    requires_confirmation INTEGER NOT NULL,
    applied               INTEGER NOT NULL,
    source                TEXT NOT NULL,
    applied_at            TEXT,             -- PI-3: ISO-8601 when injection succeeded
    applied_surface       TEXT,             -- PI-3: e.g. 'claude_code_companion'
    application_mode      TEXT,             -- PI-3: 'inject_guidance' (PI-3 only mode)
    application_id        TEXT              -- PI-3: opaque caller-side application token
);
CREATE INDEX IF NOT EXISTS idx_patches_suggestion
    ON intent_patches (suggestion_id);
CREATE INDEX IF NOT EXISTS idx_patches_contract
    ON intent_patches (contract_id);
CREATE INDEX IF NOT EXISTS idx_patches_mode
    ON intent_patches (mode, created_at);
CREATE INDEX IF NOT EXISTS idx_patches_applied
    ON intent_patches (applied, created_at);
CREATE INDEX IF NOT EXISTS idx_patches_applied_surface
    ON intent_patches (applied_surface, applied_at);
"""

_PI_3_ADDITIVE_COLUMNS = (
    "applied_at",
    "applied_surface",
    "application_mode",
    "application_id",
)


@dataclass
class IntentPatchRow:
    """One row of ``intent_patches``."""

    patch: PromptPatch
    created_at: str


class IntentPatchStore:
    """Per-host SQLite writer for ``intent_patches``.

    Lazy-init; creates the table on first :meth:`write`. Single
    connection guarded by a process-wide lock — PI-1 traffic is
    zero by design (no production write path).
    """

    _LOCK = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.executescript(_INTENT_PATCHES_DDL)
            # PI-3 additive migration: pre-PI-3 hosts have an
            # intent_patches table without applied_at /
            # applied_surface / application_mode / application_id.
            # SQLite has no ADD COLUMN IF NOT EXISTS, so swallow
            # the duplicate-column error per migration column.
            for col in _PI_3_ADDITIVE_COLUMNS:
                try:
                    conn.execute(
                        f"ALTER TABLE intent_patches ADD COLUMN {col} TEXT"
                    )
                except sqlite3.OperationalError:
                    pass
            conn.commit()
            self._conn = conn
        return self._conn

    def write(self, row: IntentPatchRow) -> None:
        """Insert one row. Best-effort — never raises on caller path."""
        p = row.patch
        try:
            with self._LOCK:
                conn = self._connect()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO intent_patches (
                        patch_id, contract_id, decision_id, suggestion_id,
                        created_at, mode, target,
                        original_hash, patch_text, reason,
                        confidence, safety_flags,
                        requires_confirmation, applied, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p.patch_id,
                        p.contract_id,
                        p.decision_id,
                        p.suggestion_id,
                        row.created_at,
                        p.mode,
                        p.target,
                        p.original_hash,
                        p.patch_text,
                        p.reason,
                        p.confidence,
                        json.dumps(list(p.safety_flags)),
                        1 if p.requires_confirmation else 0,
                        1 if p.applied else 0,
                        p.source,
                    ),
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            return

    def write_many(self, rows: Iterable[IntentPatchRow]) -> None:
        for r in rows:
            self.write(r)

    def mark_applied(
        self,
        *,
        patch_id: str,
        applied_surface: str,
        application_mode: str,
        application_id: Optional[str],
        applied_at: str,
    ) -> bool:
        """Flip ``applied = True`` and stamp PI-3 audit columns.

        Returns ``True`` when exactly one row was updated. Returns
        ``False`` on missing patch, already-applied patch, or any
        SQLite error. Idempotent guard: refuses to flip a row that
        is already ``applied = 1``.

        PI-3 only — the only caller is
        :func:`tokenpak.companion.intent_injection.apply_patch_to_companion_context`
        on a successful insertion.
        """
        if not self._db_path.is_file():
            return False
        try:
            with self._LOCK:
                conn = self._connect()
                cur = conn.execute(
                    "UPDATE intent_patches SET "
                    "applied = 1, applied_at = ?, "
                    "applied_surface = ?, application_mode = ?, "
                    "application_id = ? "
                    "WHERE patch_id = ? AND applied = 0",
                    (
                        applied_at,
                        applied_surface,
                        application_mode,
                        application_id,
                        patch_id,
                    ),
                )
                conn.commit()
                return cur.rowcount == 1
        except sqlite3.DatabaseError:
            return False

    def fetch_latest(self) -> Optional[dict[str, Any]]:
        """Return the most recent row as a dict, or ``None``."""
        if not self._db_path.is_file():
            return None
        try:
            with self._LOCK:
                conn = self._connect()
                conn.row_factory = sqlite3.Row
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='intent_patches'"
                ).fetchone()
                if exists is None:
                    return None
                row = conn.execute(
                    "SELECT * FROM intent_patches "
                    "ORDER BY created_at DESC LIMIT 1"
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
        for col in ("requires_confirmation", "applied"):
            if col in out and out[col] is not None:
                out[col] = bool(out[col])
        return out

    def fetch_for_suggestion(self, suggestion_id: str) -> List[dict[str, Any]]:
        """Return every patch linked to ``suggestion_id``."""
        if not self._db_path.is_file():
            return []
        try:
            with self._LOCK:
                conn = self._connect()
                conn.row_factory = sqlite3.Row
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='intent_patches'"
                ).fetchone()
                if exists is None:
                    return []
                rows = conn.execute(
                    "SELECT * FROM intent_patches "
                    "WHERE suggestion_id = ? ORDER BY created_at DESC",
                    (suggestion_id,),
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
            for col in ("requires_confirmation", "applied"):
                if col in r and r[col] is not None:
                    r[col] = bool(r[col])
            out.append(r)
        return out

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


_DEFAULT_STORE: Optional[IntentPatchStore] = None


def get_default_patch_store() -> IntentPatchStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = IntentPatchStore()
    return _DEFAULT_STORE


def set_default_patch_store(store: Optional[IntentPatchStore]) -> None:
    """Test hook — swap the default writer."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


__all__ = [
    "IntentPatchRow",
    "IntentPatchStore",
    "get_default_patch_store",
    "set_default_patch_store",
]
