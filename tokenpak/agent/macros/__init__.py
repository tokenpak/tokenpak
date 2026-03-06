"""Macro and hook systems for event-driven automation."""

from .engine import (
    MacroEngine,
    MacroDefinition,
    MacroStep,
    MacroResult,
    StepResult,
    create_macro,
    show_macro,
    list_user_macros,
    delete_macro,
    run_user_macro,
)
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
from .scheduler import (
    MacroScheduler,
    ScheduledMacro,
    schedule_cron,
    schedule_at,
    list_scheduled,
    cancel_schedule,
)
from .script_hooks import (
    hook_exists,
    list_hooks,
    install_hook,
    fire_hook,
    fire_on_request,
    fire_on_response,
    fire_on_error,
    fire_on_budget_alert,
    HOOK_NAMES,
)
from .premade_macros import (
    PremadeMacroRunner,
    install_macro,
    run_macro,
    list_macros,
    format_macro_output,
    PREMADE_MACROS,
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
