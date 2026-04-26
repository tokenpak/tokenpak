# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4.2 — read-side aggregations over ``intent_suggestions``.

Dual to :mod:`intent_policy_report` (Phase 2.2) which aggregates
the policy decisions table. This module aggregates the Phase 2.4.1
``intent_suggestions`` table:

  - Total advisory suggestions in window
  - Suggestion-type distribution
  - Recommended-action distribution
  - Safety-flag distribution
  - Expired suggestion count
  - user_visible true / false counts
  - Latest N suggestions (full rows)

Read-only. The privacy contract from 2.4.1 carries through —
suggestion rows store only structured fields + templated text;
no raw prompts, no per-row hashes. Asserted in
``tests/test_intent_suggestion_phase24_2.py::TestPrivacyContract``.

Window semantics mirror :mod:`intent_policy_report` exactly:
``Nd`` form, default 14d on dashboard surfaces, ``0d``/``""`` =
all rows on the CLI surface, cutoff inclusive
(``timestamp >= cutoff``).
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Standardized advisory label rendered on every surface.
ADVISORY_LABEL: str = (
    "DRY-RUN / PREVIEW ONLY — suggestions are advisory; "
    "TokenPak has not changed routing."
)

# Tag rendered on every section header so the operator scanning
# the output cannot miss it.
NOOP_DEFAULT_OFF_TAG: str = "no-op / default-off"


@dataclass
class SuggestionReport:
    """All aggregated metrics for one window. JSON-serialisable."""

    window_days: Optional[int]
    window_cutoff_iso: Optional[str]
    db_path: str

    total_suggestions: int = 0
    suggestion_type_distribution: Dict[str, int] = field(default_factory=dict)
    recommended_action_distribution: Dict[str, int] = field(default_factory=dict)
    safety_flag_distribution: Dict[str, int] = field(default_factory=dict)
    user_visible_true_count: int = 0
    user_visible_false_count: int = 0
    expired_count: int = 0
    latest_suggestions: List[Dict[str, Any]] = field(default_factory=list)
    review_areas: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_days": self.window_days,
            "window_cutoff_iso": self.window_cutoff_iso,
            "db_path": self.db_path,
            "total_suggestions": self.total_suggestions,
            "suggestion_type_distribution": self.suggestion_type_distribution,
            "recommended_action_distribution": self.recommended_action_distribution,
            "safety_flag_distribution": self.safety_flag_distribution,
            "user_visible_true_count": self.user_visible_true_count,
            "user_visible_false_count": self.user_visible_false_count,
            "expired_count": self.expired_count,
            "latest_suggestions": list(self.latest_suggestions),
            "review_areas": list(self.review_areas),
        }


def _intent_db_path() -> Path:
    from tokenpak.proxy.intent_contract import _DEFAULT_DB_PATH

    return _DEFAULT_DB_PATH


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='intent_suggestions'"
    ).fetchone()
    return row is not None


def _window_cutoff(window_days: Optional[int], now: Optional[_dt.datetime]) -> Optional[str]:
    if window_days is None:
        return None
    base = now if now is not None else _dt.datetime.now()
    return (base - _dt.timedelta(days=window_days)).isoformat(timespec="seconds")


def build_suggestion_report(
    *,
    window_days: Optional[int] = 14,
    db_path: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    latest_limit: int = 10,
) -> SuggestionReport:
    """Run every aggregation and return one fully-populated report.

    Read-only. ``window_days=None`` reads every row.
    """
    path = db_path if db_path is not None else _intent_db_path()
    cutoff = _window_cutoff(window_days, now)
    report = SuggestionReport(
        window_days=window_days,
        window_cutoff_iso=cutoff,
        db_path=str(path),
    )

    if not path.is_file():
        report.review_areas = _review_areas(report)
        return report

    where = ""
    params: tuple = ()
    if cutoff is not None:
        where = " WHERE timestamp >= ?"
        params = (cutoff,)

    with sqlite3.connect(str(path)) as conn:
        if not _table_exists(conn):
            report.review_areas = _review_areas(report)
            return report

        # Total
        row = conn.execute(
            f"SELECT COUNT(*) FROM intent_suggestions{where}", params
        ).fetchone()
        report.total_suggestions = int(row[0]) if row else 0
        if report.total_suggestions == 0:
            report.review_areas = _review_areas(report)
            return report

        # suggestion_type distribution
        for r in conn.execute(
            f"SELECT suggestion_type, COUNT(*) FROM intent_suggestions"
            f"{where} GROUP BY suggestion_type ORDER BY COUNT(*) DESC",
            params,
        ).fetchall():
            report.suggestion_type_distribution[r[0]] = int(r[1])

        # recommended_action distribution (skip nulls; spec §6
        # treats recommended_action as nullable for budget_warning /
        # adapter_capability types)
        and_ = " AND" if where else " WHERE"
        for r in conn.execute(
            f"SELECT recommended_action, COUNT(*) FROM intent_suggestions"
            f"{where}{and_} recommended_action IS NOT NULL "
            f"GROUP BY recommended_action ORDER BY COUNT(*) DESC",
            params,
        ).fetchall():
            report.recommended_action_distribution[r[0]] = int(r[1])

        # user_visible true/false split
        row = conn.execute(
            f"SELECT "
            f"SUM(CASE WHEN user_visible=1 THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN user_visible=0 THEN 1 ELSE 0 END) "
            f"FROM intent_suggestions{where}",
            params,
        ).fetchone()
        report.user_visible_true_count = int(row[0]) if row and row[0] is not None else 0
        report.user_visible_false_count = int(row[1]) if row and row[1] is not None else 0

        # Expired count: expires_at is non-null AND in the past.
        # Pass the comparison time as ISO-8601 so SQL string
        # comparison is correct.
        cmp_iso = (now or _dt.datetime.now()).isoformat(timespec="seconds")
        row = conn.execute(
            f"SELECT COUNT(*) FROM intent_suggestions"
            f"{where}{and_} expires_at IS NOT NULL AND expires_at < ?",
            (*params, cmp_iso),
        ).fetchone()
        report.expired_count = int(row[0]) if row else 0

        # safety_flag distribution: JSON column; explode in Python.
        for r in conn.execute(
            f"SELECT safety_flags FROM intent_suggestions"
            f"{where}{and_} safety_flags IS NOT NULL",
            params,
        ).fetchall():
            try:
                for f in json.loads(r[0] or "[]"):
                    report.safety_flag_distribution[f] = (
                        report.safety_flag_distribution.get(f, 0) + 1
                    )
            except (TypeError, json.JSONDecodeError):
                continue

        # Latest N rows (full-row rendering on dashboard / API)
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
            f"SELECT suggestion_id, decision_id, contract_id, timestamp, "
            f"suggestion_type, title, message, recommended_action, "
            f"confidence, safety_flags, requires_confirmation, "
            f"user_visible, expires_at, source "
            f"FROM intent_suggestions{where} "
            f"ORDER BY timestamp DESC LIMIT ?",
            (*params, latest_limit),
        ).fetchall():
            try:
                flags = json.loads(r["safety_flags"] or "[]")
            except (TypeError, json.JSONDecodeError):
                flags = []
            report.latest_suggestions.append({
                "suggestion_id": r["suggestion_id"],
                "decision_id": r["decision_id"],
                "contract_id": r["contract_id"],
                "timestamp": r["timestamp"],
                "suggestion_type": r["suggestion_type"],
                "title": r["title"],
                "message": r["message"],
                "recommended_action": r["recommended_action"],
                "confidence": r["confidence"],
                "safety_flags": flags,
                "requires_confirmation": bool(r["requires_confirmation"]),
                "user_visible": bool(r["user_visible"]),
                "expires_at": r["expires_at"],
                "source": r["source"],
            })

    report.review_areas = _review_areas(report)
    return report


def _review_areas(report: SuggestionReport) -> List[str]:
    out: List[str] = []
    if report.total_suggestions == 0:
        out.append(
            "No advisory suggestions yet — start the proxy and route some "
            "traffic through it before re-running this report."
        )
        return out

    if report.user_visible_false_count == report.total_suggestions:
        out.append(
            "Every suggestion is user_visible=false (the Phase 2.4.2 default). "
            "Phase 2.4.3 will add the per-surface visibility config that flips "
            "this to true under explicit opt-in."
        )

    if report.expired_count and report.expired_count > 0:
        out.append(
            f"{report.expired_count} suggestion(s) are expired (expires_at < now). "
            "Inspect via `tokenpak intent suggestions` and consider regenerating."
        )

    return out


def render_human(report: SuggestionReport) -> str:
    """Operator-readable plain-text suggestion section.

    Used as a sub-section appended to ``tokenpak intent report``
    output; can also be rendered standalone.
    """
    lines: List[str] = []
    lines.append("")
    lines.append(
        "  ── Advisory Suggestions ("
        f"Phase 2.4.2 — {NOOP_DEFAULT_OFF_TAG}) ──"
    )
    lines.append("")
    if report.total_suggestions == 0:
        lines.append("  No advisory suggestions in this window.")
        lines.append("")
        if report.review_areas:
            for ra in report.review_areas:
                lines.append(f"  - {ra}")
            lines.append("")
        return "\n".join(lines)

    lines.append(f"  Total suggestions:         {report.total_suggestions}")
    lines.append(
        f"  user_visible (true / false): "
        f"{report.user_visible_true_count} / {report.user_visible_false_count}"
    )
    lines.append(f"  Expired suggestions:       {report.expired_count}")
    lines.append("")

    lines.append("  Suggestion type distribution:")
    for kind, count in report.suggestion_type_distribution.items():
        pct = round(100 * count / report.total_suggestions)
        lines.append(f"    {kind:<40s} {count:>6d}  ({pct}%)")
    lines.append("")

    if report.recommended_action_distribution:
        lines.append("  Top recommended actions:")
        for action, count in list(report.recommended_action_distribution.items())[:5]:
            lines.append(f"    {action[:50]:<52s} {count}")
        lines.append("")

    if report.safety_flag_distribution:
        lines.append("  Safety flags attached to suggestions:")
        for flag, count in sorted(
            report.safety_flag_distribution.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"    {flag:<32s} {count}")
        lines.append("")

    if report.review_areas:
        lines.append("  Recommended review areas:")
        for ra in report.review_areas:
            lines.append(f"    - {ra}")
        lines.append("")

    lines.append(f"  {ADVISORY_LABEL}")
    lines.append("")
    return "\n".join(lines)


def render_json(report: SuggestionReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


__all__ = [
    "ADVISORY_LABEL",
    "NOOP_DEFAULT_OFF_TAG",
    "SuggestionReport",
    "build_suggestion_report",
    "render_human",
    "render_json",
]
