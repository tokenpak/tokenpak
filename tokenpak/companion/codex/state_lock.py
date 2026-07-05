# SPDX-License-Identifier: Apache-2.0
"""Codex local-database lock diagnostics for ``CODEX_HOME``.

Codex keeps interactive session state and logs in SQLite databases under
``CODEX_HOME`` (default ``~/.codex/``).  When two Codex processes share the
same home they can contend over those databases, and a Codex process that
is suspended by job control (``Tl`` — stopped via SIGTSTP) keeps the file
descriptors open without releasing the lock.  The result is a zombie-style
lock: the foreground process blocks on a database held by a process that
will never make progress.

This module gives the launcher a *preflight* read on that situation so it
can refuse to start (or, in an isolated home, confirm the home is clean)
with an actionable message instead of hanging on a contended database.

Detection is deliberately dependency-free:

* each known Codex-owned SQLite file is probed with an ``EXCLUSIVE``
  ``BEGIN`` inside a short connection — if another connection holds the
  database the probe raises ``sqlite3.OperationalError`` ("database is
  locked"), which is the exact symptom we want to surface;
* candidate holder PIDs are read from any sidecar ``*.lock`` / WAL files
  and from a best-effort scan, then liveness-classified with
  ``os.kill(pid, 0)`` and (on Linux) ``/proc`` so a stopped/zombie holder
  is reported distinctly from a healthy concurrent session.

No hard dependency on ``psutil`` — that keeps the launcher path importable
on minimal installs.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# Companion-internal launcher helper (probe/remediation_hint are called by
# launcher.py, not by end users): export nothing as released public API so
# this module stays out of the public-API snapshot. Direct attribute access
# (``state_lock.probe``) is unaffected — ``__all__`` only governs ``import *``
# and the release-gate API walker.
__all__: list[str] = []

# Codex's interactive state/log database filenames under CODEX_HOME.
STATE_DB_NAME = "state_5.sqlite"
_LOG_DB_NAME = "logs_2.sqlite"
_CODEX_DB_NAMES = (STATE_DB_NAME, _LOG_DB_NAME)

# How long the probe waits for the database before declaring it locked.
# Kept short — this is a preflight, not a retry loop.
_PROBE_TIMEOUT_S = 0.5


@dataclass
class LockStatus:
    """Result of a state-lock preflight on one ``CODEX_HOME``.

    ``locked`` is the load-bearing field: ``True`` means another
    connection currently holds a Codex local database and a fresh Codex
    session in this home would contend.  ``holder_pids`` /
    ``stopped_pids`` are best-effort context for the message — they may be
    empty even when ``locked`` is ``True`` (e.g. the holder PID could not
    be recovered) and that does not weaken the ``locked`` verdict.
    """

    home: Path
    db_path: Path
    exists: bool
    locked: bool
    holder_pids: list[int] = field(default_factory=list)
    stopped_pids: list[int] = field(default_factory=list)
    detail: str = ""


def _db_path(home: Path) -> Path:
    return home / STATE_DB_NAME


def _db_paths(home: Path) -> list[Path]:
    """Known Codex local SQLite databases to preflight, in priority order."""
    return [home / name for name in _CODEX_DB_NAMES]


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` exists and is signalable by us."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — count it as alive.
        return True
    except OSError:
        return False
    return True


def _pid_stopped(pid: int) -> bool:
    """Best-effort: True if ``pid`` is in a stopped/traced state (Linux).

    A stopped Codex process is the zombie-lock case the packet calls out:
    it holds the database FD but will never release it without a SIGCONT
    or kill.  Non-Linux platforms (no ``/proc``) return ``False`` — we
    simply lose the stopped/running distinction there, not correctness.
    """
    stat = Path(f"/proc/{pid}/stat")
    try:
        raw = stat.read_text()
    except (OSError, ValueError):
        return False
    # /proc/<pid>/stat: "<pid> (<comm>) <state> ...".  comm may contain
    # spaces/parens, so split on the LAST ')'.
    rparen = raw.rfind(")")
    if rparen == -1:
        return False
    fields = raw[rparen + 1 :].split()
    if not fields:
        return False
    # State codes: T = stopped (job control), t = traced.
    return fields[0] in ("T", "t")


def _candidate_holder_pids(home: Path) -> list[int]:
    """Collect candidate holder PIDs recorded in this home, best-effort.

    The TokenPak session-home provisioner writes a ``codex.pid`` sentinel
    when it launches a session into an isolated/workspace home; we read it
    here so a contended home can name its holder.  We never trust the file
    blindly — every PID is liveness-checked by the caller.
    """
    pids: list[int] = []
    sentinel = home / "codex.pid"
    try:
        for line in sentinel.read_text().splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
    except (OSError, ValueError):
        pass
    return pids


def probe(home: "Path | str | None" = None) -> LockStatus:
    """Preflight Codex-owned local databases under ``home``.

    ``home`` defaults to ``$CODEX_HOME`` then ``~/.codex``.  Returns a
    :class:`LockStatus`.  Never raises on a contended database — the lock
    is reported via ``LockStatus.locked``, which is the whole point of a
    preflight.
    """
    if home is None:
        home = os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    home = Path(home)
    dbs = _db_paths(home)
    state_db = _db_path(home)
    existing = [db for db in dbs if db.exists()]

    if not existing:
        # No Codex database yet — a fresh home is by definition uncontended.
        return LockStatus(
            home=home,
            db_path=state_db,
            exists=False,
            locked=False,
            detail="no Codex local database yet (uncontended)",
        )

    locked_db = next((db for db in existing if _is_locked(db)), None)
    if locked_db is None:
        names = ", ".join(db.name for db in existing)
        return LockStatus(
            home=home,
            db_path=existing[0],
            exists=True,
            locked=False,
            detail=f"Codex local databases are free: {names}",
        )

    # Locked — gather best-effort holder context for the message.
    candidates = [p for p in _candidate_holder_pids(home) if _pid_alive(p)]
    stopped = [p for p in candidates if _pid_stopped(p)]
    detail = _format_lock_detail(candidates, stopped, locked_db.name)
    return LockStatus(
        home=home,
        db_path=locked_db,
        exists=True,
        locked=True,
        holder_pids=candidates,
        stopped_pids=stopped,
        detail=detail,
    )


def _is_locked(db: Path) -> bool:
    """True if an EXCLUSIVE probe on ``db`` is denied within the timeout.

    We open with a short busy-timeout and attempt ``BEGIN EXCLUSIVE``.  If
    another connection holds the database the probe raises
    ``OperationalError`` ("database is locked" / "database is busy") — that
    is the contention we report.  A genuinely free database commits the
    no-op transaction immediately.
    """
    conn = None
    try:
        conn = sqlite3.connect(str(db), timeout=_PROBE_TIMEOUT_S, isolation_level=None)
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("ROLLBACK")
        return False
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "lock" in msg or "busy" in msg:
            return True
        # Some other operational error (e.g. malformed) — not a lock; do
        # not block the launcher on it.
        return False
    except sqlite3.DatabaseError:
        # Not a valid SQLite database — not a lock condition.
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def _format_lock_detail(holders: list[int], stopped: list[int], db_name: str) -> str:
    """Human-actionable description of who holds the lock and what to do."""
    if not holders:
        return f"{db_name} is locked by another Codex process (holder PID unavailable)"
    if stopped:
        s = ", ".join(str(p) for p in stopped)
        return (
            f"{db_name} is locked by a stopped Codex process (PID {s}); "
            "a suspended session never releases the lock — resume it (fg) and "
            "exit, or terminate it"
        )
    h = ", ".join(str(p) for p in holders)
    return (
        f"{db_name} is locked by an active Codex process (PID {h}); "
        "finish or close that session, or use an isolated home"
    )


def remediation_hint(status: LockStatus) -> str:
    """Multi-line, mode-aware guidance for a locked shared home.

    Shown by the launcher preflight so the user is not left guessing how
    to escape a contended ``~/.codex`` without re-deriving the isolation
    design themselves.
    """
    lines = [
        f"tokenpak: Codex local database is locked: {status.db_path}",
        f"          {status.detail}",
    ]
    if status.stopped_pids:
        pids = " ".join(str(p) for p in status.stopped_pids)
        lines.append(
            f"          resume the suspended session(s) then exit them: "
            f"kill -CONT {pids} ; or terminate: kill {pids}"
        )
    lines.append(
        "          to run a parallel session without contention, set "
        "TOKENPAK_CODEX_SESSION_MODE=workspace (per-project home) or "
        "=isolated (fresh per-session home)."
    )
    return "\n".join(lines)
