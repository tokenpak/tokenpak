"""Macro and hook systems for event-driven automation."""

from .hooks import (
    TriggerRegistry,
    Trigger,
    EventType,
    add_trigger,
    remove_trigger,
    list_triggers,
    test_trigger,
    get_trigger_log,
    fire_event,
    start_file_watcher,
    stop_file_watcher,
)

__all__ = [
    "TriggerRegistry",
    "Trigger",
    "EventType",
    "add_trigger",
    "remove_trigger",
    "list_triggers",
    "test_trigger",
    "get_trigger_log",
    "fire_event",
    "start_file_watcher",
    "stop_file_watcher",
]
