"""Macro and hook systems for event-driven automation."""

from .engine import (
    MacroDefinition,
    MacroEngine,
    MacroResult,
    MacroStep,
    StepResult,
    create_macro,
    delete_macro,
    list_user_macros,
    run_user_macro,
    show_macro,
)
from .hooks import (
    EventType,
    Trigger,
    TriggerRegistry,
    add_trigger,
    fire_event,
    get_trigger_log,
    list_triggers,
    remove_trigger,
    start_file_watcher,
    stop_file_watcher,
    test_trigger,
)
from .premade_macros import (
    PREMADE_MACROS,
    PremadeMacroRunner,
    format_macro_output,
    install_macro,
    list_macros,
    run_macro,
)
from .scheduler import (
    MacroScheduler,
    ScheduledMacro,
    cancel_schedule,
    list_scheduled,
    schedule_at,
    schedule_cron,
)
from .script_hooks import (
    HOOK_NAMES,
    fire_hook,
    fire_on_budget_alert,
    fire_on_error,
    fire_on_request,
    fire_on_response,
    hook_exists,
    install_hook,
    list_hooks,
)

__all__ = [
    # YAML macro engine
    "MacroEngine",
    "MacroDefinition",
    "MacroStep",
    "MacroResult",
    "StepResult",
    "create_macro",
    "show_macro",
    "list_user_macros",
    "delete_macro",
    "run_user_macro",
    # Trigger/event system
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
    # Scheduler
    "MacroScheduler",
    "ScheduledMacro",
    "schedule_cron",
    "schedule_at",
    "list_scheduled",
    "cancel_schedule",
    # Script hooks
    "hook_exists",
    "list_hooks",
    "install_hook",
    "fire_hook",
    "fire_on_request",
    "fire_on_response",
    "fire_on_error",
    "fire_on_budget_alert",
    "HOOK_NAMES",
    # Premade macros
    "PremadeMacroRunner",
    "install_macro",
    "run_macro",
    "list_macros",
    "format_macro_output",
    "PREMADE_MACROS",
]
