"""Close cached per-thread SQLite connections when their thread ends.

On CPython 3.11+ a ``sqlite3.Connection`` refers to itself through its
statement cache, so a connection that is merely dropped stays open until the
cyclic garbage collector runs a full collection. Code that keeps one
connection per thread on a ``threading.local`` therefore leaves every finished
thread's connection open (with its descriptor and page cache) for an
unpredictable time.

Store a :class:`ThreadConnection` next to the cached connection on the same
``threading.local``. When a thread exits, CPython discards that thread's
local storage at once; the holder is not part of any reference cycle, so its
reference count reaches zero and it closes the connection immediately.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Optional


class ThreadConnection:
    """Owns one cached connection and closes it when the holder is dropped."""

    __slots__ = ("_conn", "_on_close", "__weakref__")

    def __init__(
        self,
        conn: sqlite3.Connection,
        on_close: Optional[Callable[[sqlite3.Connection], None]] = None,
    ) -> None:
        self._conn: Optional[sqlite3.Connection] = conn
        self._on_close = on_close

    def close(self) -> None:
        """Close the connection once; later calls do nothing."""
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass
        on_close, self._on_close = self._on_close, None
        if on_close is not None:
            try:
                on_close(conn)
            except Exception:
                pass

    def __del__(self) -> None:
        self.close()


__all__ = ["ThreadConnection"]
