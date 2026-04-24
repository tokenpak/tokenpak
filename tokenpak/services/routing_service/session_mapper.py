# SPDX-License-Identifier: Apache-2.0
"""Session mapper — platform-agnostic external→internal session id persistence.

Why this exists
---------------

When an agent-orchestration platform (OpenClaw, Codex, future adapters)
routes traffic through tokenpak, each of its own sessions needs a stable
mapping onto the provider's native session id so multi-turn conversations
stay coherent. For the Claude Code companion path, the "internal" id is
the UUID that ``claude --output-format json`` emits on every turn; future
providers expose their own equivalents.

Design
------

- **Scope-keyed**: every record is keyed by
  ``(scope, external_id, provider)`` where ``scope`` is the platform
  name ("openclaw" / "codex" / …) and ``provider`` is the tokenpak
  provider slug the caller targeted ("tokenpak-claude-code" / …). The
  triple is the primary key so two platforms can reuse the same external
  session-id string without collision.
- **SQLite-backed** at ``~/.tokenpak/session_map.db`` in WAL mode so
  parallel OpenClaw workers / Cali / Trix writing concurrently don't
  serialize around a single JSON file. A corrupt database file is
  renamed and a fresh one is initialised — we never let a stale map
  break live routing.
- **Single-writer, many-reader semantics** within a process via
  ``threading.Lock``; cross-process coherence comes from SQLite's WAL
  journal.
- **No PII / no secrets.** Records hold only ids + timestamps + an
  optional ``metadata`` JSON blob (intended for model name / route
  class — never tokens or prompt content).

Public surface
--------------

- :class:`SessionMap` — the store (construct with path override for tests).
- :func:`get_session_mapper()` — process-wide singleton.
- :class:`SessionRecord` — what callers get back from a lookup.

Opt-out
-------

``TOKENPAK_SESSION_MAPPER=0`` disables the mapper process-wide —
callers (backends) see ``None`` on every lookup and skip persistence.
The oauth backend then falls back to its v1.3.13 ``--continue``
behavior. This is the only escape hatch; no partial disablement.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


_DEFAULT_DB_PATH = Path.home() / ".tokenpak" / "session_map.db"


def _enabled() -> bool:
    """Process-wide opt-out via ``TOKENPAK_SESSION_MAPPER=0``."""
    return os.environ.get("TOKENPAK_SESSION_MAPPER", "1").strip() != "0"


@dataclass(frozen=True)
class SessionRecord:
    """Result of a successful :meth:`SessionMap.get` lookup."""

    scope: str
    external_id: str
    provider: str
    internal_id: str
    created_at: float
    last_used_at: float
    metadata: Dict[str, str]


class SessionMap:
    """SQLite-backed external→internal session id mapping.

    Constructed with a path override (tests) or the default
    ``~/.tokenpak/session_map.db``. Thread-safe; multi-process safe via
    SQLite WAL.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS session_map (
        scope       TEXT NOT NULL,
        external_id TEXT NOT NULL,
        provider    TEXT NOT NULL,
        internal_id TEXT NOT NULL,
        created_at  REAL NOT NULL,
        last_used_at REAL NOT NULL,
        metadata    TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (scope, external_id, provider)
    );
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._lock = threading.Lock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create the parent dir + schema. Recover from a corrupt db file."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as conn:
                conn.executescript(self._SCHEMA)
                conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError as err:
            # File exists but isn't a valid SQLite db — quarantine + retry once.
            logger.warning(
                "session_mapper: corrupt db at %s (%s); quarantining and recreating",
                self._db_path,
                err,
            )
            quarantine = self._db_path.with_suffix(
                f".corrupt-{int(time.time())}.db"
            )
            try:
                shutil.move(str(self._db_path), str(quarantine))
            except OSError:
                try:
                    self._db_path.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._connect() as conn:
                conn.executescript(self._SCHEMA)
                conn.execute("PRAGMA journal_mode=WAL")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=5.0,
            isolation_level=None,  # autocommit; we manage transactions explicitly.
        )
        conn.row_factory = sqlite3.Row
        return conn

    # ── Public API ──────────────────────────────────────────────────────

    def get(
        self, scope: str, external_id: str, provider: str
    ) -> Optional[SessionRecord]:
        """Return the stored record, or ``None`` when absent.

        Touches ``last_used_at`` on every hit — read is write, by design,
        so TTL-based pruning gets accurate liveness signals.
        """
        if not _enabled():
            return None
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT * FROM session_map "
                        "WHERE scope=? AND external_id=? AND provider=?",
                        (scope, external_id, provider),
                    ).fetchone()
                    if row is None:
                        return None
                    now = time.time()
                    conn.execute(
                        "UPDATE session_map SET last_used_at=? "
                        "WHERE scope=? AND external_id=? AND provider=?",
                        (now, scope, external_id, provider),
                    )
                    metadata: Dict[str, str] = {}
                    try:
                        raw = row["metadata"]
                        if raw:
                            parsed = json.loads(raw)
                            if isinstance(parsed, dict):
                                metadata = {str(k): str(v) for k, v in parsed.items()}
                    except (json.JSONDecodeError, TypeError):
                        pass
                    return SessionRecord(
                        scope=row["scope"],
                        external_id=row["external_id"],
                        provider=row["provider"],
                        internal_id=row["internal_id"],
                        created_at=row["created_at"],
                        last_used_at=now,
                        metadata=metadata,
                    )
            except sqlite3.Error as err:
                logger.warning("session_mapper: get() failed: %s", err)
                return None

    def set(
        self,
        scope: str,
        external_id: str,
        provider: str,
        internal_id: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Persist a mapping. Returns True on success, False on db error.

        Upserts: a second call with a different ``internal_id`` replaces
        the stored id. That happens when the provider rotates its own
        session id (e.g. a Claude conversation forks).
        """
        if not _enabled():
            return False
        meta_json = json.dumps(metadata or {})
        now = time.time()
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO session_map "
                        "(scope, external_id, provider, internal_id, created_at, last_used_at, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(scope, external_id, provider) DO UPDATE SET "
                        "internal_id=excluded.internal_id, "
                        "last_used_at=excluded.last_used_at, "
                        "metadata=excluded.metadata",
                        (scope, external_id, provider, internal_id, now, now, meta_json),
                    )
                return True
            except sqlite3.Error as err:
                logger.warning("session_mapper: set() failed: %s", err)
                return False

    def delete(self, scope: str, external_id: str, provider: str) -> bool:
        """Remove a mapping. Used on explicit session invalidation."""
        if not _enabled():
            return False
        with self._lock:
            try:
                with self._connect() as conn:
                    cursor = conn.execute(
                        "DELETE FROM session_map "
                        "WHERE scope=? AND external_id=? AND provider=?",
                        (scope, external_id, provider),
                    )
                    return cursor.rowcount > 0
            except sqlite3.Error as err:
                logger.warning("session_mapper: delete() failed: %s", err)
                return False

    def prune_older_than(self, max_age_seconds: float) -> int:
        """Delete records whose ``last_used_at`` is older than cutoff.

        Returns the number of rows removed. Callers schedule this (cron /
        startup hook); the mapper itself never auto-prunes so a long-idle
        session isn't silently dropped.
        """
        if not _enabled():
            return 0
        cutoff = time.time() - max_age_seconds
        with self._lock:
            try:
                with self._connect() as conn:
                    cursor = conn.execute(
                        "DELETE FROM session_map WHERE last_used_at < ?", (cutoff,)
                    )
                    return cursor.rowcount
            except sqlite3.Error as err:
                logger.warning("session_mapper: prune failed: %s", err)
                return 0

    def count(self) -> int:
        """Number of active mappings (diagnostics / tests)."""
        if not _enabled():
            return 0
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) AS n FROM session_map"
                    ).fetchone()
                    return int(row["n"]) if row else 0
            except sqlite3.Error:
                return 0


# ── Process-wide singleton ─────────────────────────────────────────────

_singleton: Optional[SessionMap] = None
_singleton_lock = threading.Lock()


def get_session_mapper() -> SessionMap:
    """Return the process-wide :class:`SessionMap`, creating it lazily.

    A fresh :class:`SessionMap` can still be constructed directly with a
    path override — tests use that to get per-test isolation.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = SessionMap()
    return _singleton


__all__ = [
    "SessionMap",
    "SessionRecord",
    "get_session_mapper",
]
