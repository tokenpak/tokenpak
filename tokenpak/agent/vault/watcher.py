"""TokenPak Agent Vault File Watcher — Phase 1 stub.

Watch a directory for file changes and trigger re-indexing automatically.
Used by `tokenpak index --watch`.

THIS IS A STUB. Full implementation arrives in Phase 1 (task 1.5).
The interface is defined here so Phase 0 modules can import it safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class WatcherConfig:
    """Configuration for the file watcher."""
    watch_paths: list[str]
    debounce_ms: int = 500          # Wait N ms after last change before re-indexing
    recursive: bool = True
    ignore_patterns: list[str] = None  # Glob patterns to ignore


class VaultWatcher:
    """STUB: Watch directories and trigger re-indexing on file changes.

    Phase 1 will implement:
    - watchdog-based filesystem events
    - Debounced re-indexing
    - Pattern filtering
    - Status/stats reporting
    - `tokenpak index --watch` CLI integration
    """

    def __init__(self, config: WatcherConfig, on_change: Optional[Callable[[str], None]] = None):
        self.config = config
        self.on_change = on_change
        self._running = False

    def start(self, blocking: bool = False) -> None:
        """Start watching. No-op in stub."""
        self._running = True

    def stop(self) -> None:
        """Stop watching. No-op in stub."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running
