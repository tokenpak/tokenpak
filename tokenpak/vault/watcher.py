"""TokenPak Agent Vault File Watcher — auto re-indexing on file changes.

Implements `tokenpak index --watch` (task 1.5).

Usage::

    from tokenpak.agent.vault.watcher import VaultWatcher, WatcherConfig

    config = WatcherConfig(watch_paths=["~/myproject"])
    watcher = VaultWatcher(config)
    watcher.start(blocking=True)  # Ctrl+C to stop
"""

from __future__ import annotations

import fnmatch
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_PATTERNS = [
    "*.pyc",
    "*.pyo",
    "__pycache__",
    ".git",
    ".svn",
    "*.swp",
    "*.swo",
    "*.tmp",
    ".DS_Store",
    "node_modules",
    ".tox",
    ".venv",
    "venv",
    "*.egg-info",
    "dist",
    "build",
]


@dataclass
class WatcherConfig:
    """Configuration for the file watcher."""

    watch_paths: list
    debounce_ms: int = 500
    recursive: bool = True
    ignore_patterns: list = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    db_path: Optional[str] = None
    use_gitignore: bool = True
    use_tokenpakignore: bool = True


@dataclass
class WatcherStats:
    events_received: int = 0
    reindexes_triggered: int = 0
    files_reindexed: int = 0
    started_at: float = field(default_factory=time.time)

    def uptime_seconds(self) -> float:
        return time.time() - self.started_at


class VaultWatcher:
    """Watch directories and trigger re-indexing on file changes.

    Features:
    - watchdog-based filesystem events (inotify/FSEvents)
    - Debounced re-indexing: coalesces rapid bursts into one reindex
    - Pattern filtering (ignore __pycache__, .git, etc.)
    - Status / stats reporting
    - Graceful Ctrl+C handling when blocking=True
    """

    def __init__(self, config: WatcherConfig, on_change: Optional[Callable[[str], None]] = None):
        self.config = config
        self.on_change = on_change
        self._running = False
        self._stats = WatcherStats()
        self._pending: Dict[str, float] = {}
        self._debounce_lock = threading.Lock()
        self._debounce_thread: Optional[threading.Thread] = None
        self._observer = None
        self._gitignore_patterns: List[str] = []
        self._tokenpakignore_patterns: List[str] = []
        self._load_ignore_files()

    def start(self, blocking: bool = False) -> None:
        """Start watching. If blocking=True, run until Ctrl+C."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            raise RuntimeError("watchdog is required: pip install watchdog")

        self._stats = WatcherStats()
        self._running = True
        watcher_ref = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                src = getattr(event, "src_path", None)
                if src and not watcher_ref._should_ignore(src):
                    watcher_ref._on_fs_event(src)

        self._observer: BaseObserver = Observer()  # type: ignore
        for raw_path in self.config.watch_paths:
            watch_dir = str(Path(raw_path).expanduser().resolve())
            self._observer.schedule(_Handler(), watch_dir, recursive=self.config.recursive)  # type: ignore
            logger.info("Watching: %s", watch_dir)

        self._observer.start()  # type: ignore

        self._debounce_thread = threading.Thread(
            target=self._debounce_loop, daemon=True, name="tp-watcher-debounce"
        )
        self._debounce_thread.start()

        if blocking:
            self._run_blocking()

    def stop(self) -> None:
        """Stop watching gracefully."""
        self._running = False
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> dict:
        """Return a status/stats dict."""
        s = self._stats
        watched = [str(Path(p).expanduser().resolve()) for p in self.config.watch_paths]
        return {
            "running": self._running,
            "watched_paths": watched,
            "debounce_ms": self.config.debounce_ms,
            "uptime_seconds": round(s.uptime_seconds(), 1),
            "events_received": s.events_received,
            "reindexes_triggered": s.reindexes_triggered,
            "files_reindexed": s.files_reindexed,
        }

    def _load_patterns_from_file(self, filepath: str) -> List[str]:
        """Read patterns from a .gitignore-style file, skipping comments/blanks."""
        patterns = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.rstrip("\n")
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    # Negation patterns (!) are not yet supported — skip
                    if stripped.startswith("!"):
                        continue
                    patterns.append(stripped)
        except OSError:
            pass
        return patterns

    def _load_ignore_files(self) -> None:
        """Scan watch paths for .gitignore and .tokenpakignore files."""
        self._gitignore_patterns = []
        self._tokenpakignore_patterns = []
        for raw_path in self.config.watch_paths:
            watch_dir = Path(raw_path).expanduser().resolve()
            if self.config.use_gitignore:
                gi_path = watch_dir / ".gitignore"
                self._gitignore_patterns.extend(self._load_patterns_from_file(str(gi_path)))
            if self.config.use_tokenpakignore:
                tpi_path = watch_dir / ".tokenpakignore"
                self._tokenpakignore_patterns.extend(self._load_patterns_from_file(str(tpi_path)))
        if self._gitignore_patterns:
            logger.info("Loaded %d pattern(s) from .gitignore", len(self._gitignore_patterns))
        if self._tokenpakignore_patterns:
            logger.info(
                "Loaded %d pattern(s) from .tokenpakignore", len(self._tokenpakignore_patterns)
            )

    def _should_ignore(self, path: str) -> bool:
        """Return True if path matches any ignore pattern (config, .gitignore, .tokenpakignore)."""
        p = Path(path)
        parts = p.parts

        all_patterns = list(self.config.ignore_patterns or [])
        all_patterns.extend(self._gitignore_patterns)
        all_patterns.extend(self._tokenpakignore_patterns)

        for pattern in all_patterns:
            # Directory pattern (trailing slash) — match against path parts
            if pattern.endswith("/"):
                dir_pattern = pattern.rstrip("/")
                for part in parts:
                    if fnmatch.fnmatch(part, dir_pattern):
                        return True
            # Pattern with slash = relative path match
            elif "/" in pattern:
                # Match against the full path string
                if fnmatch.fnmatch(str(p), f"*{pattern}") or fnmatch.fnmatch(str(p), pattern):
                    return True
            else:
                # Simple name/extension pattern — match against each path component
                for part in parts:
                    if fnmatch.fnmatch(part, pattern):
                        return True
        return False

    def _on_fs_event(self, path: str) -> None:
        self._stats.events_received += 1
        with self._debounce_lock:
            self._pending[path] = time.monotonic()

    def _debounce_loop(self) -> None:
        debounce_s = self.config.debounce_ms / 1000.0
        while self._running:
            time.sleep(max(debounce_s / 4, 0.05))
            now = time.monotonic()
            ready = []
            with self._debounce_lock:
                for path, ts in list(self._pending.items()):
                    if now - ts >= debounce_s:
                        ready.append(path)
                for path in ready:
                    del self._pending[path]
            if ready:
                self._reindex(ready)

    def _reindex(self, changed_paths: list) -> None:
        self._stats.reindexes_triggered += 1
        reindexed = 0
        try:
            import hashlib

            from tokenpak.processors import get_processor
            from tokenpak.registry import BlockRegistry
            from tokenpak.tokens import count_tokens
            from tokenpak.walker import FILE_TYPES

            db = self.config.db_path
            registry = BlockRegistry(db) if db else BlockRegistry()

            for path in changed_paths:
                p = Path(path)
                if not p.exists():
                    continue
                ext = p.suffix.lower()
                file_type = FILE_TYPES.get(ext)
                if file_type not in ("code", "text", "data"):
                    continue
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

                if not registry.has_changed(str(p), content):
                    continue

                processor = get_processor(file_type)
                if processor is None:
                    continue
                compressed = processor.process(content, str(p))

                content_hash = hashlib.sha256(content.encode()).hexdigest()
                block = {
                    "id": f"{p}#{content_hash[:8]}",
                    "path": str(p),
                    "content_hash": content_hash,
                    "file_type": file_type,
                    "raw_tokens": count_tokens(content),
                    "compressed_tokens": count_tokens(compressed),
                    "compressed_content": compressed,
                    "metadata": "{}",
                }
                registry.add_block(block)  # type: ignore
                reindexed += 1
                logger.debug("Re-indexed: %s", path)

                if self.on_change:
                    try:
                        self.on_change(path)
                    except Exception:
                        pass

        except Exception as exc:
            logger.error("Reindex error: %s", exc)
            return

        self._stats.files_reindexed += reindexed
        if reindexed:
            logger.info(
                "Re-indexed %d file(s) | total reindexes=%d files=%d",
                reindexed,
                self._stats.reindexes_triggered,
                self._stats.files_reindexed,
            )

    def _run_blocking(self) -> None:
        watched = [str(Path(p).expanduser().resolve()) for p in self.config.watch_paths]
        print(f"[tokenpak] Watching {len(watched)} path(s) — press Ctrl+C to stop")
        for p in watched:
            print(f"  → {p}")
        print(f"  debounce: {self.config.debounce_ms}ms | recursive: {self.config.recursive}")
        print()

        def _handle_signal(sig, frame):
            print("\n[tokenpak] Stopping watcher…")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        last_status = 0
        while self._running:
            time.sleep(1)
            uptime = int(self._stats.uptime_seconds())
            if uptime > 0 and uptime - last_status >= 60:
                last_status = uptime
                s = self._stats
                print(
                    f"[tokenpak] uptime={uptime}s events={s.events_received} "
                    f"reindexes={s.reindexes_triggered} files={s.files_reindexed}"
                )
