# SPDX-License-Identifier: Apache-2.0
"""Phase 2.2 — read-side aggregations over ``intent_policy_decisions``.

Dual to :mod:`tokenpak.proxy.intent_report` (Phase 1) which
aggregates the ``intent_events`` table. This module aggregates the
Phase 2.1 ``intent_policy_decisions`` table:

  - Total dry-run decisions in window
  - Action distribution
  - Safety-flag distribution
  - Decision-reason distribution
  - Per-intent-class recommendation counts (cross-tab of
    ``intent_class`` × ``action``)
  - Low-confidence: blocked vs safe-handled counts
  - Catch-all: safe-handled count
  - ``live_verified=False`` recommendation blocks
  - Budget-risk flag count (reserved in 2.1 — included for
    forward-compatibility with 2.6)
  - Compression / cache / delivery suggestion distributions

Read-only. Reads ``raw_prompt_hash`` at most (not even that — the
policy table doesn't carry that column). The privacy contract is
asserted in
``tests/test_intent_policy_phase22.py::TestPrivacyContract``.

Window semantics mirror :mod:`intent_report` exactly: ``Nd`` form,
default 14d on the dashboard surface, ``0d``/``""`` = all rows on
the CLI surface, cutoff is inclusive (``timestamp >= cutoff``).
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PolicyReport:
    """All aggregated metrics for one window. JSON-serialisable."""

    window_days: Optional[int]
    window_cutoff_iso: Optional[str]
    db_path: str

    total_decisions: int = 0
    action_distribution: Dict[str, int] = field(default_factory=dict)
    decision_reason_distribution: Dict[str, int] = field(default_factory=dict)
    safety_flag_distribution: Dict[str, int] = field(default_factory=dict)

    # Cross-tab: intent_class -> {action -> count}.
    recommendations_by_intent_class: Dict[str, Dict[str, int]] = field(
        default_factory=dict
    )

    # Safety summaries.
    low_confidence_blocked: int = 0
    low_confidence_safe_handled: int = 0
    catch_all_safe_handled: int = 0
    unverified_provider_blocked: int = 0
    missing_slots_blocked: int = 0

    # Reserved for 2.6; counted now so the schema is forward-compatible.
    budget_risk_flags: int = 0

    # Suggestion distributions.
    compression_profile_distribution: Dict[str, int] = field(default_factory=dict)
    cache_strategy_distribution: Dict[str, int] = field(default_factory=dict)
    delivery_strategy_distribution: Dict[str, int] = field(default_factory=dict)

    review_areas: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_days": self.window_days,
            "window_cutoff_iso": self.window_cutoff_iso,
            "db_path": self.db_path,
            "total_decisions": self.total_decisions,
            "action_distribution": self.action_distribution,
            "decision_reason_distribution": self.decision_reason_distribution,
            "safety_flag_distribution": self.safety_flag_distribution,
            "recommendations_by_intent_class": self.recommendations_by_intent_class,
            "low_confidence_blocked": self.low_confidence_blocked,
            "low_confidence_safe_handled": self.low_confidence_safe_handled,
            "catch_all_safe_handled": self.catch_all_safe_handled,
            "unverified_provider_blocked": self.unverified_provider_blocked,
            "missing_slots_blocked": self.missing_slots_blocked,
            "budget_risk_flags": self.budget_risk_flags,
            "compression_profile_distribution": self.compression_profile_distribution,
            "cache_strategy_distribution": self.cache_strategy_distribution,
            "delivery_strategy_distribution": self.delivery_strategy_distribution,
            "review_areas": list(self.review_areas),
        }


def _intent_db_path() -> Path:
    from tokenpak.proxy.intent_contract import _DEFAULT_DB_PATH

    return _DEFAULT_DB_PATH


def _table_exists(conn: sqlite3.Connection, name: str = "intent_policy_decisions") -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _window_cutoff(window_days: Optional[int], now: Optional[_dt.datetime]) -> Optional[str]:
    if window_days is None:
        return None
    base = now if now is not None else _dt.datetime.now()
    return (base - _dt.timedelta(days=window_days)).isoformat(timespec="seconds")


def build_policy_report(
    *,
    window_days: Optional[int] = 14,
    db_path: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
) -> PolicyReport:
    """Run every aggregation and return one fully-populated report.

    Read-only. ``window_days=None`` reads every row.
    """
    path = db_path if db_path is not None else _intent_db_path()
    cutoff = _window_cutoff(window_days, now)
    report = PolicyReport(
        window_days=window_days,
        window_cutoff_iso=cutoff,
        db_path=str(path),
    )

    if not path.is_file():
        report.review_areas = _review_areas(report)
        return report

    where = ""
    params: Tuple[Any, ...] = ()
    if cutoff is not None:
        where = " WHERE timestamp >= ?"
        params = (cutoff,)

    with sqlite3.connect(str(path)) as conn:
        if not _table_exists(conn):
            report.review_areas = _review_areas(report)
            return report

        # Total
        row = conn.execute(
            f"SELECT COUNT(*) FROM intent_policy_decisions{where}", params
        ).fetchone()
        report.total_decisions = int(row[0]) if row else 0
        if report.total_decisions == 0:
            report.review_areas = _review_areas(report)
            return report

        # Action distribution
        for r in conn.execute(
            f"SELECT action, COUNT(*) FROM intent_policy_decisions"
            f"{where} GROUP BY action ORDER BY COUNT(*) DESC",
            params,
        ).fetchall():
            report.action_distribution[r[0]] = int(r[1])

        # Decision-reason distribution
        for r in conn.execute(
            f"SELECT decision_reason, COUNT(*) FROM intent_policy_decisions"
            f"{where} GROUP BY decision_reason ORDER BY COUNT(*) DESC",
            params,
        ).fetchall():
            report.decision_reason_distribution[r[0]] = int(r[1])

        # Per-intent-class × action cross-tab
        for r in conn.execute(
            f"SELECT intent_class, action, COUNT(*) FROM intent_policy_decisions"
            f"{where} GROUP BY intent_class, action",
            params,
        ).fetchall():
            cls = r[0]
            action = r[1]
            count = int(r[2])
            report.recommendations_by_intent_class.setdefault(cls, {})[action] = count

        # Suggestion distributions (only over non-null rows)
        for col, target in (
            ("compression_profile", report.compression_profile_distribution),
            ("cache_strategy", report.cache_strategy_distribution),
            ("delivery_strategy", report.delivery_strategy_distribution),
        ):
            and_ = " AND" if where else " WHERE"
            for r in conn.execute(
                f"SELECT {col}, COUNT(*) FROM intent_policy_decisions"
                f"{where}{and_} {col} IS NOT NULL "
                f"GROUP BY {col} ORDER BY COUNT(*) DESC",
                params,
            ).fetchall():
                target[r[0]] = int(r[1])

        # Safety-flag distribution. ``safety_flags`` is a JSON array
        # column; aggregate by exploding in Python so SQL stays
        # portable. Read only the column to keep the prompt-content
        # locality contract intact.
        and_ = " AND" if where else " WHERE"
        for r in conn.execute(
            f"SELECT safety_flags FROM intent_policy_decisions"
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

        # Safety summaries derived from decision_reason +
        # safety_flag presence. Each is a separate count to keep the
        # report explainable.
        report.low_confidence_blocked = report.decision_reason_distribution.get(
            "low_confidence_blocked_routing", 0
        )
        # low_confidence_safe_handled: rows where the row's confidence
        # column itself is below the engine's CLASSIFY_THRESHOLD
        # (Phase 0 default 0.4) AND the decision is NOT in the
        # routing-affecting blocked set. This is the symmetric
        # "we observed low confidence and the engine handled it
        # safely" counter. Use the engine's threshold for symmetry.
        try:
            from tokenpak.proxy.intent_policy_engine import PolicyEngineConfig

            threshold = PolicyEngineConfig().low_confidence_threshold
        except Exception:  # noqa: BLE001
            threshold = 0.65
        row = conn.execute(
            f"SELECT COUNT(*) FROM intent_policy_decisions"
            f"{where}{and_} intent_confidence < ? "
            f"AND decision_reason != 'low_confidence_blocked_routing'",
            (*params, threshold),
        ).fetchone()
        report.low_confidence_safe_handled = int(row[0]) if row else 0

        report.catch_all_safe_handled = report.decision_reason_distribution.get(
            "catch_all_blocked_routing", 0
        )
        report.unverified_provider_blocked = report.decision_reason_distribution.get(
            "unverified_provider_blocked", 0
        )
        report.missing_slots_blocked = report.decision_reason_distribution.get(
            "missing_slots_blocked_routing", 0
        )
        report.budget_risk_flags = report.action_distribution.get(
            "flag_budget_risk", 0
        )

    report.review_areas = _review_areas(report)
    return report


def _review_areas(report: PolicyReport) -> List[str]:
    """Operator-narrative recommendations. Heuristic, not authoritative."""
    out: List[str] = []
    if report.total_decisions == 0:
        out.append(
            "No dry-run policy decisions yet — start the proxy and route some "
            "traffic through it before re-running this report."
        )
        return out

    warn_count = report.action_distribution.get("warn_only", 0)
    if warn_count and warn_count / report.total_decisions > 0.5:
        pct = round(100 * warn_count / report.total_decisions)
        out.append(
            f"warn_only is {pct}% of policy decisions — investigate which "
            f"safety flag dominates and whether it's calibrated correctly."
        )

    observe_count = report.action_distribution.get("observe_only", 0)
    if observe_count == report.total_decisions and report.total_decisions > 0:
        out.append(
            "Every decision is observe_only — the per-intent heuristic table "
            "may need broader entries before a future Phase 2.4 suggest mode "
            "becomes useful."
        )

    if report.unverified_provider_blocked:
        out.append(
            f"{report.unverified_provider_blocked} request(s) hit "
            f"unverified_provider_blocked; confirm provider live_verified "
            f"flags are set correctly in credential_injector."
        )

    return out


def render_human(report: PolicyReport) -> str:
    """Operator-readable plain-text policy report.

    Used as a section appended to ``tokenpak intent report`` output;
    can also be rendered standalone.
    """
    lines: List[str] = []
    lines.append("")
    lines.append("  ── Policy summary (Phase 2.2 dry-run / preview only) ──")
    lines.append("")
    if report.total_decisions == 0:
        lines.append("  No dry-run policy decisions in this window.")
        lines.append("")
        if report.review_areas:
            for ra in report.review_areas:
                lines.append(f"  - {ra}")
            lines.append("")
        return "\n".join(lines)

    lines.append(f"  Total dry-run decisions:   {report.total_decisions}")
    lines.append("")
    lines.append("  Action distribution:")
    for action, count in report.action_distribution.items():
        pct = round(100 * count / report.total_decisions)
        lines.append(f"    {action:<32s} {count:>6d}  ({pct}%)")
    lines.append("")

    if report.safety_flag_distribution:
        lines.append("  Safety flags raised:")
        for flag, count in sorted(
            report.safety_flag_distribution.items(),
            key=lambda kv: -kv[1],
        ):
            lines.append(f"    {flag:<32s} {count}")
        lines.append("")

    lines.append("  Safety summary:")
    lines.append(f"    low_confidence_blocked:     {report.low_confidence_blocked}")
    lines.append(f"    low_confidence_safe_handled:{report.low_confidence_safe_handled}")
    lines.append(f"    catch_all_safe_handled:     {report.catch_all_safe_handled}")
    lines.append(f"    unverified_provider_blocked:{report.unverified_provider_blocked}")
    lines.append(f"    missing_slots_blocked:      {report.missing_slots_blocked}")
    lines.append(f"    budget_risk_flags:          {report.budget_risk_flags}")
    lines.append("")

    if report.compression_profile_distribution:
        lines.append("  Suggested compression profiles:")
        for profile, count in report.compression_profile_distribution.items():
            lines.append(f"    {profile:<24s} {count}")
        lines.append("")
    if report.cache_strategy_distribution:
        lines.append("  Suggested cache strategies:")
        for strat, count in report.cache_strategy_distribution.items():
            lines.append(f"    {strat:<24s} {count}")
        lines.append("")
    if report.delivery_strategy_distribution:
        lines.append("  Suggested delivery strategies:")
        for strat, count in report.delivery_strategy_distribution.items():
            lines.append(f"    {strat:<24s} {count}")
        lines.append("")

    if report.review_areas:
        lines.append("  Recommended review areas:")
        for ra in report.review_areas:
            lines.append(f"    - {ra}")
        lines.append("")

    lines.append("  Phase 2.2 is observation only. No routing decisions made.")
    lines.append("  See docs/reference/intent-policy-preview.md for what NOT")
    lines.append("  to infer from these aggregations.")
    lines.append("")
    return "\n".join(lines)


def render_json(report: PolicyReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


__all__ = [
    "PolicyReport",
    "build_policy_report",
    "render_human",
    "render_json",
]
