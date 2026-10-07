# SPDX-License-Identifier: Apache-2.0
"""Detect an update that is installed or staged but not yet running.

TokenPak never switches versions underneath a live session. An update becomes
*pending* when a newer version is on disk than the one the proxy is running,
and it takes effect at the next launch or when ``tokenpak update apply`` runs
while nothing is in use.

Two sources answer "is an update pending?":

``staged``
    A staged-release marker, ``pending-release.json`` under the user state
    directory (``$XDG_STATE_HOME/tokenpak`` or ``~/.local/state/tokenpak``).
    The marker is read-only here. Only version strings are used from it, and
    only when they look like versions; every other field is ignored for
    display.

``installed``
    The distribution on disk is newer than the version the running proxy
    reports on ``/health``: the package was upgraded but the service still
    runs the old code.

Detection is local and fast: no network beyond a loopback ``/health`` read with
a short timeout, and it never raises.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

__all__ = ["PendingUpdate", "detect", "footer_marker", "marker_path", "stack_units"]

MARKER_NAME = "pending-release.json"
MARKER_SCHEMA = "tokenpak-pending-release/1"
_VERSION_RE = re.compile(r"^\d{1,4}(?:\.\d{1,5}){1,3}(?:(?:a|b|rc|\.post|\.dev)\d{1,5})?$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
_MARKER_MAX_BYTES = 64 * 1024
_FOOTER_TTL_SECONDS = 60.0

SOURCE_STAGED = "staged"
SOURCE_INSTALLED = "installed"


@dataclass(frozen=True)
class PendingUpdate:
    """Result of one detection pass."""

    pending: bool = False
    source: str = ""  # "staged" | "installed" | "" when not pending
    running: Optional[str] = None  # version that is live now (None if unknown)
    target: Optional[str] = None  # version that will be live after apply

    def summary(self) -> str:
        """One concise line: ``1.30.2 → 1.30.3, <how it applies>``."""
        if not self.pending:
            return ""
        arrow = f"{self.running} → {self.target}" if self.running else f"→ {self.target}"
        if self.source == SOURCE_STAGED:
            return f"{arrow}, applies at next launch"
        return f"{arrow}, restart to load it"


def marker_path() -> Path:
    """Where the staged-release marker lives (it may not exist)."""
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".local" / "state"
    return root / "tokenpak" / MARKER_NAME


def _parse(text: Any) -> Any:
    from packaging.version import InvalidVersion, Version

    if not isinstance(text, str) or not _VERSION_RE.match(text):
        return None
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _read_marker() -> Optional[dict[str, Any]]:
    """Return the marker dict, or None when absent, unsafe or malformed."""
    path = marker_path()
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MARKER_MAX_BYTES:
            return None
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != MARKER_SCHEMA:
        return None
    return data


def _staged_version(marker: dict[str, Any]) -> Optional[str]:
    release = marker.get("release")
    value = release.get("oss") if isinstance(release, dict) else None
    return value if _parse(value) is not None else None


def stack_units() -> list[str]:
    """Service units named by the staged marker, filtered to safe names."""
    marker = _read_marker()
    units = marker.get("stack_units") if marker else None
    if not isinstance(units, list) or not 0 < len(units) <= 8:
        return []
    if not all(isinstance(u, str) and _UNIT_RE.match(u) for u in units):
        return []
    return list(units)


def _loaded_version() -> Optional[str]:
    try:
        from tokenpak import __version__

        return __version__ if _parse(__version__) is not None else None
    except Exception:
        return None


def _disk_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        value = version("tokenpak")
        return value if _parse(value) is not None else None
    except Exception:
        return None


def _health_version(timeout: float) -> Optional[str]:
    try:
        from tokenpak.core.runtime import lifecycle

        port = int(os.environ.get("TOKENPAK_PORT", "8766"))
        ok, payload = lifecycle.probe_health(port, timeout=timeout)
        value = payload.get("version") if ok else None
        return value if _parse(value) is not None else None
    except Exception:
        return None


def detect(
    *,
    probe: bool = True,
    running: Optional[str] = None,
    timeout: float = 0.5,
) -> PendingUpdate:
    """Answer "is an update pending?". Never raises.

    ``running`` is the version the proxy reports, when the caller already has
    it. With ``probe`` and no ``running``, ``/health`` is read once. When the
    proxy version cannot be learned, the staged source falls back to the version
    loaded in this process and the installed source is skipped (nothing stale is
    running).
    """
    try:
        live = running if _parse(running) is not None else None
        if live is None and probe:
            live = _health_version(timeout)
        reference = live or _loaded_version()

        marker = _read_marker()
        staged = _staged_version(marker) if marker else None
        if staged and reference and _parse(staged) > _parse(reference):
            return PendingUpdate(True, SOURCE_STAGED, reference, staged)

        disk = _disk_version()
        if live and disk and _parse(disk) > _parse(live):
            return PendingUpdate(True, SOURCE_INSTALLED, live, disk)
    except Exception:
        pass
    return PendingUpdate()


_footer_cache: tuple[float, str] = (0.0, "")


def footer_marker() -> str:
    """Short footer text when an update is pending, else an empty string.

    Cached for a minute and never touches the network, so it is safe to call on
    every render. It compares against the version loaded in this process, so it
    is accurate inside the proxy (where it catches an upgrade made on disk) and
    for a staged marker anywhere.
    """
    global _footer_cache
    now = time.monotonic()
    stamp, text = _footer_cache
    if stamp and now - stamp < _FOOTER_TTL_SECONDS:
        return text
    loaded = _loaded_version()
    found = detect(probe=False, running=loaded)
    text = f"↑ update {found.target} pending" if found.pending and found.target else ""
    _footer_cache = (now, text)
    return text
