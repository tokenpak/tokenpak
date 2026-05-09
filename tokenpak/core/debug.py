"""infrastructure.debug — DebugLogger (JSONL per-request) + DebugState (on/off toggle).

Consolidated from agent/debug/logger.py and agent/debug/state.py.
"""


import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

_DEFAULT_LOG = Path.home() / ".tokenpak" / "debug.log"


class _DebugRecord:
    """Mutable record accumulated during a single request."""

    def __init__(self) -> None:
        self.fields: Dict[str, Any] = {}
        self.pipeline_steps: list = []
        self._start = time.monotonic()
        self.error: Optional[str] = None

    def set(self, key: str, value: Any) -> None:
        self.fields[key] = value

    def add_step(self, name: str, **kw: Any) -> None:
        self.pipeline_steps.append({"step": name, **kw})

    def fail(self, msg: str) -> None:
        self.error = msg

    def to_dict(self) -> dict:
        elapsed = round((time.monotonic() - self._start) * 1000, 2)
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_ms": elapsed,
            "fields": self.fields,
            "pipeline_steps": self.pipeline_steps,
            "error": self.error,
        }


class DebugLogger:
    """Write JSONL debug records for each request when debug mode is active."""

    def __init__(self, log_path: Optional[Path] = None) -> None:
        self._path = Path(log_path) if log_path else _DEFAULT_LOG
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def record(self) -> Iterator[_DebugRecord]:
        """Context manager: yields a _DebugRecord; appends to log on exit."""
        rec = _DebugRecord()
        try:
            yield rec
        except Exception as exc:
            rec.fail(str(exc))
            raise
        finally:
            with open(self._path, "a") as fh:
                fh.write(json.dumps(rec.to_dict()) + "\n")



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
