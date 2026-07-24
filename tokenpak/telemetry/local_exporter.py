"""Local-file telemetry exporter — JSONL format.

Writes anonymised metrics to ``~/.tokenpak/telemetry/metrics-YYYY-MM-DD.jsonl``
when ``TOKENPAK_TELEMETRY_MODE=local`` (the default when no remote endpoint
is explicitly configured).

Features:
- Append mode, one JSON object per line.
- Daily rotation — a new file is created each UTC day.
- Automatic 30-day retention — files older than 30 days are pruned on write.
- Opt-in respected: write is a no-op when metrics are disabled.
- Never raises — telemetry must not break the proxy.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_TELEMETRY_DIR = Path(os.path.expanduser("~/.tokenpak/telemetry"))

TELEMETRY_DIR: Path = Path(os.environ.get("TOKENPAK_TELEMETRY_DIR", str(_DEFAULT_TELEMETRY_DIR)))

# "local" → write JSONL locally (default).
# "remote" → skip local write (caller relies on reporter.py for remote sync).
TELEMETRY_MODE: str = os.environ.get("TOKENPAK_TELEMETRY_MODE", "local")

RETENTION_DAYS: int = 30
FILE_PREFIX: str = "metrics-"
FILE_SUFFIX: str = ".jsonl"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _current_file(telemetry_dir: Path) -> Path:
    return telemetry_dir / f"{FILE_PREFIX}{_today_utc()}{FILE_SUFFIX}"


def _cleanup_old_files(telemetry_dir: Path, retention_days: int = RETENTION_DAYS) -> None:
    """Delete JSONL files older than *retention_days* days."""
    try:
        cutoff = datetime.now(timezone.utc).toordinal() - retention_days
        for p in telemetry_dir.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}"):
            stem = p.stem[len(FILE_PREFIX) :]  # "YYYY-MM-DD"
            try:
                file_date = datetime.strptime(stem, "%Y-%m-%d").toordinal()
            except ValueError:
                continue
            if file_date < cutoff:
                p.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("telemetry local_exporter: cleanup failed: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_local_mode() -> bool:
    """Return True when local-file export is active."""
    return TELEMETRY_MODE == "local"


def write_record(
    record_dict: dict,
    *,
    telemetry_dir: Optional[Path] = None,
    mode: Optional[str] = None,
) -> None:
    """Append *record_dict* as a JSONL line to today's metrics file.

    Skips silently when:
    - ``TOKENPAK_TELEMETRY_MODE`` is not ``"local"``
    - Metrics opt-in is disabled (``get_metrics_enabled()`` returns False)

    Args:
        record_dict: Serialisable dict (e.g. ``MetricsRecord.to_upload_dict()``).
        telemetry_dir: Override directory (default: ``TELEMETRY_DIR``).
        mode: Override mode string (default: ``TELEMETRY_MODE``).
    """
    _mode = mode if mode is not None else TELEMETRY_MODE
    if _mode != "local":
        return

    try:
        from tokenpak.core.config import get_metrics_enabled

        if not get_metrics_enabled():
            return
    except Exception:
        return

    _dir = telemetry_dir if telemetry_dir is not None else TELEMETRY_DIR

    try:
        _dir.mkdir(parents=True, exist_ok=True)
        _cleanup_old_files(_dir)
        dest = _current_file(_dir)
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record_dict, default=str) + "\n")
    except Exception as exc:
        logger.debug("telemetry local_exporter: write failed: %s", exc)
