"""TokenPak Telemetry Collector — watches session files and sends to ingest API."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


@dataclass
class CollectorConfig:
    """Configuration for the TelemetryCollector file watcher."""

    watch_paths: list[Path] = field(default_factory=list)
    ingest_url: str = "http://localhost:17888/v1/telemetry/ingest"
    batch_size: int = 10
    batch_timeout_seconds: float = 5.0
    file_patterns: list[str] = field(default_factory=lambda: ["*.jsonl", "*.json"])
    backfill_on_start: bool = False
    state_file: Optional[Path] = None


@dataclass
class FileState:
    """Tracks last-seen mtime and size of a watched file."""

    path: Path
    last_position: int = 0
    last_modified: float = 0.0
    events_sent: int = 0


class TelemetryCollector:
    """Watches the tokenpak telemetry DB and emits events to subscribers."""

    def __init__(self, config: CollectorConfig):
        self.config = config
        self.file_states: dict[str, FileState] = {}
        self.pending_events: list[dict[str, Any]] = []
        self.last_flush_time = time.time()
        self.observer: Optional[Observer] = None  # type: ignore
        self._running = False
        if config.state_file and config.state_file.exists():
            self._load_state()

    def start(self, blocking: bool = True):
        """Start the file watcher background thread."""
        self._running = True
        if self.config.backfill_on_start:
            self.backfill()
        self.observer = Observer()
        handler = _FileEventHandler(self._on_file_change)
        for watch_path in self.config.watch_paths:
            if watch_path.exists():
                self.observer.schedule(handler, str(watch_path), recursive=True)
        self.observer.start()
        if blocking:
            try:
                while self._running:
                    time.sleep(1)
                    self._check_flush_timeout()
            except KeyboardInterrupt:
                self.stop()

    def stop(self):
        """Stop the file watcher and clean up resources."""
        self._running = False
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self._flush_batch(force=True)
        self._save_state()

    def backfill(self, paths: Optional[list[Path]] = None):
        """Emit stored events from before the watcher was started."""
        for base_path in paths or self.config.watch_paths:
            if not base_path.exists():
                continue
            for pattern in self.config.file_patterns:
                for file_path in base_path.rglob(pattern):
                    self._process_file(file_path, from_start=True)
        self._flush_batch(force=True)

    def _on_file_change(self, event):
        file_path = Path(event.src_path)
        if any(file_path.match(p) for p in self.config.file_patterns):
            self._process_file(file_path)

    def _process_file(self, file_path: Path, from_start: bool = False):
        key = str(file_path)
        if key not in self.file_states:
            self.file_states[key] = FileState(path=file_path)
        state = self.file_states[key]
        if from_start:
            state.last_position = 0
        try:
            with open(file_path, "r") as f:
                f.seek(state.last_position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            event = json.loads(line)
                            event["_source_file"] = str(file_path)
                            self.pending_events.append(event)
                        except json.JSONDecodeError:
                            pass
                state.last_position = f.tell()
                state.last_modified = time.time()
            if len(self.pending_events) >= self.config.batch_size:
                self._flush_batch()
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")

    def _check_flush_timeout(self):
        if (
            self.pending_events
            and (time.time() - self.last_flush_time) >= self.config.batch_timeout_seconds
        ):
            self._flush_batch()

    def _flush_batch(self, force: bool = False):
        if not self.pending_events:
            self.last_flush_time = time.time()
            return
        events = self.pending_events[: self.config.batch_size]
        try:
            response = requests.post(self.config.ingest_url, json={"events": events}, timeout=30)
            response.raise_for_status()
            for event in events:
                source = event.get("_source_file")
                if source and source in self.file_states:
                    self.file_states[source].events_sent += 1
            self.pending_events = self.pending_events[len(events) :]
        except Exception as e:
            logger.error(f"Failed to send events: {e}")
        self.last_flush_time = time.time()

    def _load_state(self):
        try:
            with open(self.config.state_file, "r") as f:
                data = json.load(f)
                for key, s in data.get("file_states", {}).items():
                    self.file_states[key] = FileState(
                        path=Path(s["path"]),
                        last_position=s.get("last_position", 0),
                        last_modified=s.get("last_modified", 0.0),
                        events_sent=s.get("events_sent", 0),
                    )
        except Exception:
            pass

    def _save_state(self):
        if not self.config.state_file:
            return
        try:
            data = {
                "file_states": {
                    k: {
                        "path": str(s.path),
                        "last_position": s.last_position,
                        "last_modified": s.last_modified,
                        "events_sent": s.events_sent,
                    }
                    for k, s in self.file_states.items()
                }
            }
            self.config.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


class _FileEventHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable):
        self.callback = callback

    def on_modified(self, event):
        """Called by watchdog when a watched file changes."""
        if not event.is_directory:
            self.callback(event)

    def on_created(self, event):
        """Called by watchdog when a new file is created in the watched directory."""
        if not event.is_directory:
            self.callback(event)


def create_collector(
    watch_paths: list[str],
    ingest_url: str = "http://localhost:17888/v1/telemetry/ingest",
    backfill: bool = False,
    state_file: Optional[str] = None,
) -> TelemetryCollector:
    """Factory: create a TelemetryCollector from a config dict."""
    return TelemetryCollector(
        CollectorConfig(
            watch_paths=[Path(p) for p in watch_paths],
            ingest_url=ingest_url,
            backfill_on_start=backfill,
            state_file=Path(state_file) if state_file else None,
        )
    )
