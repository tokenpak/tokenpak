# SPDX-License-Identifier: Apache-2.0
"""Phase 2.1 — SQLite store for ``intent_policy_decisions`` rows.

Separate file from :mod:`intent_contract` so policy concerns don't
bleed into Phase 0's classifier surface. Same DB
(``~/.tokenpak/telemetry.db``) so a single backup captures
everything.

Schema is **strictly hashes / ids / aggregate-safe fields**. No raw
prompt text, no per-row content. Each row links to the
``intent_events`` row (Phase 0) via ``contract_id`` so the operator
can join the two tables to see "what intent → what policy decision"
without exposing the underlying prompt body.

Privacy contract (asserted in
``tests/test_intent_policy_engine_phase21.py::TestPrivacyContract``):

  - The schema has no column named after raw content.
  - The writer never accepts a string with prompt content; only
    structured fields from :class:`PolicyDecision`.
  - The ``warning_message`` column carries the engine's templated
    string only; no caller substring reaches it.

Best-effort write contract (mirrors :class:`IntentTelemetryStore`):
exceptions are swallowed at the writer boundary so a misbehaving
disk never breaks a request.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from tokenpak.proxy.intent_policy_engine import PolicyDecision

_DEFAULT_DB_PATH = Path(
    os.environ.get("TOKENPAK_HOME", str(Path.home() / ".tokenpak"))
) / "telemetry.db"


_INTENT_POLICY_DECISIONS_DDL = """\
CREATE TABLE IF NOT EXISTS intent_policy_decisions (
    decision_id              TEXT PRIMARY KEY,
    request_id               TEXT,
    contract_id              TEXT,
    timestamp                TEXT NOT NULL,
    mode                     TEXT NOT NULL,
    intent_class             TEXT NOT NULL,
    intent_confidence        REAL NOT NULL,
    action                   TEXT NOT NULL,
    decision_reason          TEXT NOT NULL,
    safety_flags             TEXT NOT NULL,    -- JSON list
    recommended_provider     TEXT,
    recommended_model        TEXT,
    budget_action            TEXT,
    compression_profile      TEXT,
    cache_strategy           TEXT,
    delivery_strategy        TEXT,
    warning_message          TEXT,
    requires_user_confirmation INTEGER NOT NULL,
    config_mode              TEXT,
    config_dry_run           INTEGER,
    config_allow_auto_routing INTEGER,
    config_allow_unverified_providers INTEGER,
    config_low_confidence_threshold REAL
);
CREATE INDEX IF NOT EXISTS idx_policy_decisions_contract
    ON intent_policy_decisions (contract_id);
CREATE INDEX IF NOT EXISTS idx_policy_decisions_action
    ON intent_policy_decisions (action, timestamp);
CREATE INDEX IF NOT EXISTS idx_policy_decisions_reason
    ON intent_policy_decisions (decision_reason);
"""


@dataclass
class IntentPolicyDecisionRow:
    """One row of the ``intent_policy_decisions`` table.

    ``decision`` is the engine output; the row also carries the
    request-side correlation ids and the config snapshot so a
    future replay can rebuild the inputs verbatim.
    """

    request_id: str
    contract_id: str
    timestamp: str
    decision: PolicyDecision
    config_mode: Optional[str] = None
    config_dry_run: Optional[bool] = None
    config_allow_auto_routing: Optional[bool] = None
    config_allow_unverified_providers: Optional[bool] = None
    config_low_confidence_threshold: Optional[float] = None


class IntentPolicyDecisionStore:
    """Per-host SQLite writer for the ``intent_policy_decisions`` table.

    Lazy-init (creates the table on first :meth:`write`). Single
    connection guarded by a process-wide lock — Phase 2.1 traffic is
    low; Phase 2.2+ may switch to the existing async writer once the
    schema lift is complete.
    """

    _LOCK = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.executescript(_INTENT_POLICY_DECISIONS_DDL)
            conn.commit()
            self._conn = conn
        return self._conn

    def write(self, row: IntentPolicyDecisionRow) -> None:
        """Insert one row. Best-effort — never raises on caller path."""
        d = row.decision
        try:
            with self._LOCK:
                conn = self._connect()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO intent_policy_decisions (
                        decision_id, request_id, contract_id, timestamp,
                        mode, intent_class, intent_confidence,
                        action, decision_reason, safety_flags,
                        recommended_provider, recommended_model,
                        budget_action, compression_profile,
                        cache_strategy, delivery_strategy,
                        warning_message, requires_user_confirmation,
                        config_mode, config_dry_run,
                        config_allow_auto_routing,
                        config_allow_unverified_providers,
                        config_low_confidence_threshold
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d.decision_id,
                        row.request_id,
                        row.contract_id,
                        row.timestamp,
                        d.mode,
                        d.intent_class,
                        d.confidence,
                        d.action,
                        d.decision_reason,
                        json.dumps(list(d.safety_flags)),
                        d.recommended_provider,
                        d.recommended_model,
                        d.budget_action,
                        d.compression_profile,
                        d.cache_strategy,
                        d.delivery_strategy,
                        d.warning_message,
                        1 if d.requires_user_confirmation else 0,
                        row.config_mode,
                        _bool_to_int(row.config_dry_run),
                        _bool_to_int(row.config_allow_auto_routing),
                        _bool_to_int(row.config_allow_unverified_providers),
                        row.config_low_confidence_threshold,
                    ),
                )
                conn.commit()
        except Exception:  # noqa: BLE001 — best-effort; never propagate
            return

    def fetch_latest(self) -> Optional[dict[str, Any]]:
        """Return the most recent row as a dict, or ``None``.

        Read path used by ``tokenpak intent policy-preview --last``.
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
                    "WHERE type='table' AND name='intent_policy_decisions'"
                ).fetchone()
                if exists is None:
                    return None
                row = conn.execute(
                    "SELECT * FROM intent_policy_decisions "
                    "ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if row is None:
            return None
        out = {k: row[k] for k in row.keys()}
        # JSON-decode the safety flags column for caller convenience.
        try:
            out["safety_flags"] = json.loads(out.get("safety_flags") or "[]")
        except (TypeError, json.JSONDecodeError):
            out["safety_flags"] = []
        # Re-cast bool-ish columns.
        for col in (
            "requires_user_confirmation",
            "config_dry_run",
            "config_allow_auto_routing",
            "config_allow_unverified_providers",
        ):
            if col in out and out[col] is not None:
                out[col] = bool(out[col])
        return out

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


_DEFAULT_STORE: Optional[IntentPolicyDecisionStore] = None


def get_default_policy_store() -> IntentPolicyDecisionStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = IntentPolicyDecisionStore()
    return _DEFAULT_STORE


def set_default_policy_store(store: Optional[IntentPolicyDecisionStore]) -> None:
    """Test hook — swap the default writer (e.g. for an in-memory DB)."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


def _bool_to_int(v: Optional[bool]) -> Optional[int]:
    if v is None:
        return None
    return 1 if v else 0


__all__ = [
    "IntentPolicyDecisionRow",
    "IntentPolicyDecisionStore",
    "get_default_policy_store",
    "set_default_policy_store",
]
