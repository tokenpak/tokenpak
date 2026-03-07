"""DebugState — persistent on/off toggle with optional request countdown."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = Path.home() / ".tokenpak" / "debug.json"


class DebugState:
    """Manage debug mode state persisted to disk.

    Schema:
        {
            "enabled": bool,
            "requests_remaining": int | null   # null = unlimited
        }
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"enabled": False, "requests_remaining": None}

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enable(self, requests: Optional[int] = None) -> None:
        """Enable debug mode. If *requests* is given, auto-disable after N requests."""
        self._save({"enabled": True, "requests_remaining": requests})

    def disable(self) -> None:
        """Disable debug mode."""
        self._save({"enabled": False, "requests_remaining": None})

    def is_enabled(self) -> bool:
        return bool(self._load().get("enabled", False))

    def requests_remaining(self) -> Optional[int]:
        """Return remaining request count, or None if unlimited."""
        data = self._load()
        if not data.get("enabled"):
            return None
        return data.get("requests_remaining")

    def decrement(self) -> None:
        """Decrement the request counter; auto-disable when it hits zero."""
        data = self._load()
        if not data.get("enabled"):
            return
        remaining = data.get("requests_remaining")
        if remaining is None:
            return  # unlimited — nothing to decrement
        remaining -= 1
        if remaining <= 0:
            data["enabled"] = False
            data["requests_remaining"] = None
        else:
            data["requests_remaining"] = remaining
        self._save(data)

    def status(self) -> dict:
        """Return a dict suitable for display."""
        data = self._load()
        log_path = Path.home() / ".tokenpak" / "debug.log"
        log_size = log_path.stat().st_size if log_path.exists() else 0
        remaining = data.get("requests_remaining")
        return {
            "enabled": bool(data.get("enabled", False)),
            "requests_remaining": remaining,
            "log_path": str(log_path),
            "log_size_bytes": log_size,
        }
