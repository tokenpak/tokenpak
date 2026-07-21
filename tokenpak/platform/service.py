# SPDX-License-Identifier: Apache-2.0
"""Cross-platform managed-service and scheduler adapters.

Wraps the Linux-only service/scheduler shellouts that product commands embed
(``systemctl --user restart``, ``journalctl``, ``crontab``, ``at``) behind
helpers that keep full Linux behavior, attempt sensible macOS behavior, and
return an honest *unsupported / degraded* result on native Windows instead of
raising a missing-binary traceback.

Results are returned as :class:`_ServiceResult` (``supported`` / ``ok`` /
``message`` / ``detail``) so callers can print actionable, platform-specific
guidance.

Nothing here is public API: ``__all__`` is empty so the module contributes no
symbols to the public-API snapshot. Consumers import this module and call
through the module qualifier (e.g. ``service.restart_proxy_service()``).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from tokenpak.platform import process

__all__: list[str] = []

DEFAULT_PROXY_SERVICE = "tokenpak-proxy.service"


@dataclass(frozen=True)
class _ServiceResult:
    """Outcome of a platform service/scheduler operation.

    supported: the operation is implemented on this platform.
    ok:        the operation ran and succeeded (always False when unsupported).
    message:   human-facing, platform-specific guidance or captured output.
    detail:    optional diagnostic context.
    """

    supported: bool
    ok: bool
    message: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Proxy managed-service operations
# ---------------------------------------------------------------------------


def restart_proxy_service(service_name: str = DEFAULT_PROXY_SERVICE) -> _ServiceResult:
    """Restart the proxy via the platform's service manager.

    Linux: ``systemctl --user restart <service>`` (full behavior).
    macOS / Windows: no managed-service integration yet — returns an honest
    degraded result pointing at ``tokenpak restart`` rather than shelling out
    to a binary that does not exist.
    """
    if process.is_linux():
        if not shutil.which("systemctl"):
            return _ServiceResult(
                supported=False,
                ok=False,
                message="systemctl not found — restart the proxy with: tokenpak restart",
                detail="linux host without systemd --user",
            )
        try:
            result = subprocess.run(
                ["systemctl", "--user", "restart", service_name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return _ServiceResult(True, True, "✓ Proxy service restarted")
            return _ServiceResult(
                supported=True,
                ok=False,
                message=f"✖ Restart failed (systemctl exit {result.returncode})",
                detail=(result.stderr or result.stdout or "").strip(),
            )
        except OSError as e:
            return _ServiceResult(True, False, f"✖ Restart failed: {e}")

    if process.is_macos():
        return _ServiceResult(
            supported=False,
            ok=False,
            message=(
                "Managed-service restart is not yet integrated on macOS. "
                "Restart the proxy with: tokenpak restart"
            ),
        )
    if process.is_windows():
        return _ServiceResult(
            supported=False,
            ok=False,
            message=(
                "Managed-service restart is not available on Windows. "
                "Restart the proxy with: tokenpak restart"
            ),
        )
    return _ServiceResult(
        supported=False,
        ok=False,
        message="Managed-service restart is unsupported on this platform. Use: tokenpak restart",
    )


def proxy_logs(service_name: str = DEFAULT_PROXY_SERVICE, n: int = 30) -> _ServiceResult:
    """Fetch recent proxy logs from the platform's logging facility.

    Linux: ``journalctl --user -u <service> -n N --no-pager`` (captured into
    ``message``). macOS / Windows: honest guidance pointing at the on-disk
    watchdog/proxy log instead of invoking journalctl.
    """
    if process.is_linux() and shutil.which("journalctl"):
        try:
            r = subprocess.run(
                ["journalctl", "--user", "-u", service_name, f"-n{n}", "--no-pager"],
                capture_output=True,
                text=True,
            )
            return _ServiceResult(True, r.returncode == 0, r.stdout or r.stderr or "")
        except OSError as e:
            return _ServiceResult(True, False, f"✖ Could not read logs: {e}")

    return _ServiceResult(
        supported=False,
        ok=False,
        message=(
            "Service logs via journalctl are not available on this platform.\n"
            "Check the proxy/watchdog log at: ~/.tokenpak/watchdog.log"
        ),
    )


def restart_remediation(service_name: str = DEFAULT_PROXY_SERVICE) -> str:
    """Return platform-specific remediation guidance for restarting the proxy.

    Used by doctor checks so remediation text is honest on every OS rather than
    always recommending ``systemctl``.
    """
    if process.is_linux():
        return f"Restart proxy via: systemctl --user restart {service_name}"
    if process.is_macos():
        return "Restart proxy via: tokenpak restart (launchd integration not yet available)"
    if process.is_windows():
        return "Restart proxy via: tokenpak restart"
    return "Restart proxy via: tokenpak restart"


# ---------------------------------------------------------------------------
# Scheduler (crontab / at) operations
# ---------------------------------------------------------------------------


def cron_supported() -> bool:
    """True iff a POSIX ``crontab`` binary is available on this host."""
    return process.is_posix() and shutil.which("crontab") is not None


def cron_read() -> Optional[List[str]]:
    """Return current crontab lines, or None if crontab is unavailable.

    None signals an unsupported/degraded host (e.g. native Windows, or a POSIX
    host without cron); callers map that to "no entries".
    """
    if not cron_supported():
        return None
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.splitlines()
        return []
    except (OSError, subprocess.SubprocessError):
        return None


def cron_write(lines: List[str]) -> bool:
    """Write ``lines`` to the user crontab. Returns False if unavailable."""
    if not cron_supported():
        return False
    try:
        content = "\n".join(lines) + "\n"
        proc = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def at_supported() -> bool:
    """True iff a POSIX ``at`` binary is available on this host."""
    return process.is_posix() and shutil.which("at") is not None


def at_submit(run_at: str, command: str) -> bool:
    """Submit a one-shot ``at`` job. Returns False if ``at`` is unavailable."""
    if not at_supported():
        return False
    try:
        proc = subprocess.run(
            ["at", run_at],
            input=command + "\n",
            text=True,
            capture_output=True,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
