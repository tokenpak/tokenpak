"""
tokenpak.agent.agentic.locks
────────────────────────────
File lock coordination for multi-agent environments.

Lock registry: ~/.tokenpak/locks/<sha256(path)>.json
Each lock record:
  {
    "path":       "/abs/path/to/file",
    "agent":      "cali",
    "acquired":   1234567890.0,   # epoch float
    "expires":    1234568490.0,   # acquired + timeout
    "pid":        12345
  }

Public API:
  manager = FileLockManager()
  manager.claim(path)              -> LockConflictError if taken
  manager.release(path)
  manager.locks()                  -> list[dict]
  manager.query(path)              -> dict | None
  manager.prune_expired()          -> int  (count removed)
  manager.suggest_alternatives(path, candidates) -> list[str]
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

DEFAULT_LOCK_DIR = Path.home() / ".tokenpak" / "locks"
DEFAULT_TIMEOUT_S = 600  # 10 minutes


class LockConflictError(Exception):
    """Raised when a file is already locked by another agent/process."""

    def __init__(self, path: str, lock_info: dict):
        self.path = path
        self.lock_info = lock_info
        agent = lock_info.get("agent", "unknown")
        expires = lock_info.get("expires", 0)
        remaining = max(0, expires - time.time())
        super().__init__(
            f"Lock conflict on '{path}': held by '{agent}', expires in {remaining:.0f}s"
        )


class LockExpiredError(Exception):
    """Raised when operating on a lock that has already expired."""


class FileLockManager:
    """
    File lock registry for multi-agent coordination.

    Parameters
    ----------
    agent_id : str
        Identifier for the agent claiming locks (default: $TOKENPAK_AGENT or 'cali').
    lock_dir : Path | str | None
        Directory where lock files are stored.
    timeout_s : int
        Default lock timeout in seconds.
    """

    def __init__(
        self,
        agent_id: Optional[str] = None,
        lock_dir: Optional[Path | str] = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ):
        self.agent_id = agent_id or os.environ.get("TOKENPAK_AGENT", "cali")
        self.lock_dir = Path(lock_dir or DEFAULT_LOCK_DIR)
        self.timeout_s = timeout_s
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _lock_key(self, path: str | Path) -> str:
        """Stable filename derived from the absolute path."""
        abs_path = str(Path(path).resolve())
        return hashlib.sha256(abs_path.encode()).hexdigest()[:16]

    def _lock_file(self, path: str | Path) -> Path:
        return self.lock_dir / f"{self._lock_key(path)}.json"

    def _read_lock(self, lock_file: Path) -> Optional[dict]:
        try:
            return json.loads(lock_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _write_lock(self, lock_file: Path, record: dict) -> None:
        tmp = lock_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2))
        tmp.replace(lock_file)  # atomic on POSIX

    # ── public API ───────────────────────────────────────────────────────────

    def claim(
        self,
        path: str | Path,
        timeout_s: Optional[int] = None,
    ) -> dict:
        """
        Claim a lock on *path*.

        Returns the lock record on success.
        Raises LockConflictError if another agent holds a live lock.
        """
        abs_path = str(Path(path).resolve())
        timeout = timeout_s if timeout_s is not None else self.timeout_s
        lock_file = self._lock_file(abs_path)

        existing = self._read_lock(lock_file)
        if existing:
            if time.time() < existing.get("expires", 0):
                # Live lock held by someone else?
                if existing.get("agent") != self.agent_id:
                    raise LockConflictError(abs_path, existing)
                # Same agent — re-affirm (extend)
            # else: expired — we can steal it

        now = time.time()
        record = {
            "path": abs_path,
            "agent": self.agent_id,
            "acquired": now,
            "expires": now + timeout,
            "pid": os.getpid(),
        }
        self._write_lock(lock_file, record)
        return record

    def release(self, path: str | Path) -> bool:
        """
        Release the lock on *path*.

        Returns True if removed, False if lock did not exist or belonged
        to another agent (does not raise — safe to call on cleanup).
        """
        abs_path = str(Path(path).resolve())
        lock_file = self._lock_file(abs_path)
        existing = self._read_lock(lock_file)
        if not existing:
            return False
        if existing.get("agent") != self.agent_id:
            return False
        try:
            lock_file.unlink()
            return True
        except FileNotFoundError:
            return False

    def query(self, path: str | Path) -> Optional[dict]:
        """Return lock info for *path*, or None if unlocked / expired."""
        abs_path = str(Path(path).resolve())
        lock_file = self._lock_file(abs_path)
        record = self._read_lock(lock_file)
        if not record:
            return None
        if time.time() >= record.get("expires", 0):
            # Auto-prune expired
            try:
                lock_file.unlink()
            except FileNotFoundError:
                pass
            return None
        return record

    def locks(self) -> list[dict]:
        """Return all live (non-expired) lock records."""
        self.prune_expired()
        result = []
        for lf in self.lock_dir.glob("*.json"):
            record = self._read_lock(lf)
            if record:
                result.append(record)
        return sorted(result, key=lambda r: r.get("acquired", 0))

    def prune_expired(self) -> int:
        """Remove expired lock files. Returns count removed."""
        removed = 0
        now = time.time()
        for lf in self.lock_dir.glob("*.json"):
            record = self._read_lock(lf)
            if record and now >= record.get("expires", 0):
                try:
                    lf.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
            elif not record:
                try:
                    lf.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        return removed

    def suggest_alternatives(
        self,
        blocked_path: str | Path,
        candidates: list[str | Path],
    ) -> list[str]:
        """
        Given a list of candidate file paths, return those that are NOT
        currently locked — i.e. viable alternatives when *blocked_path* is taken.
        """
        return [str(c) for c in candidates if self.query(c) is None]

    def renew(self, path: str | Path, timeout_s: Optional[int] = None) -> dict:
        """
        Renew (extend) an existing lock held by this agent.

        Raises LockConflictError if path is locked by another agent.
        Raises LockExpiredError if the lock has already expired.
        Returns the updated lock record on success.
        """
        abs_path = str(Path(path).resolve())
        lock_file = self._lock_file(abs_path)
        existing = self._read_lock(lock_file)

        if not existing:
            raise LockExpiredError(f"No lock found on '{abs_path}' — cannot renew.")

        if time.time() >= existing.get("expires", 0):
            try:
                lock_file.unlink(missing_ok=True)
            except OSError:
                pass
            raise LockExpiredError(f"Lock on '{abs_path}' has already expired.")

        if existing.get("agent") != self.agent_id:
            raise LockConflictError(abs_path, existing)

        timeout = timeout_s if timeout_s is not None else self.timeout_s
        now = time.time()
        updated = dict(existing)
        updated["expires"] = now + timeout
        updated["renewed"] = now
        self._write_lock(lock_file, updated)
        return updated
