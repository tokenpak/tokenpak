"""TokenPak Trigger Daemon — watches file, timer, and cost events.

Runs as a lightweight background process; zero LLM calls.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .matcher import match_event
from .store import Trigger, TriggerStore


def _parse_interval_seconds(interval: str) -> int:
    """Parse '5m', '30s', '2h' → seconds."""
    m = re.match(r"^(\d+)([smh])$", interval)
    if not m:
        raise ValueError(f"Invalid timer interval: {interval!r}. Use e.g. 30s, 5m, 1h")
    val, unit = int(m.group(1)), m.group(2)
    return val * {"s": 1, "m": 60, "h": 3600}[unit]


def _run_action(trigger: Trigger, store: TriggerStore) -> None:
    """Execute trigger action and log result."""
    cmd = trigger.action
    # If action looks like a tokenpak sub-command, prefix with 'tokenpak'
    if not cmd.startswith("/") and not cmd.startswith("./") and not cmd.startswith("~"):
        cmd = f"tokenpak {cmd}"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        output = (result.stdout + result.stderr).strip()
        store.log_fire(trigger, result.returncode, output)
    except subprocess.TimeoutExpired:
        store.log_fire(trigger, -1, "timeout")
    except Exception as exc:
        store.log_fire(trigger, -2, str(exc))


class TriggerDaemon:
    """Watches file system, timers, and cost thresholds; fires matching triggers."""

    POLL_INTERVAL = 2  # seconds between file-check cycles
    COST_CHECK_INTERVAL = 60  # seconds between cost threshold checks

    def __init__(self, store: Optional[TriggerStore] = None):
        self.store = store or TriggerStore()
        self._stop_event = threading.Event()
        self._timer_last_fire: Dict[str, float] = {}
        self._file_mtimes: Dict[str, float] = {}
        self._known_files: Dict[str, set] = {}  # glob→set of paths
        self._cost_last_check = 0.0

    # ── public ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Block and run daemon until stop() is called."""
        print("TokenPak trigger daemon started (Ctrl-C to stop)")
        try:
            while not self._stop_event.is_set():
                self._tick()
                self._stop_event.wait(timeout=self.POLL_INTERVAL)
        except KeyboardInterrupt:
            pass
        print("TokenPak trigger daemon stopped")

    def stop(self) -> None:
        self._stop_event.set()

    # ── internal ─────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        triggers = [t for t in self.store.list() if t.enabled]
        now = time.time()

        for trigger in triggers:
            pat = trigger.event

            # timer
            if pat.startswith("timer:"):
                interval_str = pat[len("timer:") :]
                try:
                    interval = _parse_interval_seconds(interval_str)
                except ValueError:
                    continue
                last = self._timer_last_fire.get(trigger.id, 0.0)
                if now - last >= interval:
                    self._timer_last_fire[trigger.id] = now
                    threading.Thread(
                        target=_run_action, args=(trigger, self.store), daemon=True
                    ).start()

            # file:changed / file:created — handled via mtime polling
            elif pat.startswith("file:changed:") or pat.startswith("file:created:"):
                kind, glob = pat.split(":", 2)[1], pat.split(":", 2)[2]
                self._check_file_event(trigger, kind, glob)

            # cost threshold
            elif pat.startswith("cost:daily>"):
                if now - self._cost_last_check >= self.COST_CHECK_INTERVAL:
                    self._cost_last_check = now
                    self._check_cost_threshold(triggers)
                    break  # only check once per batch

    def _check_file_event(self, trigger: Trigger, kind: str, glob: str) -> None:
        import fnmatch as _fnmatch
        from pathlib import Path as _Path

        # Build candidate paths from glob
        base_dir = _Path.home()
        # Try to find a concrete base dir from glob
        parts = glob.split("/")
        # Walk up until we hit a wildcard segment
        concrete_parts = []
        for p in parts:
            if "*" in p or "?" in p:
                break
            concrete_parts.append(p)
        if concrete_parts and concrete_parts[0]:
            base_dir = (
                _Path("/".join(concrete_parts))
                if glob.startswith("/")
                else _Path.home() / "/".join(concrete_parts)
            )
        else:
            base_dir = _Path.cwd()

        if not base_dir.exists():
            return

        try:
            current_files = {
                str(f): f.stat().st_mtime
                for f in base_dir.rglob("*")
                if f.is_file() and _fnmatch.fnmatch(f.name, parts[-1] if parts else "*")
            }
        except PermissionError:
            return

        known_mtimes = self._file_mtimes

        if kind == "changed":
            for path, mtime in current_files.items():
                old = known_mtimes.get(path)
                if old is not None and mtime > old:
                    known_mtimes[path] = mtime
                    event_str = f"file:changed:{path}"
                    if match_event(trigger.event, event_str):
                        threading.Thread(
                            target=_run_action, args=(trigger, self.store), daemon=True
                        ).start()
                        return
                elif old is None:
                    known_mtimes[path] = mtime

        elif kind == "created":
            for path, mtime in current_files.items():
                if path not in known_mtimes:
                    known_mtimes[path] = mtime
                    event_str = f"file:created:{path}"
                    if match_event(trigger.event, event_str):
                        threading.Thread(
                            target=_run_action, args=(trigger, self.store), daemon=True
                        ).start()
                        return

    def _check_cost_threshold(self, triggers: List[Trigger]) -> None:
        """Read daily cost from telemetry.db and fire cost threshold triggers."""
        db_path = Path.home() / ".tokenpak" / "telemetry.db"
        if not db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.execute(
                "SELECT COALESCE(SUM(cost_saved),0) FROM tp_requests WHERE date(timestamp)=date('now')"
            )
            daily_cost = float(cur.fetchone()[0] or 0)
            conn.close()
        except Exception:
            return

        event_str = f"cost:daily>{daily_cost:.2f}"
        for trigger in triggers:
            if trigger.event.startswith("cost:daily>") and match_event(trigger.event, event_str):
                threading.Thread(
                    target=_run_action, args=(trigger, self.store), daemon=True
                ).start()
