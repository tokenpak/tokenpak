# SPDX-License-Identifier: Apache-2.0
"""Single writer, owned by one companion process; no provider calls or bodies."""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
from pathlib import Path

from tokenpak.companion.session_binding import current_session, run_dir, valid_session
from tokenpak.status.display import WIDTHS, render
from tokenpak.status.snapshot import MAX_AGE, fetch_snapshot


def atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def refresh(directory: Path, proxy: str, routing_mode: str) -> None:
    session = current_session()
    if not session:
        return
    snapshot = fetch_snapshot(session, proxy, routing_mode=routing_mode)
    # Cache key is validated by current_session before it reaches any path.
    cache = directory / "status"
    cache.mkdir(mode=0o700, exist_ok=True)
    content = f"{int(snapshot.generated_at + MAX_AGE)}\n"
    content += "".join(f"{width}|{render(snapshot, width)}\n" for width in WIDTHS)
    atomic_write(cache / f"{session}.line", content)
    atomic_write(cache / f"{session}.json", json.dumps(snapshot.to_dict(), sort_keys=True))
    # /clear can bind a new native session. Keep only this launch's current
    # disposable cache; never touch ledger or journal history.
    for old in cache.iterdir():
        if old.suffix in {".line", ".json"} and valid_session(old.stem) and old.stem != session:
            old.unlink(missing_ok=True)


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
    while active and os.getppid() == parent and parent != 1:
        try:
            refresh(directory, proxy, routing)
        except (OSError, ValueError, TypeError):
            pass  # Readers label expired caches stale; never keep a failed writer's value live.
        for _ in range(20):
            if not active or os.getppid() != parent:
                break
            time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
