# SPDX-License-Identifier: Apache-2.0
"""Intent Layer Phase 1 — observation/reporting query layer + renderers.

Read-only summary over the ``intent_events`` SQLite table written
by Phase 0 (:mod:`tokenpak.proxy.intent_contract`). Surfaces the
ten aggregations the proposal §6 measurement plan calls for, plus
five operator-narrative sections mandated by the Phase 1 directive
(2026-04-25):

  - "Top missing slots"
  - "Top catch-all reasons"
  - "Adapters eligible for TIP intent headers" (declare the gate)
  - "Adapters blocking TIP intent headers" (do NOT declare)
  - "Recommended review areas" (heuristic flags drawn from the
    aggregations — see :func:`_review_areas`)

Privacy contract
----------------

The report **MUST NOT** read or emit raw prompt content. Every
query in this module reads the ``raw_prompt_hash`` (sha256 hex
digest) at most; the rendering paths assert this in tests
(``tests/test_intent_report.py::test_no_prompt_text_in_human_or_json``).

Window semantics
----------------

``--window 14d`` translates to ``timestamp >= now - 14 days`` over
the row's ISO-8601 timestamp string. Rows older than the window are
EXCLUDED from every aggregation. Default window is 14 days
(matches the proposal's 2-week observation default). Pass any
``Nd`` form (``7d``, ``30d``, ``1d``); ``0d`` and missing window
mean "all rows".

This module is read-only. It NEVER writes to the telemetry store,
NEVER mutates adapter state, NEVER invokes a provider.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Window parsing
# ---------------------------------------------------------------------------


_WINDOW_RE = re.compile(r"^(\d+)d$")


def parse_window(spec: Optional[str]) -> Optional[int]:
    """Parse a window spec like ``"14d"`` to an integer day count.

    Returns ``None`` when ``spec`` is empty / ``None`` / ``"0d"``
    (interpret as "no window — read every row"). Raises
    :class:`ValueError` on malformed input so the CLI can surface a
    clean error message.
    """
    if spec is None or spec == "":
        return None
    m = _WINDOW_RE.match(spec)
    if m is None:
        raise ValueError(
            f"--window must be of the form 'Nd' (e.g. '14d', '7d'); got {spec!r}"
        )
    n = int(m.group(1))
    if n == 0:
        return None
    return n


def window_cutoff_iso(days: int, *, now: Optional[_dt.datetime] = None) -> str:
    """Compute the ISO-8601 timestamp ``days`` days ago.

    Used as the SQL filter floor — rows with ``timestamp >= cutoff``
    are included.
    """
    base = now if now is not None else _dt.datetime.now()
    return (base - _dt.timedelta(days=days)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------


@dataclass
class IntentReport:
    """All aggregated metrics for one window. JSON-serialisable.

    Field set covers every metric the directive enumerates.
    """

    # Window identity
    window_days: Optional[int]
    window_cutoff_iso: Optional[str]
    db_path: str

    # Volume
    total_classified: int = 0

    # Distributions
    intent_class_distribution: Dict[str, int] = field(default_factory=dict)
    avg_confidence_by_class: Dict[str, float] = field(default_factory=dict)
    catch_all_reason_distribution: Dict[str, int] = field(default_factory=dict)
    slots_present_frequency: Dict[str, int] = field(default_factory=dict)
    slots_missing_frequency: Dict[str, int] = field(default_factory=dict)

    # Confidence / wire-emission counts
    low_confidence_count: int = 0
    low_confidence_threshold: float = 0.4
    tip_headers_emitted: int = 0
    tip_headers_stripped: int = 0
    telemetry_only: int = 0

    # Adapter posture
    adapters_eligible: List[Dict[str, Any]] = field(default_factory=list)
    adapters_blocking: List[Dict[str, Any]] = field(default_factory=list)

    # Operator narrative
    top_missing_slots: List[Tuple[str, int]] = field(default_factory=list)
    top_catch_all_reasons: List[Tuple[str, int]] = field(default_factory=list)
    review_areas: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-friendly dict (tuples → 2-element lists)."""
        return {
            "window_days": self.window_days,
            "window_cutoff_iso": self.window_cutoff_iso,
            "db_path": self.db_path,
            "total_classified": self.total_classified,
            "intent_class_distribution": self.intent_class_distribution,
            "avg_confidence_by_class": self.avg_confidence_by_class,
            "catch_all_reason_distribution": self.catch_all_reason_distribution,
            "slots_present_frequency": self.slots_present_frequency,
            "slots_missing_frequency": self.slots_missing_frequency,
            "low_confidence_count": self.low_confidence_count,
            "low_confidence_threshold": self.low_confidence_threshold,
            "tip_headers_emitted": self.tip_headers_emitted,
            "tip_headers_stripped": self.tip_headers_stripped,
            "telemetry_only": self.telemetry_only,
            "adapters_eligible": self.adapters_eligible,
            "adapters_blocking": self.adapters_blocking,
            "top_missing_slots": [list(t) for t in self.top_missing_slots],
            "top_catch_all_reasons": [list(t) for t in self.top_catch_all_reasons],
            "review_areas": list(self.review_areas),
        }


def _intent_db_path() -> Path:
    """Resolved telemetry.db path. Mirrors :mod:`intent_contract`."""
    from tokenpak.proxy.intent_contract import _DEFAULT_DB_PATH

    return _DEFAULT_DB_PATH


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intent_events'"
    ).fetchone()
    return row is not None


def _collect_adapter_posture() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Walk the default adapter registry and split by gate declaration.

    Returns ``(eligible, blocking)`` lists. Each entry carries
    adapter class name + source_format. Capability sets are
    intentionally excluded — the report is about the §4.3 gate
    label only, and dumping every capability inflates noise.
    """
    eligible: List[Dict[str, Any]] = []
    blocking: List[Dict[str, Any]] = []
    try:
        from tokenpak.proxy.adapters import build_default_registry
        from tokenpak.proxy.intent_contract import GATE_CAPABILITY

        for ad in build_default_registry().adapters():
            entry = {
                "name": ad.__class__.__name__,
                "source_format": ad.source_format,
            }
            if GATE_CAPABILITY in ad.capabilities:
                eligible.append(entry)
            else:
                blocking.append(entry)
    except Exception:  # noqa: BLE001
        # Defensive — registry should never fail, but the report
        # MUST NOT raise on read-side traversal issues.
        pass
    return eligible, blocking


def _review_areas(report: IntentReport) -> List[str]:
    """Operator-narrative recommendations.

    Heuristic, not authoritative. Each line is a sentence
    describing a baseline pattern the operator should look at —
    NOT a prescription. Phase 1 is observation only.
    """
    areas: List[str] = []
    if report.total_classified == 0:
        areas.append(
            "No classified requests yet — start the proxy and route some "
            "traffic through it before re-running this report."
        )
        return areas

    # Catch-all dominance.
    catch_all_count = report.intent_class_distribution.get("query", 0)
    if catch_all_count and catch_all_count / report.total_classified > 0.5:
        pct = round(100 * catch_all_count / report.total_classified)
        areas.append(
            f"Catch-all 'query' is {pct}% of classifications — the keyword "
            f"table or canonical-intent set may need revision before Intent-1 "
            f"lifts the subsystem (proposal §6.3)."
        )

    # Low confidence.
    if report.total_classified and report.low_confidence_count / report.total_classified > 0.3:
        pct = round(100 * report.low_confidence_count / report.total_classified)
        areas.append(
            f"{pct}% of requests fell below the {report.low_confidence_threshold} "
            f"confidence floor. Inspect a sample of low-confidence prompts (hand-read "
            f"the raw request log) to ground-truth the rule-based classifier."
        )

    # Missing-slot dominance.
    if report.top_missing_slots:
        slot_name, count = report.top_missing_slots[0]
        if count and count / report.total_classified > 0.4:
            pct = round(100 * count / report.total_classified)
            areas.append(
                f"Slot '{slot_name}' is missing in {pct}% of classifications — "
                f"either the slot is genuinely optional in real traffic, or the "
                f"slot definition needs broadening (slot_definitions.yaml)."
            )

    # Wire-emission posture.
    if report.tip_headers_emitted == 0 and report.total_classified > 0:
        if report.adapters_eligible:
            areas.append(
                f"{len(report.adapters_eligible)} adapter(s) declare the gate "
                f"label but no requests emitted on the wire — confirm those "
                f"adapters resolved at request time (check `tokenpak doctor "
                f"--explain-last`)."
            )
        else:
            areas.append(
                "No adapter declares 'tip.intent.contract-headers-v1' — every "
                "classification stayed in local telemetry, which is the "
                "expected Phase 0 default. Opt-in is gated on this baseline."
            )

    # Volume.
    if report.total_classified < 500 and report.window_days and report.window_days >= 14:
        areas.append(
            f"Only {report.total_classified} classifications in the {report.window_days}-day "
            f"window — proposal §6 sets 500 as the meaningful-statistics floor. Extend "
            f"the window before drawing distribution-shape conclusions."
        )

    return areas


def build_report(
    *,
    window_days: Optional[int] = 14,
    db_path: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    top_n: int = 5,
) -> IntentReport:
    """Run every aggregation and return one fully-populated report.

    Read-only. ``window_days=None`` reads every row.
    """
    path = db_path if db_path is not None else _intent_db_path()
    cutoff = window_cutoff_iso(window_days, now=now) if window_days else None

    eligible, blocking = _collect_adapter_posture()
    report = IntentReport(
        window_days=window_days,
        window_cutoff_iso=cutoff,
        db_path=str(path),
        adapters_eligible=eligible,
        adapters_blocking=blocking,
    )
    # Confidence floor mirrors the classifier's CLASSIFY_THRESHOLD.
    try:
        from tokenpak.proxy.intent_classifier import CLASSIFY_THRESHOLD

        report.low_confidence_threshold = CLASSIFY_THRESHOLD
    except Exception:  # noqa: BLE001
        pass

    if not path.is_file():
        report.review_areas = _review_areas(report)
        return report

    where_clause = ""
    params: Tuple[Any, ...] = ()
    if cutoff is not None:
        where_clause = " WHERE timestamp >= ?"
        params = (cutoff,)

    with sqlite3.connect(str(path)) as conn:
        if not _table_exists(conn):
            report.review_areas = _review_areas(report)
            return report

        # Total classified
        row = conn.execute(
            f"SELECT COUNT(*) FROM intent_events{where_clause}", params
        ).fetchone()
        report.total_classified = int(row[0]) if row else 0

        if report.total_classified == 0:
            report.review_areas = _review_areas(report)
            return report

        # Intent class distribution + avg confidence by class
        for r in conn.execute(
            f"SELECT intent_class, COUNT(*), AVG(intent_confidence) "
            f"FROM intent_events{where_clause} GROUP BY intent_class "
            f"ORDER BY COUNT(*) DESC",
            params,
        ).fetchall():
            cls = r[0]
            count = int(r[1])
            avg_conf = round(float(r[2]) if r[2] is not None else 0.0, 4)
            report.intent_class_distribution[cls] = count
            report.avg_confidence_by_class[cls] = avg_conf

        # Catch-all reason distribution (only over rows with reason set)
        for r in conn.execute(
            f"SELECT catch_all_reason, COUNT(*) FROM intent_events"
            f"{where_clause}{' AND' if where_clause else ' WHERE'} "
            f"catch_all_reason IS NOT NULL GROUP BY catch_all_reason "
            f"ORDER BY COUNT(*) DESC",
            params,
        ).fetchall():
            report.catch_all_reason_distribution[r[0]] = int(r[1])

        # Low confidence count
        floor = report.low_confidence_threshold
        row = conn.execute(
            f"SELECT COUNT(*) FROM intent_events"
            f"{where_clause}{' AND' if where_clause else ' WHERE'} "
            f"intent_confidence < ?",
            (*params, floor),
        ).fetchone()
        report.low_confidence_count = int(row[0]) if row else 0

        # Wire-path counts
        row = conn.execute(
            f"SELECT "
            f"SUM(CASE WHEN tip_headers_emitted=1 THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN tip_headers_stripped=1 THEN 1 ELSE 0 END) "
            f"FROM intent_events{where_clause}",
            params,
        ).fetchone()
        report.tip_headers_emitted = int(row[0]) if row and row[0] is not None else 0
        report.tip_headers_stripped = int(row[1]) if row and row[1] is not None else 0
        # telemetry-only = stripped (the equivalent framing the
        # directive asks for, kept as a separate field so the
        # caller doesn't have to compute it).
        report.telemetry_only = report.tip_headers_stripped

        # Slot present/missing frequency. Slot lists are stored as
        # JSON arrays per row; aggregate by exploding in Python so
        # the SQL stays portable and read-only.
        slot_present_counts: Dict[str, int] = {}
        slot_missing_counts: Dict[str, int] = {}
        for r in conn.execute(
            f"SELECT intent_slots_present, intent_slots_missing "
            f"FROM intent_events{where_clause}",
            params,
        ).fetchall():
            try:
                for s in json.loads(r[0] or "[]"):
                    slot_present_counts[s] = slot_present_counts.get(s, 0) + 1
                for s in json.loads(r[1] or "[]"):
                    slot_missing_counts[s] = slot_missing_counts.get(s, 0) + 1
            except (TypeError, json.JSONDecodeError):
                # Defensive: a corrupt row should not crash the
                # report. Skip + continue.
                continue
        report.slots_present_frequency = dict(
            sorted(slot_present_counts.items(), key=lambda kv: -kv[1])
        )
        report.slots_missing_frequency = dict(
            sorted(slot_missing_counts.items(), key=lambda kv: -kv[1])
        )

    # Operator-narrative top-N
    report.top_missing_slots = [
        (k, v) for k, v in list(report.slots_missing_frequency.items())[:top_n]
    ]
    report.top_catch_all_reasons = [
        (k, v) for k, v in list(report.catch_all_reason_distribution.items())[:top_n]
    ]
    report.review_areas = _review_areas(report)
    return report


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_human(report: IntentReport) -> str:
    """Operator-readable plain-text report.

    Visual rhythm matches ``tokenpak doctor --intent`` so an
    operator running both back-to-back gets a coherent reading
    experience.
    """
    lines: List[str] = []
    lines.append("")
    lines.append("TOKENPAK  |  Intent Layer report (Phase 1)")
    lines.append("──────────────────────────────")
    lines.append("")

    window_str = (
        "all rows (no window)"
        if report.window_days is None
        else f"last {report.window_days}d (since {report.window_cutoff_iso})"
    )
    lines.append(f"  Window:                    {window_str}")
    lines.append(f"  Telemetry store:           {report.db_path}")
    lines.append(f"  Total classified:          {report.total_classified}")
    lines.append("")

    if report.total_classified == 0:
        lines.append("  No classified requests in this window.")
        lines.append("")
        if report.review_areas:
            lines.append("  Recommended review areas:")
            for ra in report.review_areas:
                lines.append(f"    - {ra}")
            lines.append("")
        return "\n".join(lines)

    lines.append("  Intent class distribution (count, avg confidence):")
    for cls, count in report.intent_class_distribution.items():
        avg = report.avg_confidence_by_class.get(cls, 0.0)
        pct = round(100 * count / report.total_classified)
        lines.append(f"    {cls:<14s} {count:>6d}  ({pct:>3d}%)   avg conf {avg:.2f}")
    lines.append("")

    floor = report.low_confidence_threshold
    if report.total_classified:
        lc_pct = round(100 * report.low_confidence_count / report.total_classified)
    else:
        lc_pct = 0
    lines.append(
        f"  Low confidence (<{floor}):    "
        f"{report.low_confidence_count} ({lc_pct}%)"
    )
    lines.append("")

    lines.append("  Wire-emission posture:")
    lines.append(f"    tip_headers_emitted:     {report.tip_headers_emitted}")
    lines.append(f"    tip_headers_stripped:    {report.tip_headers_stripped}")
    lines.append(f"    telemetry-only:          {report.telemetry_only}")
    lines.append("")

    lines.append("  Top missing slots:")
    if report.top_missing_slots:
        for name, count in report.top_missing_slots:
            lines.append(f"    {name:<20s} {count}")
    else:
        lines.append("    (none)")
    lines.append("")

    lines.append("  Top catch-all reasons:")
    if report.top_catch_all_reasons:
        for reason, count in report.top_catch_all_reasons:
            lines.append(f"    {reason:<28s} {count}")
    else:
        lines.append("    (none)")
    lines.append("")

    lines.append("  Adapters eligible for TIP intent headers:")
    if report.adapters_eligible:
        for a in report.adapters_eligible:
            lines.append(f"    ✓ {a['name']:<40s} ({a['source_format']})")
    else:
        lines.append("    (none — Phase 0 default; opt-in gated on this baseline)")
    lines.append("")

    lines.append("  Adapters blocking TIP intent headers:")
    if report.adapters_blocking:
        for a in report.adapters_blocking:
            lines.append(f"    · {a['name']:<40s} ({a['source_format']})")
    else:
        lines.append("    (none)")
    lines.append("")

    lines.append("  Recommended review areas:")
    if report.review_areas:
        for ra in report.review_areas:
            lines.append(f"    - {ra}")
    else:
        lines.append("    (no flags raised by the heuristic — keep observing)")
    lines.append("")

    lines.append("  Phase 1 is observation-only. No user-facing behavior changes.")
    lines.append("  See docs/reference/intent-reporting.md for what NOT to infer.")
    lines.append("")
    return "\n".join(lines)


def render_json(report: IntentReport) -> str:
    """Machine-readable JSON dump of the report."""
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


__all__ = [
    "IntentReport",
    "build_report",
    "parse_window",
    "render_human",
    "render_json",
    "window_cutoff_iso",
]
