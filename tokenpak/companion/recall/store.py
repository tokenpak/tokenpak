# SPDX-License-Identifier: Apache-2.0
"""``RecallStore`` — opens (and lazily migrates) the recall storage database.

Surface
-------
The module exposes:

- ``RecallStore`` — a thin wrapper around an ``sqlite3.Connection`` that
  knows how to open the file, apply PRAGMAs, run any pending migrations,
  and close. The underlying connection is exposed as ``self.conn`` for
  later code to attach further helpers without changing this surface.
- ``RecallStore.upsert_pak(...)`` — metadata-only write path. Inserts a
  new row keyed on ``pak_id`` or replaces the existing row's metadata
  in place. The v2 FTS triggers keep ``paks_fts`` consistent.
- ``open_recall_store(path)`` — convenience factory that resolves the
  default DB location when ``path`` is ``None``.
- ``UpsertResult`` — NamedTuple describing the outcome of an upsert.

This module does *not* expose any read / query / recall surface; that
is deferred to a later phase.

Default DB path resolution
--------------------------
1. ``TOKENPAK_RECALL_DB`` env var (matches the broader ``tokenpak.core.paths``
   convention).
2. ``~/.tokenpak/companion/recall.db`` (companion subsystem default,
   matching ``journal.db`` placement).

The parent directory is created on first open.

Concurrency
-----------
WAL is enabled so concurrent readers don't block writers. A 5-second
``busy_timeout`` covers transient lock contention. ``foreign_keys`` is
on so cascade behaviour from the schema fires as expected.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import NamedTuple, Optional

from tokenpak.companion.recall.migrations import apply_migrations, current_version
from tokenpak.companion.recall.schema import SCHEMA_VERSION

_DEFAULT_REL_PATH = ".tokenpak/companion/recall.db"
_ENV_VAR = "TOKENPAK_RECALL_DB"

_log = logging.getLogger(__name__)


# Required (non-empty, non-whitespace) fields on ``upsert_pak``.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "pak_id",
    "pak_type",
    "source_type",
    "authority",
    "title",
    "content_hash",
)


class UpsertResult(NamedTuple):
    """The outcome of a single :meth:`RecallStore.upsert_pak` call.

    Attributes:
        pak_id: The stable identity the row was written under.
        inserted: ``True`` if the row was newly created; ``False`` if an
            existing row was updated in place.
        body_changed: ``True`` iff an existing row's ``content_hash``
            differed from the incoming value. Always ``False`` when
            ``inserted`` is ``True``.
    """

    pak_id: str
    inserted: bool
    body_changed: bool


def _utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_recall_db_path() -> Path:
    """Resolve the default recall DB path, honouring the env override."""
    if override := os.environ.get(_ENV_VAR):
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_REL_PATH


class RecallStore:
    """Recall storage handle.

    Open via :meth:`RecallStore.open` (or the module-level
    :func:`open_recall_store`). The instance is a context manager so
    callers can ``with RecallStore.open() as store: ...`` and have the
    connection closed deterministically.
    """

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        # Direct construction is allowed but ``open`` is the supported path.
        self._conn = conn
        self._path = path

    @classmethod
    def open(cls, path: Optional[Path] = None) -> "RecallStore":
        """Open (and migrate if needed) the recall store at ``path``.

        If ``path`` is ``None``, the default location is used.
        """
        resolved = path if path is not None else default_recall_db_path()
        resolved.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(resolved))
        try:
            cls._apply_pragmas(conn)
            apply_migrations(conn)
        except Exception:
            conn.close()
            raise

        return cls(conn=conn, path=resolved)

    @staticmethod
    def _apply_pragmas(conn: sqlite3.Connection) -> None:
        # ``execute`` is fine — these are session-level pragmas, not statements
        # that need to be inside a transaction.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying SQLite connection.

        Later PRs build their query/write helpers on top of this. PR 1
        exposes the connection as the only public surface beyond open/close.
        """
        return self._conn

    @property
    def path(self) -> Path:
        """The filesystem path the store was opened at."""
        return self._path

    @property
    def schema_version(self) -> int:
        """The schema version currently applied to the underlying DB."""
        return current_version(self._conn)

    # Metadata write surface ------------------------------------------------

    def upsert_pak(
        self,
        *,
        pak_id: str,
        pak_type: str,
        source_type: str,
        authority: str,
        title: str,
        content_hash: str,
        summary: str = "",
        project: Optional[str] = None,
        topic: Optional[str] = None,
        superseded_by: Optional[str] = None,
        now: Optional[str] = None,
    ) -> UpsertResult:
        """Insert or update a single Pak metadata row.

        The write is keyed on ``pak_id`` — that is the stable external
        identity callers use to address a Pak. Behaviour:

        - **No existing row** → INSERT. ``created_at`` and ``updated_at``
          are both set to ``now`` (or the current UTC time if ``now``
          is omitted).
        - **Existing row, identical ``content_hash``** → metadata-only
          UPDATE. ``created_at`` is preserved; ``updated_at`` is bumped.
        - **Existing row, different ``content_hash``** → UPDATE that
          replaces metadata and bumps ``content_hash`` / ``updated_at``.
          A warning is emitted (``logging.WARNING``) because the source
          object changed under the same identity; downstream audit /
          versioning lands in a later PR.

        Required (non-empty) fields are listed in ``_REQUIRED_FIELDS``;
        ``ValueError`` is raised if any are missing or all-whitespace.

        The FTS5 shadow is kept consistent by the v2 triggers — callers
        do not write to ``paks_fts`` directly.

        Parameters:
            pak_id: Stable Pak identity (e.g. ``vault://block/foo``).
            pak_type: Kind of Pak (e.g. ``vault``, ``code``).
            source_type: Source classification (e.g. ``code``, ``doc``).
            authority: Authority label for the source.
            title: Short heading; indexed in FTS.
            content_hash: Hex digest of the underlying body bytes.
            summary: Short summary; indexed in FTS. Defaults to ``""``.
            project: Optional project tag.
            topic: Optional topic tag.
            superseded_by: Optional ``pak_id`` of a superseding row.
            now: Optional ISO-8601 UTC string for deterministic testing.

        Returns:
            :class:`UpsertResult` describing what happened.

        Raises:
            ValueError: One of the required fields was missing /
                empty / whitespace-only.
            sqlite3.IntegrityError: A FK constraint failed (e.g. an
                unknown ``superseded_by``).
        """
        # Validation -------------------------------------------------------
        values = {
            "pak_id": pak_id,
            "pak_type": pak_type,
            "source_type": source_type,
            "authority": authority,
            "title": title,
            "content_hash": content_hash,
        }
        for name in _REQUIRED_FIELDS:
            v = values[name]
            if v is None or not isinstance(v, str) or not v.strip():
                raise ValueError(
                    f"upsert_pak: required field {name!r} must be a non-empty string"
                )
        if summary is None:
            summary = ""

        ts = now if now is not None else _utc_now_iso8601()
        conn = self._conn

        # Transaction ------------------------------------------------------
        # ``apply_migrations`` leaves the connection in autocommit mode
        # (``isolation_level=None``); make BEGIN explicit so the lookup +
        # write happen atomically and the FTS triggers fire as one unit.
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT content_hash FROM paks WHERE pak_id = ?",
                (pak_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO paks ("
                    "pak_id, pak_type, project, topic, source_type, authority, "
                    "title, summary, content_hash, created_at, updated_at, superseded_by"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pak_id,
                        pak_type,
                        project,
                        topic,
                        source_type,
                        authority,
                        title,
                        summary,
                        content_hash,
                        ts,
                        ts,
                        superseded_by,
                    ),
                )
                inserted = True
                body_changed = False
            else:
                old_hash = row[0]
                body_changed = old_hash != content_hash
                conn.execute(
                    "UPDATE paks SET "
                    "pak_type = ?, project = ?, topic = ?, source_type = ?, "
                    "authority = ?, title = ?, summary = ?, content_hash = ?, "
                    "updated_at = ?, superseded_by = ? "
                    "WHERE pak_id = ?",
                    (
                        pak_type,
                        project,
                        topic,
                        source_type,
                        authority,
                        title,
                        summary,
                        content_hash,
                        ts,
                        superseded_by,
                        pak_id,
                    ),
                )
                inserted = False
                if body_changed:
                    _log.warning(
                        "recall.upsert_pak: content_hash changed for pak_id=%s "
                        "(old=%s new=%s); replacing metadata in place.",
                        pak_id,
                        _short_hash(old_hash),
                        _short_hash(content_hash),
                    )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

        return UpsertResult(pak_id=pak_id, inserted=inserted, body_changed=body_changed)

    def close(self) -> None:
        """Close the underlying connection.

        Safe to call multiple times.
        """
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            # Already closed — fine.
            pass

    # Context-manager sugar -------------------------------------------------

    def __enter__(self) -> "RecallStore":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()


def open_recall_store(path: Optional[Path] = None) -> RecallStore:
    """Module-level convenience wrapper around :meth:`RecallStore.open`."""
    return RecallStore.open(path)


def _short_hash(value: object) -> str:
    """Render a hash for log lines without leaking the whole digest."""
    s = "" if value is None else str(value)
    return s[:12] + ("…" if len(s) > 12 else "")


__all__ = [
    "RecallStore",
    "UpsertResult",
    "default_recall_db_path",
    "open_recall_store",
    "SCHEMA_VERSION",
]
