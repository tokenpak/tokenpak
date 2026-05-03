# SPDX-License-Identifier: Apache-2.0
"""Append-only benchmark trend store.

Per Standard 24 §7, every metric value produced by a benchmark run is
appended as a single JSON record to `tests/benchmarks/history.jsonl`.

The file is forward-only: records are never modified or deleted. To
produce baselines for prior tokenpak versions, check out the older tag,
run the *current* bench suite, and append the resulting records — do
NOT extract numbers from observational telemetry.
"""
from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .manifest import BENCH_ROOT

HISTORY_PATH = BENCH_ROOT / "history.jsonl"


@dataclass(frozen=True)
class Record:
    ts: str
    tokenpak_version: str
    tokenpak_commit: str
    suite_version: str
    host: str
    tier: str
    metric_id: str
    metric_name: str
    fixture: str | None
    value: float
    unit: str
    run_id: str
    duration_ms: float
    extra: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_short_sha(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short=10", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2.0,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _tokenpak_version() -> str:
    try:
        from tokenpak import __version__
        return __version__
    except Exception:
        return "unknown"


def make_run_context(suite_version: str, tier: str) -> dict[str, str]:
    """Build the immutable per-run context shared across all records of one run."""
    return {
        "tokenpak_version": _tokenpak_version(),
        "tokenpak_commit": _git_short_sha(BENCH_ROOT.parent.parent),
        "suite_version": suite_version,
        "host": socket.gethostname(),
        "tier": tier,
        "run_id": uuid.uuid4().hex,
    }


def append_record(
    ctx: dict[str, str],
    *,
    metric_id: str,
    metric_name: str,
    fixture: str | None,
    value: float,
    unit: str,
    duration_ms: float,
    extra: dict[str, Any] | None = None,
) -> Record:
    """Append a single record to history.jsonl. File is created if missing."""
    rec = Record(
        ts=_now_iso(),
        tokenpak_version=ctx["tokenpak_version"],
        tokenpak_commit=ctx["tokenpak_commit"],
        suite_version=ctx["suite_version"],
        host=ctx["host"],
        tier=ctx["tier"],
        metric_id=metric_id,
        metric_name=metric_name,
        fixture=fixture,
        value=value,
        unit=unit,
        run_id=ctx["run_id"],
        duration_ms=duration_ms,
        extra=extra or {},
    )
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(asdict(rec), separators=(",", ":")) + "\n")
    return rec


def load_records() -> list[dict[str, Any]]:
    """Read every record. Stable across appends; called by `compare`."""
    if not HISTORY_PATH.exists():
        return []
    out = []
    for line in HISTORY_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out
