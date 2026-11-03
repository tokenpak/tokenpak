# SPDX-License-Identifier: Apache-2.0
"""Single writer, owned by one companion process; no provider calls or bodies."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import tempfile
import time
from pathlib import Path

from tokenpak.core.runtime.update_pending import cached_detect, marker_path
from tokenpak.status.binding import current_session, run_dir
from tokenpak.status.display import WIDTHS, render
from tokenpak.status.snapshot import MAX_AGE, fetch_snapshot

_last_written: tuple[Path, str] | None = None
POLL_SECONDS = 2.0
IDLE_REFRESH_SECONDS = 30.0
SESSION_IDLE_SECONDS = 60 * 60
logger = logging.getLogger(__name__)


def _file_stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        info = path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    except OSError:
        return None


class InputWatch:
    """Watch a fixed set of metadata paths; never enumerate native history.

    Remote/proxy-only changes are picked up by the periodic refresh. Local
    ledger, configuration and session changes wake the writer on the next poll.
    """

    def __init__(self, directory: Path) -> None:
        from tokenpak import _paths

        self.directory = directory
        home = _paths.home()
        # Use the canonical bounded candidate list, including files that do
        # not exist yet. The diagnostic API counts database rows; a display
        # watcher needs paths only, never a database connection.
        databases = _paths._monitor_db_candidates()
        self.paths = (
            directory,
            directory / "current-session",
            directory / "settings.json",
            directory / "session_title.json",
            home / "config.json",
            home / "config.yaml",
            marker_path(),
        ) + tuple(
            path for database in databases for path in (database, Path(str(database) + "-wal"))
        )
        # Claude's ordinary project transcript has an exact session filename.
        # If that layout is absent (e.g. another client), do not search history
        # or accidentally bind this launch to somebody else's latest session.
        config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        project = re.sub(r"[^A-Za-z0-9-]", "-", str(Path.cwd()))
        self.transcripts = config / "projects" / project

    def stamp(self) -> tuple:
        """Return bounded metadata plus the validated, bounded session marker."""
        session = current_session()
        paths = self.paths + ((self.transcripts / f"{session}.jsonl",) if session else ())
        return session, tuple(_file_stamp(path) for path in paths)


class SessionLifetime:
    """End abandoned writers using only this launch's activity, never output.

    A transcript must have been observed before its disappearance ends a
    session: hooks may bind an identity before the client creates the file.
    Clients without this native layout still have the run-directory idle fence.
    """

    def __init__(self, directory: Path, transcripts: Path) -> None:
        self.directory = directory
        self.transcripts = transcripts
        self.session = ""
        self.seen_transcript = False
        self.previous: tuple | None = None
        self.last_activity = time.monotonic()

    def alive(self) -> bool:
        """Check deletion and one-hour inactivity without reading file bodies."""
        directory = _file_stamp(self.directory)
        if directory is None:
            return False
        session = current_session()
        marker = _file_stamp(self.directory / "current-session")
        if self.session and not session and marker is None:
            return False
        transcript = _file_stamp(self.transcripts / f"{session}.jsonl") if session else None
        now = time.monotonic()
        if session != self.session:
            self.session = session
            self.seen_transcript = False
        if transcript is None and self.seen_transcript:
            return False
        self.seen_transcript = transcript is not None
        signature = (session, directory, marker, transcript)
        if self.previous is None:
            newest = max(info[3] / 1e9 for info in (directory, marker, transcript) if info)
            self.last_activity = now - max(0.0, time.time() - newest)
        elif signature != self.previous:
            self.last_activity = now
        self.previous = signature
        return now - self.last_activity < SESSION_IDLE_SECONDS


def atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def pending_version() -> str:
    """Version of an installed or staged update that is not running yet, else ``""``.

    Cached for a minute, so the writer's roughly two-second refresh adds one
    loopback ``/health`` read per minute at most. Never raises.
    """
    try:
        found = cached_detect()
        return found.target or "" if found.pending else ""
    except Exception:
        return ""


def refresh(directory: Path, proxy: str, routing_mode: str) -> None:
    global _last_written
    session = current_session()
    if not session:
        return
    snapshot = fetch_snapshot(session, proxy, routing_mode=routing_mode)
    pending = pending_version()
    # Cache key is validated by current_session before it reaches any path.
    cache = directory / "status"
    cache.mkdir(mode=0o700, exist_ok=True)
    content = f"{int(snapshot.generated_at + MAX_AGE)}\n"
    content += "".join(f"{width}|{render(snapshot, width, pending)}\n" for width in WIDTHS)
    atomic_write(cache / f"{session}.line", content)
    atomic_write(cache / f"{session}.json", json.dumps(snapshot.to_dict(), sort_keys=True))
    # /clear replaces only the pair this writer last produced. Never enumerate
    # the run directory: unrelated entries must not make every tick more work.
    # Files from an earlier writer remain expired and can be cleaned at launch
    # disposal; they are not this process's ownership to infer from filenames.
    previous = _last_written
    _last_written = (directory, session)
    if previous and previous[0] == directory and previous[1] != session:
        for suffix in (".line", ".json"):
            (cache / f"{previous[1]}{suffix}").unlink(missing_ok=True)


def main() -> int:
    parent = os.getppid()
    active = True

    def stop(signum, frame):
        nonlocal active
        active = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    directory = run_dir()
    proxy = os.environ.get("TOKENPAK_FORECAST_PROXY", "http://127.0.0.1:8766")
    routing = os.environ.get("TOKENPAK_FORECAST_ROUTING", "unknown")
    watch = InputWatch(directory)
    lifetime = SessionLifetime(directory, watch.transcripts)
    previous = None
    next_refresh = 0.0
    retrying = False
    while active and os.getppid() == parent and parent != 1:
        try:
            if not lifetime.alive():
                break
            stamp = watch.stamp()
            if time.monotonic() >= next_refresh or (stamp != previous and not retrying):
                # Set the deadline even on failure. Failed observations keep
                # their original expiry; sleeping never makes old data fresh.
                next_refresh = time.monotonic() + IDLE_REFRESH_SECONDS
                previous = stamp
                refresh(directory, proxy, routing)
                retrying = False
        except Exception as exc:
            # This process is a disposable display boundary. Never let an
            # unexpected observation error create a busy retry loop, and never
            # include remote response text or local paths in its diagnostic.
            retrying = True
            next_refresh = time.monotonic() + IDLE_REFRESH_SECONDS
            logger.warning("Status observation failed (%s); retry deferred", type(exc).__name__)
        for _ in range(round(POLL_SECONDS / 0.1)):
            if not active or os.getppid() != parent:
                break
            time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
