"""
Event-driven trigger system for TokenPak.

Supports event types:
- file:changed — file system changes (via watchdog)
- git:push — git push events
- cost:threshold — cost threshold exceeded
- agent:finished — agent task completion

Zero-token activation: triggers fire only when events occur.
"""

import fnmatch
import json
import subprocess
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default paths
DEFAULT_TRIGGERS_PATH = Path.home() / ".tokenpak" / "triggers.json"
DEFAULT_LOG_PATH = Path.home() / ".tokenpak" / "trigger_log.json"
MAX_LOG_ENTRIES = 1000


class EventType(str, Enum):
    """Supported event types."""

    FILE_CHANGED = "file:changed"
    GIT_PUSH = "git:push"
    COST_THRESHOLD = "cost:threshold"
    AGENT_FINISHED = "agent:finished"

    @classmethod
    def from_string(cls, value: str) -> "EventType":
        """Parse event type from string."""
        normalized = value.lower().replace("_", ":").replace("-", ":")
        for et in cls:
            if et.value == normalized:
                return et
        raise ValueError(f"Unknown event type: {value}. Valid types: {[e.value for e in cls]}")


@dataclass
class Trigger:
    """A trigger that maps an event pattern to an action."""

    id: str
    event_type: str
    pattern: str  # glob pattern for matching
    action: str  # CLI command to execute
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    description: str = ""

    def matches(self, event_type: str, event_data: str) -> bool:
        """Check if this trigger matches the given event."""
        if not self.enabled:
            return False
        if self.event_type != event_type:
            return False
        # Pattern matching: glob-style for file paths, exact for others
        if self.event_type == EventType.FILE_CHANGED.value:
            return fnmatch.fnmatch(event_data, self.pattern)
        else:
            # For other events, pattern can be "*" for any or exact match
            return self.pattern == "*" or self.pattern == event_data

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trigger":
        return cls(**data)


@dataclass
class TriggerLogEntry:
    """Log entry for a trigger activation."""

    id: str
    trigger_id: str
    event_type: str
    event_data: str
    action: str
    timestamp: str
    success: bool
    output: str = ""
    error: str = ""
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TriggerRegistry:
    """
    Registry for event triggers.

    Stores triggers in JSON format for persistence.
    Provides methods to add, remove, list, test, and fire triggers.
    """

    def __init__(self, triggers_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self.triggers_path = triggers_path or DEFAULT_TRIGGERS_PATH
        self.log_path = log_path or DEFAULT_LOG_PATH
        self._triggers: Dict[str, Trigger] = {}
        self._log: List[TriggerLogEntry] = []
        self._watchers: Dict[str, Any] = {}  # file watcher instances
        self._load()

    def _ensure_dir(self, path: Path) -> None:
        """Ensure parent directory exists."""
        path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        """Load triggers and log from disk."""
        # Load triggers
        if self.triggers_path.exists():
            try:
                data = json.loads(self.triggers_path.read_text())
                self._triggers = {
                    tid: Trigger.from_dict(tdata) for tid, tdata in data.get("triggers", {}).items()
                }
            except (json.JSONDecodeError, KeyError):
                self._triggers = {}

        # Load log
        if self.log_path.exists():
            try:
                data = json.loads(self.log_path.read_text())
                self._log = [TriggerLogEntry(**entry) for entry in data.get("entries", [])]
            except (json.JSONDecodeError, KeyError):
                self._log = []

    def _save_triggers(self) -> None:
        """Persist triggers to disk."""
        self._ensure_dir(self.triggers_path)
        data = {
            "triggers": {tid: t.to_dict() for tid, t in self._triggers.items()},
            "version": 1,
            "updated_at": datetime.now().isoformat(),
        }
        self.triggers_path.write_text(json.dumps(data, indent=2))

    def _save_log(self) -> None:
        """Persist log to disk."""
        self._ensure_dir(self.log_path)
        # Trim log if too large
        if len(self._log) > MAX_LOG_ENTRIES:
            self._log = self._log[-MAX_LOG_ENTRIES:]
        data = {"entries": [entry.to_dict() for entry in self._log], "version": 1}
        self.log_path.write_text(json.dumps(data, indent=2))

    def add(self, event_type: str, pattern: str, action: str, description: str = "") -> Trigger:
        """
        Register a new trigger.

        Args:
            event_type: Event type (e.g., "file:changed")
            pattern: Glob pattern for matching events
            action: CLI command to execute when triggered
            description: Optional description

        Returns:
            The created Trigger
        """
        # Validate event type
        EventType.from_string(event_type)

        trigger_id = str(uuid.uuid4())[:8]
        trigger = Trigger(
            id=trigger_id,
            event_type=event_type,
            pattern=pattern,
            action=action,
            description=description,
        )
        self._triggers[trigger_id] = trigger
        self._save_triggers()
        return trigger

    def remove(self, trigger_id: str) -> bool:
        """
        Remove a trigger by ID.

        Returns:
            True if removed, False if not found
        """
        if trigger_id in self._triggers:
            del self._triggers[trigger_id]
            self._save_triggers()
            return True
        return False

    def list(self, event_type: Optional[str] = None) -> List[Trigger]:
        """
        List all triggers, optionally filtered by event type.
        """
        triggers = list(self._triggers.values())
        if event_type:
            triggers = [t for t in triggers if t.event_type == event_type]
        return sorted(triggers, key=lambda t: t.created_at)

    def get(self, trigger_id: str) -> Optional[Trigger]:
        """Get a trigger by ID."""
        return self._triggers.get(trigger_id)

    def test(self, event_type: str, event_data: str = "*") -> List[Dict[str, Any]]:
        """
        Dry-run: show what triggers would fire for an event.

        Args:
            event_type: Event type to test
            event_data: Event data (e.g., file path)

        Returns:
            List of triggers that would fire with their actions
        """
        results = []
        for trigger in self._triggers.values():
            if trigger.matches(event_type, event_data):
                results.append(
                    {
                        "id": trigger.id,
                        "pattern": trigger.pattern,
                        "action": trigger.action,
                        "would_fire": True,
                    }
                )
        return results

    def fire(
        self,
        event_type: str,
        event_data: str,
        dry_run: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> List[TriggerLogEntry]:
        """
        Fire all triggers matching an event.

        Args:
            event_type: Event type
            event_data: Event-specific data (e.g., file path)
            dry_run: If True, don't execute actions, just log
            env: Additional environment variables

        Returns:
            List of log entries for fired triggers
        """
        entries = []
        matched = [t for t in self._triggers.values() if t.matches(event_type, event_data)]

        for trigger in matched:
            entry = TriggerLogEntry(
                id=str(uuid.uuid4())[:8],
                trigger_id=trigger.id,
                event_type=event_type,
                event_data=event_data,
                action=trigger.action,
                timestamp=datetime.now().isoformat(),
                success=True,
                dry_run=dry_run,
            )

            if not dry_run:
                try:
                    # Substitute event data in action
                    action = trigger.action
                    action = action.replace("$EVENT_DATA", event_data)
                    action = action.replace("$EVENT_TYPE", event_type)

                    # Build environment
                    run_env = dict(**dict(import_os_env()))
                    run_env["TOKENPAK_EVENT_TYPE"] = event_type
                    run_env["TOKENPAK_EVENT_DATA"] = event_data
                    if env:
                        run_env.update(env)

                    result = subprocess.run(
                        action,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=300,  # 5 minute timeout
                        env=run_env,
                    )
                    entry.output = result.stdout
                    entry.error = result.stderr
                    entry.success = result.returncode == 0
                except subprocess.TimeoutExpired:
                    entry.success = False
                    entry.error = "Action timed out (300s limit)"
                except Exception as e:
                    entry.success = False
                    entry.error = str(e)

            entries.append(entry)
            self._log.append(entry)

        if entries:
            self._save_log()

        return entries

    def get_log(self, limit: int = 50, trigger_id: Optional[str] = None) -> List[TriggerLogEntry]:
        """
        Get recent trigger activations.

        Args:
            limit: Maximum entries to return
            trigger_id: Filter by trigger ID

        Returns:
            List of log entries (newest first)
        """
        entries = self._log
        if trigger_id:
            entries = [e for e in entries if e.trigger_id == trigger_id]
        return list(reversed(entries[-limit:]))

    def clear_log(self) -> int:
        """Clear the trigger log. Returns number of entries cleared."""
        count = len(self._log)
        self._log = []
        self._save_log()
        return count


def import_os_env():
    """Import os.environ for subprocess calls."""
    import os

    return os.environ


# Module-level registry instance
_registry: Optional[TriggerRegistry] = None


def _get_registry() -> TriggerRegistry:
    """Get or create the module-level registry."""
    global _registry
    if _registry is None:
        _registry = TriggerRegistry()
    return _registry


# Convenience functions for CLI use
def add_trigger(event_type: str, pattern: str, action: str, description: str = "") -> Trigger:
    """Add a new trigger."""
    return _get_registry().add(event_type, pattern, action, description)


def remove_trigger(trigger_id: str) -> bool:
    """Remove a trigger by ID."""
    return _get_registry().remove(trigger_id)


def list_triggers(event_type: Optional[str] = None) -> List[Trigger]:
    """List all triggers."""
    return _get_registry().list(event_type)


def test_trigger(event_type: str, event_data: str = "*") -> List[Dict[str, Any]]:
    """Test what triggers would fire for an event."""
    return _get_registry().test(event_type, event_data)


def get_trigger_log(limit: int = 50, trigger_id: Optional[str] = None) -> List[TriggerLogEntry]:
    """Get trigger activation log."""
    return _get_registry().get_log(limit, trigger_id)


def fire_event(event_type: str, event_data: str, dry_run: bool = False) -> List[TriggerLogEntry]:
    """Fire all matching triggers for an event."""
    return _get_registry().fire(event_type, event_data, dry_run)


# File watcher integration using watchdog
_file_watcher: Optional[Any] = None
_watcher_thread: Optional[threading.Thread] = None


def start_file_watcher(paths: Optional[List[str]] = None) -> bool:
    """
    Start the file watcher for file:changed events.

    Args:
        paths: List of paths to watch. If None, watches current directory.

    Returns:
        True if watcher started, False if already running or watchdog unavailable
    """
    global _file_watcher, _watcher_thread

    if _file_watcher is not None:
        return False

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return False

    class TriggerEventHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory:
                fire_event(EventType.FILE_CHANGED.value, event.src_path)

        def on_created(self, event):
            if not event.is_directory:
                fire_event(EventType.FILE_CHANGED.value, event.src_path)

    observer = Observer()
    handler = TriggerEventHandler()

    watch_paths = paths or ["."]
    for path in watch_paths:
        observer.schedule(handler, path, recursive=True)

    observer.start()
    _file_watcher = observer

    return True


def stop_file_watcher() -> bool:
    """
    Stop the file watcher.

    Returns:
        True if stopped, False if not running
    """
    global _file_watcher

    if _file_watcher is None:
        return False

    _file_watcher.stop()
    _file_watcher.join()
    _file_watcher = None

    return True


def is_file_watcher_running() -> bool:
    """Check if file watcher is running."""
    return _file_watcher is not None
