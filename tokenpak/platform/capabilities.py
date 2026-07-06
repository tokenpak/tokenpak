# SPDX-License-Identifier: Apache-2.0
"""Cross-platform capability detection for local dashboard read models."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def _capability(state: str, *, source: str, detail: str | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": state, "source": source}
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    return payload


def _service_control_source(*, os_name: str, sys_platform: str) -> tuple[str, str]:
    if os_name == "nt":
        sc = shutil.which("sc.exe") or shutil.which("sc")
        if sc:
            return "available", "sc.exe"
        return "unsupported", "windows-service-control"
    if sys_platform == "darwin":
        launchctl = shutil.which("launchctl")
        if launchctl:
            return "available", "launchctl"
        return "unsupported", "launchctl"
    systemctl = shutil.which("systemctl")
    if systemctl:
        return "available", "systemctl"
    return "unsupported", "service-manager"


def _detect_dashboard_capabilities(
    *,
    stdout_is_tty: bool | None = None,
    os_name: str | None = None,
    sys_platform: str | None = None,
    proc_path: Path | None = None,
    monitor_db_available: bool = False,
    dispatch_state_available: bool = False,
    companion_state_available: bool = False,
    fleet_config_exists: bool = False,
) -> dict[str, Any]:
    """Return source-labelled local capabilities for dashboard read models.

    This function only detects whether read-only sources are available. It does
    not start, stop, attach, clear, or mutate any TokenPak runtime.
    """

    resolved_os_name = os.name if os_name is None else os_name
    resolved_sys_platform = sys.platform if sys_platform is None else sys_platform
    resolved_proc_path = Path("/proc") if proc_path is None else proc_path
    resolved_stdout_is_tty = sys.stdout.isatty() if stdout_is_tty is None else stdout_is_tty

    rich_state = "available" if importlib.util.find_spec("rich") is not None else "fallback"
    if resolved_stdout_is_tty:
        terminal_state = "available"
        terminal_detail = "interactive stdout"
    else:
        terminal_state = "not_interactive"
        terminal_detail = "stdout is not a TTY"

    process_available = resolved_os_name == "posix" and resolved_proc_path.exists()
    service_state, service_source = _service_control_source(
        os_name=resolved_os_name,
        sys_platform=resolved_sys_platform,
    )

    return {
        "terminal_ui": _capability(
            terminal_state,
            source="stdio",
            detail=terminal_detail,
            rich=rich_state,
        ),
        "process_inspection": _capability(
            "available" if process_available else "unsupported",
            source=str(resolved_proc_path) if process_available else "platform",
            detail="process metadata can be inspected"
            if process_available
            else "no portable process-inspection source detected",
        ),
        "service_control": _capability(
            service_state,
            source=service_source,
            detail="detected for future read-only status projection",
            read_only=True,
        ),
        "receipt_state_sources": {
            "monitor_db": _capability(
                "available" if monitor_db_available else "source_unavailable",
                source="tokenpak._paths.monitor_db",
            ),
            "dispatch_runs": _capability(
                "available" if dispatch_state_available else "not_configured",
                source="tokenpak-home/dispatch/runs.db",
            ),
            "companion_state": _capability(
                "available" if companion_state_available else "not_configured",
                source="tokenpak-home/companion/journal.db",
            ),
        },
        "fleet_projection": _capability(
            "configured" if fleet_config_exists else "not_configured",
            source="tokenpak-home/fleet.yaml",
            detail="opt-in only; local dashboard does not assume a fleet configuration by default",
            default_enabled=False,
        ),
    }
