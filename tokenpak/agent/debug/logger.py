"""DebugLogger — context-manager per-request logger (JSONL output)."""

from __future__ import annotations

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
