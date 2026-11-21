"""The forecast calibration readers do not pin an extra monitor.db descriptor.

While the proxy's ledger writer holds ``monitor.db`` open, SQLite's unix VFS
keeps the descriptor of a closed connection for reuse by the next connection
opened in the same mode (read-write or read-only), so the process's
``monitor.db`` descriptor count is the writer plus the most connections ever
open at once in each mode. The calibration readers used ``mode=ro`` while
every other reader opens read-write, which left one more descriptor open for
the life of the process. They now open ``mode=rw`` with ``query_only``: still
unable to write, still unable to create an absent file.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

from tokenpak.proxy import session_forecast_calibration, time_forecast_calibration

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="counts descriptors in /proc/self/fd"
)

READERS = [session_forecast_calibration, time_forecast_calibration]


def _db_fds(path: str) -> int:
    count = 0
    for name in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{name}") == path:
                count += 1
        except OSError:
            pass
    return count


@pytest.mark.parametrize("module", READERS, ids=lambda m: m.__name__.rsplit(".", 1)[1])
def test_readers_share_the_read_write_descriptor(tmp_path, module) -> None:
    db = str(tmp_path / "monitor.db")
    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, session_id TEXT)")
        writer.execute("INSERT INTO requests (session_id) VALUES ('s')")
        writer.commit()
        for _ in range(5):
            conn = module._connect_ro(db)
            try:
                assert conn.execute("SELECT COUNT(*) FROM requests").fetchone() == (1,)
            finally:
                conn.close()
            other = sqlite3.connect(db)
            try:
                other.execute("SELECT COUNT(*) FROM requests").fetchone()
            finally:
                other.close()
        # The writer and one descriptor parked for reuse; a read-only reader
        # would have parked a second one.
        assert _db_fds(db) == 2
    finally:
        writer.close()


@pytest.mark.parametrize("module", READERS, ids=lambda m: m.__name__.rsplit(".", 1)[1])
def test_readers_cannot_write_or_create(tmp_path, module) -> None:
    db = str(tmp_path / "monitor.db")
    setup = sqlite3.connect(db)
    setup.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY)")
    setup.commit()
    setup.close()
    conn = module._connect_ro(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO requests DEFAULT VALUES")
    finally:
        conn.close()
    missing = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        module._connect_ro(str(missing)).close()
    assert not missing.exists()
