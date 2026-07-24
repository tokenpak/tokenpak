# SPDX-License-Identifier: Apache-2.0
"""Episode Distiller — structured distillation of agent work episodes.

After each agent work episode (task attempt), automatically distills:
  - what was tried (tools_used)
  - what failed (errors_encountered)
  - what fix was applied (fix_applied)
  - how it ended (outcome)
  - resource consumption (duration, tokens_spent)

Produces structured EpisodeRecord dataclasses that:
  - serialize to JSON and SQLite without external dependencies
  - feed into memory_promoter.py for durable memory candidacy scoring

Public API:
  distill_episode(raw)              → EpisodeRecord
  submit_to_memory(record, ...)     → Lesson | None
  SQLITE_SCHEMA                     (CREATE TABLE DDL for routing stores)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_OUTCOMES = ("success", "failure", "partial")

# SQLite schema for episode records (can be imported by callers)
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS episode_records (
    task_id            TEXT    NOT NULL,
    tools_used         TEXT    NOT NULL,  -- JSON array
    errors_encountered TEXT    NOT NULL,  -- JSON array
    fix_applied        TEXT,
    outcome            TEXT    NOT NULL,
    duration           REAL    NOT NULL,
    tokens_spent       INTEGER NOT NULL,
    timestamp          TEXT    NOT NULL,
    PRIMARY KEY (task_id, timestamp)
);
"""


# ---------------------------------------------------------------------------
# EpisodeRecord dataclass
# ---------------------------------------------------------------------------


@dataclass
class EpisodeRecord:
    """Structured record of a single agent work episode."""

    task_id: str
    tools_used: List[str]
    errors_encountered: List[str]
    fix_applied: Optional[str]
    outcome: str  # "success" | "failure" | "partial"
    duration: float  # seconds elapsed
    tokens_spent: int  # total tokens consumed in this episode
    timestamp: str  # ISO 8601 UTC

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Return the record as a JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def to_sqlite_row(
        self,
    ) -> tuple[str, str, str, Optional[str], str, float, int, str]:
        """Return a tuple suitable for SQLite INSERT (matches SQLITE_SCHEMA)."""
        return (
            self.task_id,
            json.dumps(self.tools_used),
            json.dumps(self.errors_encountered),
            self.fix_applied,
            self.outcome,
            self.duration,
            self.tokens_spent,
            self.timestamp,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EpisodeRecord":
        """Reconstruct from a dict (e.g. JSON-parsed)."""
        return cls(
            task_id=d["task_id"],
            tools_used=list(d.get("tools_used", [])),
            errors_encountered=list(d.get("errors_encountered", [])),
            fix_applied=d.get("fix_applied"),
            outcome=d["outcome"],
            duration=float(d.get("duration", 0.0)),
            tokens_spent=int(d.get("tokens_spent", 0)),
            timestamp=d.get("timestamp", ""),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def outcome_score(self) -> float:
        """Return numeric outcome score for QPT calculations.

        success → 1.0, partial → 0.5, failure → 0.0
        """
        return {"success": 1.0, "partial": 0.5, "failure": 0.0}.get(self.outcome, 0.0)

    def has_errors(self) -> bool:
        return len(self.errors_encountered) > 0

    def tool_count(self) -> int:
        return len(self.tools_used)


# ---------------------------------------------------------------------------
# distill_episode()
# ---------------------------------------------------------------------------


def distill_episode(raw: dict[str, Any]) -> EpisodeRecord:
    """Distill a raw episode dict into a structured EpisodeRecord.

    Accepts a flexible input dict — fields may arrive under several common
    aliases (e.g. ``tokens_used`` / ``tokens_spent``) and all are optional
    except ``task_id`` and ``outcome``.  Missing or malformed fields fall
    back to safe defaults rather than raising.

    Args:
        raw: Dict containing raw episode telemetry.  Expected keys (all
             optional unless noted):

             task_id (required) : str — task or request identifier
             outcome (required) : str — "success", "failure", or "partial";
                                  also accepts bool True/False or int 1/0
             tools_used         : list[str] — tool names invoked
             errors             : list[str|dict] — errors observed (alias:
                                  errors_encountered)
             fix_applied        : str — description of the fix that resolved
                                  the last error (if any)
             duration           : float — seconds elapsed (alias:
                                  duration_seconds)
             tokens_spent       : int — tokens consumed (alias: tokens_used)
             timestamp          : str — ISO 8601; defaults to now(UTC)

    Returns:
        EpisodeRecord with all fields populated.
    """
    # -- task_id -----------------------------------------------------------
    task_id = str(raw.get("task_id", "unknown"))

    # -- outcome -----------------------------------------------------------
    raw_outcome = raw.get("outcome", raw.get("accepted"))
    if raw_outcome is True or raw_outcome == 1:
        outcome = "success"
    elif raw_outcome is False or raw_outcome == 0:
        outcome = "failure"
    else:
        outcome_str = str(raw_outcome).lower() if raw_outcome is not None else "failure"
        outcome = outcome_str if outcome_str in VALID_OUTCOMES else "failure"

    # -- tools_used --------------------------------------------------------
    tools_raw = raw.get("tools_used", raw.get("tools", []))
    if isinstance(tools_raw, (list, tuple)):
        tools_used = [str(t) for t in tools_raw]
    else:
        tools_used = []

    # -- errors_encountered ------------------------------------------------
    errors_raw = raw.get("errors_encountered", raw.get("errors", []))
    if isinstance(errors_raw, (list, tuple)):
        errors_encountered = []
        for e in errors_raw:
            if isinstance(e, dict):
                errors_encountered.append(e.get("message", str(e)))
            else:
                errors_encountered.append(str(e))
    else:
        errors_encountered = []

    # -- fix_applied -------------------------------------------------------
    fix_raw = raw.get("fix_applied", raw.get("fix", raw.get("resolution")))
    fix_applied = str(fix_raw) if fix_raw is not None else None

    # -- duration ----------------------------------------------------------
    dur_raw = raw.get("duration", raw.get("duration_seconds", 0.0))
    try:
        duration = float(dur_raw)
    except (TypeError, ValueError):
        duration = 0.0

    # -- tokens_spent ------------------------------------------------------
    tok_raw = raw.get("tokens_spent", raw.get("tokens_used", raw.get("total_tokens", 0)))
    try:
        tokens_spent = int(tok_raw)
    except (TypeError, ValueError):
        tokens_spent = 0

    # -- timestamp ---------------------------------------------------------
    ts_raw = raw.get("timestamp")
    if ts_raw:
        timestamp = str(ts_raw)
    else:
        timestamp = datetime.now(timezone.utc).isoformat()

    return EpisodeRecord(
        task_id=task_id,
        tools_used=tools_used,
        errors_encountered=errors_encountered,
        fix_applied=fix_applied,
        outcome=outcome,
        duration=duration,
        tokens_spent=tokens_spent,
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# Memory candidacy scoring
# ---------------------------------------------------------------------------


def submit_to_memory(
    record: EpisodeRecord,
    lesson_content: Optional[str] = None,
    specificity_score: float = 0.5,
    savings_pct: float = 0.0,
    memory_path: Optional[str] = None,
) -> Optional[object]:
    """Submit an episode record as a memory candidacy lesson.

    Creates or updates a Lesson in the MemoryPromoter keyed on the
    episode's task_id.  Success episodes record a success observation;
    failure episodes record a failure observation.  Partial episodes
    record neither (candidacy is left unchanged).

    Args:
        record:            The distilled EpisodeRecord to score.
        lesson_content:    Human-readable description of what was learned.
                           Defaults to an auto-generated summary.
        specificity_score: How actionable the lesson is (0.0–1.0).
        savings_pct:       Estimated reduction in future work (0–100).
        memory_path:       Override path to memory_promoter.json.

    Returns:
        The Lesson dataclass after update, or None if MemoryPromoter is
        unavailable (soft dependency).
    """
    try:
        from tokenpak.orchestration.memory_promoter import (  # noqa: PLC0415
            DEFAULT_PROMOTER_PATH,
            MemoryPromoter,
        )
    except ImportError:
        return None

    path = memory_path or str(DEFAULT_PROMOTER_PATH)
    promoter = MemoryPromoter(path=path)

    lesson_id = f"episode_{record.task_id}"
    content = lesson_content or _auto_lesson(record)

    existing = promoter.get_lesson(lesson_id)
    if existing is None:
        promoter.add_lesson(
            lesson_id=lesson_id,
            content=content,
            specificity_score=specificity_score,
            savings_pct=savings_pct,
        )

    if record.outcome == "success":
        return promoter.record_success(lesson_id)
    elif record.outcome == "failure":
        return promoter.record_failure(lesson_id)
    # partial: no observation recorded — lesson stays as-is
    return promoter.get_lesson(lesson_id)


def _auto_lesson(record: EpisodeRecord) -> str:
    """Generate a short lesson description from an episode record."""
    tools = ", ".join(record.tools_used) if record.tools_used else "none"
    if record.outcome == "success":
        return (
            f"Task {record.task_id} succeeded using [{tools}] "
            f"in {record.duration:.1f}s / {record.tokens_spent} tokens."
        )
    if record.outcome == "failure":
        err_summary = "; ".join(record.errors_encountered[:2]) or "unknown error"
        return (
            f"Task {record.task_id} failed: {err_summary}. "
            f"Fix attempted: {record.fix_applied or 'none'}."
        )
    return f"Task {record.task_id} partially completed using [{tools}]."
