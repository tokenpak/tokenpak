# SPDX-License-Identifier: Apache-2.0
"""``RecallStore`` — opens (and lazily migrates) the recall storage database.

PR-1 surface
------------
This module is intentionally minimal. It exposes:

- ``RecallStore`` — a thin wrapper around an ``sqlite3.Connection`` that
  knows how to open the file, apply PRAGMAs, run any pending migrations,
  and close. The underlying connection is exposed as ``self.conn`` for
  later PRs to attach read/write helpers without changing this surface.
- ``open_recall_store(path)`` — convenience factory that resolves the
  default DB location when ``path`` is ``None``.

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

import os
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Optional

from tokenpak.companion.recall.migrations import apply_migrations, current_version
from tokenpak.companion.recall.schema import SCHEMA_VERSION

_DEFAULT_REL_PATH = ".tokenpak/companion/recall.db"
_ENV_VAR = "TOKENPAK_RECALL_DB"


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


__all__ = [
    "RecallStore",
    "default_recall_db_path",
    "open_recall_store",
    "SCHEMA_VERSION",
]
