# SPDX-License-Identifier: Apache-2.0
"""``tokenpak dispatch`` CLI — Decision Inbox + dispatch verbs (P-CLI-01).

TokenPak Dispatch (Standards Delta v0) is an OSS workflow-control layer that
turns ad-hoc requests into scoped, station-based, resumable, gated, auditable
work packages. This module is the **CLI-first** control surface for v0.1-alpha
(§14.3: MCP is post-alpha; the CLI ships first).

Command group (Standards Delta v0 §14.1)::

    tokenpak dispatch run "request text" [--route --autonomy --ci --dry-run --confirm --json]
    tokenpak dispatch status   <job_id>      [--json]
    tokenpak dispatch inspect  <job_id>      [--late] [--json]
    tokenpak dispatch decisions [--job <job_id>] [--json]
    tokenpak dispatch approve  <decision_id> [--option <id>] [--json]
    tokenpak dispatch reject   <decision_id> [--json]
    tokenpak dispatch pause    <job_id>      [--json]
    tokenpak dispatch resume   <job_id>      [--json]
    tokenpak dispatch cancel   <job_id>      [--json]
    tokenpak dispatch discard-late <station_run_id> [--json]
    tokenpak dispatch delivery <job_id>      [--json]
    tokenpak dispatch receipt  <job_id>      [--json]

Design notes:

* Records persist to the Run Ledger (``<tokenpak-home>/dispatch/runs.db``) via
  :class:`~tokenpak.orchestration.dispatch.ledger.db.RunLedger`. The ledger path
  honors ``TOKENPAK_HOME`` so tests drive a tmp home and never touch ``~/.tpk``.
* ``run`` performs FrontDock intake → DispatchRuntime route selection and
  persists the job/manifest/route (+ any DispatchDecision). Station *execution*
  is an LLM boundary not wired into the alpha CLI; ``run`` therefore stops at the
  dispatch decision (auto-dispatch records the bound route; a decision is filed
  in the Decision Inbox when approval/clarification is required).
* The **Decision Inbox MVP** is the ``decisions`` / ``approve`` / ``reject``
  verbs over ``DispatchDecision`` records. Cards render human-readable with a
  ``--json`` fallback for scripting (§13 item 17).
* User-facing output uses plain **Worker / Route / Station** terminology. The
  legacy worker-alias bigram is excluded by the §11 verification gate.
* Receipt + Delivery output is run through the public-safe sanitizer
  (:func:`tokenpak.orchestration.dispatch.public_safe.sanitize_public_text`)
  before display — these surfaces are public-export-eligible (§10).
"""

from __future__ import annotations

import functools
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Preview-honesty boundary (v0.1-alpha)
# ---------------------------------------------------------------------------
# Station execution — the LLM execution boundary — is intentionally NOT wired
# into the CLI in this preview build, so ``dispatch run`` stops at the dispatch
# decision and no station runs execute. Delivery packages and receipts are
# therefore never produced through the CLI alone in this build. That absence is
# expected for the preview; it is not a failed or incomplete job. The receipt /
# delivery / inspect verbs surface this note so an empty receipt does not read
# as a defect. (Wiring station execution is a separate, post-alpha scope.)
_ALPHA_PREVIEW_NO_RECEIPT_NOTE = (
    "Preview build (v0.1-alpha): station execution is not wired into the CLI, "
    "so no station runs execute and no receipt is produced in this build. "
    "This is expected for the preview — not a failed job."
)

# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def build_dispatch_parser(sub: Any) -> None:
    """Register the ``tokenpak dispatch`` command group on *sub*."""
    p = sub.add_parser(
        "dispatch",
        help="Run, observe, and decide on Dispatch jobs (workflow control)",
        description=(
            "TokenPak Dispatch — scoped, station-based, resumable, gated work "
            "packages with a Decision Inbox and delivery receipts (OSS, "
            "v0.1-alpha preview — not yet in a released pip package; available "
            "on the project main branch; CLI-first)."
        ),
    )
    dsub = p.add_subparsers(dest="dispatch_action", required=False)

    # -- run -----------------------------------------------------------------
    p_run = dsub.add_parser("run", help="Intake + route a request into a Dispatch job")
    p_run.add_argument("request", help="The request text to dispatch")
    p_run.add_argument(
        "--route", dest="route", default=None,
        help="Force an explicit Route (e.g. code_task); overrides auto-routing",
    )
    p_run.add_argument(
        "--autonomy", dest="autonomy", default=None,
        choices=["advisory", "draft", "dispatch_with_approval", "auto_dispatch_limited"],
        help="Autonomy mode override (default depends on caller — §14.2)",
    )
    p_run.add_argument(
        "--ci", dest="ci", action="store_true",
        help="CI/automation caller; default autonomy = auto_dispatch_limited",
    )
    p_run.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Draft only; default autonomy = draft",
    )
    p_run.add_argument(
        "--confirm", dest="confirm", action="store_true",
        help="Treat an approval-gated route as approved (record the bound route)",
    )
    _add_json(p_run)
    p_run.set_defaults(func=cmd_dispatch_run)

    # -- status --------------------------------------------------------------
    p_status = dsub.add_parser("status", help="Show a job's current status")
    p_status.add_argument("job_id", help="Dispatch job id (job_…)")
    _add_json(p_status)
    p_status.set_defaults(func=cmd_dispatch_status)

    # -- inspect -------------------------------------------------------------
    p_inspect = dsub.add_parser("inspect", help="Inspect a job's full record set")
    p_inspect.add_argument("job_id", help="Dispatch job id (job_…)")
    p_inspect.add_argument(
        "--late", dest="late", action="store_true",
        help="Include late results (post-cancellation TIP output)",
    )
    _add_json(p_inspect)
    p_inspect.set_defaults(func=cmd_dispatch_inspect)

    # -- decisions (inbox list) ---------------------------------------------
    p_dec = dsub.add_parser("decisions", help="List Decision Inbox cards")
    p_dec.add_argument(
        "--job", dest="job", default=None,
        help="Filter to one job id",
    )
    _add_json(p_dec)
    p_dec.set_defaults(func=cmd_dispatch_decisions)

    # -- approve -------------------------------------------------------------
    p_appr = dsub.add_parser("approve", help="Approve a pending decision")
    p_appr.add_argument("decision_id", help="Decision id (decision_…)")
    p_appr.add_argument(
        "--option", dest="option", default=None,
        help="Selected option id (default: the recommended option)",
    )
    _add_json(p_appr)
    p_appr.set_defaults(func=cmd_dispatch_approve)

    # -- reject --------------------------------------------------------------
    p_rej = dsub.add_parser("reject", help="Reject a pending decision")
    p_rej.add_argument("decision_id", help="Decision id (decision_…)")
    _add_json(p_rej)
    p_rej.set_defaults(func=cmd_dispatch_reject)

    # -- pause ---------------------------------------------------------------
    p_pause = dsub.add_parser("pause", help="Pause a running job")
    p_pause.add_argument("job_id", help="Dispatch job id (job_…)")
    _add_json(p_pause)
    p_pause.set_defaults(func=cmd_dispatch_pause)

    # -- resume --------------------------------------------------------------
    p_resume = dsub.add_parser("resume", help="Resume a paused/interrupted job")
    p_resume.add_argument("job_id", help="Dispatch job id (job_…)")
    _add_json(p_resume)
    p_resume.set_defaults(func=cmd_dispatch_resume)

    # -- cancel --------------------------------------------------------------
    p_cancel = dsub.add_parser("cancel", help="Cancel a job (late results handled)")
    p_cancel.add_argument("job_id", help="Dispatch job id (job_…)")
    _add_json(p_cancel)
    p_cancel.set_defaults(func=cmd_dispatch_cancel)

    # -- discard-late --------------------------------------------------------
    p_dlate = dsub.add_parser(
        "discard-late", help="Discard a late result for a station run"
    )
    p_dlate.add_argument("station_run_id", help="Station run id (stationrun_…)")
    _add_json(p_dlate)
    p_dlate.set_defaults(func=cmd_dispatch_discard_late)

    # -- delivery ------------------------------------------------------------
    p_del = dsub.add_parser("delivery", help="Show a job's Delivery Package")
    p_del.add_argument("job_id", help="Dispatch job id (job_…)")
    _add_json(p_del)
    p_del.set_defaults(func=cmd_dispatch_delivery)

    # -- receipt -------------------------------------------------------------
    p_rcpt = dsub.add_parser("receipt", help="Show a job's delivery Receipt")
    p_rcpt.add_argument("job_id", help="Dispatch job id (job_…)")
    _add_json(p_rcpt)
    p_rcpt.set_defaults(func=cmd_dispatch_receipt)

    p.set_defaults(func=lambda a: (p.print_help() or 0))


def _add_json(parser: Any) -> None:
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit machine-readable JSON instead of human-readable output",
    )


# ---------------------------------------------------------------------------
# Ledger access
# ---------------------------------------------------------------------------


def _ledger():
    """Open the Run Ledger at the canonical Dispatch path (honors TOKENPAK_HOME)."""
    from tokenpak.orchestration.dispatch.ledger.db import RunLedger

    return RunLedger()


def _ledger_path():
    from tokenpak.orchestration.dispatch.ledger.db import ledger_db_path

    return ledger_db_path()


def _emit(payload: dict, as_json: bool, render) -> int:
    """Common emit helper: JSON branch or human render. Returns the exit code."""
    if as_json:
        print(json.dumps(payload, indent=2, default=str, sort_keys=True))
        return int(payload.get("_rc", 0))
    return render(payload)


def _err(msg: str, as_json: bool, *, code: str = "error") -> int:
    if as_json:
        print(json.dumps({"error": code, "detail": msg}))
    else:
        print(f"✗ {msg}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Runtime availability gate (Dispatch v0.1-alpha: runtime is source/main-only)
# ---------------------------------------------------------------------------
#
# The Dispatch *runtime engine* (DispatchRuntime / FrontDock / Run Ledger) is
# excluded from the released wheel for v0.1-alpha — only the CLI command file and
# the registry/schema DATA ship under ``tokenpak/orchestration/dispatch/``. Those
# data files make that directory a PEP 420 *namespace package*, so probing the
# package directory (``find_spec("tokenpak.orchestration.dispatch")``) is NOT a
# reliable presence check — it resolves non-``None`` even when no runtime module
# is installed. We therefore sentinel on a real runtime module. The runtime
# package is build-excluded as a unit, so a single sentinel is sufficient.

_DISPATCH_RUNTIME_SENTINEL = "tokenpak.orchestration.dispatch.dispatch"

_DISPATCH_RUNTIME_UNAVAILABLE_MSG = (
    "Dispatch runtime is source/main-only in TokenPak v0.1-alpha. This build "
    "ships the Dispatch CLI and registry/schema data but not the runtime engine. "
    "The optional `[dispatch]` extra installs preview dependencies for running "
    "Dispatch from a source checkout — it does not bundle a packaged runtime. "
    "Run Dispatch from a source/main install to use this verb."
)


def _dispatch_runtime_available() -> bool:
    """Return ``True`` when the Dispatch runtime engine is importable.

    Checks the real runtime module rather than the namespace-package directory,
    so a slim/data-only install (where ``tokenpak/orchestration/dispatch/`` exists
    only as a PEP 420 namespace package of registry/schema data) reports the
    runtime as absent instead of falsely present.
    """
    try:
        return importlib.util.find_spec(_DISPATCH_RUNTIME_SENTINEL) is not None
    except (ImportError, ValueError):
        return False


def _needs_runtime(fn):
    """Degrade a runtime-touching dispatch verb to an actionable message.

    When the Dispatch runtime engine is absent (e.g. the slim released wheel),
    invoking a runtime verb returns a concise, nonzero "source/main-only" notice
    instead of raising a raw ``ModuleNotFoundError`` traceback (B1). The message
    also explains the truthful ``[dispatch]`` extra contract (B2).
    """

    @functools.wraps(fn)
    def _wrapper(args: Any) -> int:
        if not _dispatch_runtime_available():
            return _err(
                _DISPATCH_RUNTIME_UNAVAILABLE_MSG,
                getattr(args, "as_json", False),
                code="dispatch_runtime_unavailable",
            )
        return fn(args)

    return _wrapper


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _default_autonomy(args: Any) -> str:
    """Resolve the default autonomy mode for the caller (Standards Delta v0 §14.2).

    Precedence: explicit ``--autonomy`` > ``--dry-run`` (draft) > ``--ci``
    (auto_dispatch_limited) > bare CLI default (dispatch_with_approval).
    """
    if getattr(args, "autonomy", None):
        return args.autonomy
    if getattr(args, "dry_run", False):
        return "draft"
    if getattr(args, "ci", False):
        return "auto_dispatch_limited"
    return "dispatch_with_approval"


@_needs_runtime
def cmd_dispatch_run(args: Any) -> int:
    from tokenpak.orchestration.dispatch.dispatch import DispatchRuntime
    from tokenpak.orchestration.dispatch.frontdock import FrontDock

    as_json = getattr(args, "as_json", False)
    autonomy = _default_autonomy(args)

    intake = FrontDock().intake(args.request, autonomy_mode=autonomy)
    runtime = DispatchRuntime()
    outcome = runtime.select_route(intake, explicit_route=getattr(args, "route", None))

    ledger = _ledger()
    try:
        ledger.write_job(intake.job)
        ledger.write_manifest(intake.manifest)
        if outcome.route is not None:
            ledger.write_route(outcome.route)
        # Decisions come from FrontDock (blocking gap) and/or route selection.
        decisions = [d for d in (intake.decision, outcome.decision) if d is not None]
        for decision in decisions:
            ledger.write_decision(decision)
    finally:
        ledger.close()

    payload = {
        "job_id": intake.job.id,
        "manifest_id": intake.manifest.id,
        "detected_intent": intake.job.detected_intent,
        "autonomy_mode": autonomy,
        "selection_status": outcome.status,
        "precedence_layer": outcome.precedence_layer,
        "confidence": outcome.confidence,
        "route_id": outcome.route.id if outcome.route else None,
        "route_name": outcome.route.name if outcome.route else None,
        "decision_ids": [d.id for d in decisions],
        "assumptions": list(intake.job.assumptions),
        "missing_info": list(intake.job.missing_info),
        "risk_flags": list(intake.job.risk_flags),
        "confirm": bool(getattr(args, "confirm", False)),
    }

    def render(p: dict) -> int:
        print("Dispatch run")
        print("────────────")
        print(f"  Job        : {p['job_id']}")
        print(f"  Intent     : {p['detected_intent']}")
        print(f"  Autonomy   : {p['autonomy_mode']}")
        if p["route_id"]:
            print(f"  Route      : {p['route_name']} ({p['route_id']})")
        else:
            print("  Route      : (none selected)")
        print(f"  Selection  : {p['selection_status']}  "
              f"[layer={p['precedence_layer']}, confidence={p['confidence']}]")
        if p["decision_ids"]:
            print()
            print("  Decision Inbox:")
            for did in p["decision_ids"]:
                print(f"    • {did}  →  tokenpak dispatch approve {did}")
        if p["assumptions"]:
            print("  Assumptions:")
            for a in p["assumptions"]:
                print(f"    - {a}")
        if p["missing_info"]:
            print("  Missing info:")
            for m in p["missing_info"]:
                print(f"    - {m}")
        if p["selection_status"] == "auto_dispatch":
            print()
            print("  → Route auto-dispatched. Inspect with "
                  f"`tokenpak dispatch status {p['job_id']}`.")
        elif p["selection_status"] == "needs_approval":
            print()
            print("  → Route selected but needs approval (autonomy gate). "
                  "Confirm or raise autonomy to dispatch.")
        elif p["selection_status"] == "refused":
            print()
            print("  → No route was confident enough to dispatch (refused).")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# status / inspect
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_status(args: Any) -> int:
    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        job = ledger.read_job(args.job_id)
        if job is None:
            return _err(f"no such job: {args.job_id}", as_json, code="job_not_found")
        runs = _query_runs_for_job(ledger, args.job_id)
        pending = _count_pending_decisions(ledger, args.job_id)
        control_state = _read_control_state(ledger, args.job_id) or "active"
    finally:
        ledger.close()

    latest = runs[-1] if runs else None
    payload = {
        "job_id": job.id,
        "status": _enum_value(job.status),
        "control_state": control_state,
        "detected_intent": job.detected_intent,
        "autonomy_mode": _enum_value(job.autonomy_mode),
        "run_id": latest["id"] if latest else None,
        "run_status": latest["status"] if latest else None,
        "run_count": len(runs),
        "pending_decisions": pending,
        "source_task_packet_id": job.source_task_packet_id,
    }

    def render(p: dict) -> int:
        print(f"Job {p['job_id']}")
        print("─" * (4 + len(p["job_id"])))
        print(f"  Status          : {p['status']}")
        if p["control_state"] != "active":
            print(f"  Control state   : {p['control_state']}")
        print(f"  Intent          : {p['detected_intent']}")
        print(f"  Autonomy        : {p['autonomy_mode']}")
        print(f"  Runs            : {p['run_count']}")
        if p["run_id"]:
            print(f"  Latest run      : {p['run_id']} ({p['run_status']})")
        print(f"  Pending decisions: {p['pending_decisions']}")
        if p["source_task_packet_id"]:
            print(f"  Task packet     : {p['source_task_packet_id']}")
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_inspect(args: Any) -> int:
    as_json = getattr(args, "as_json", False)
    include_late = getattr(args, "late", False)
    ledger = _ledger()
    try:
        job = ledger.read_job(args.job_id)
        if job is None:
            return _err(f"no such job: {args.job_id}", as_json, code="job_not_found")
        manifests = _query_by_job(ledger, "dispatch_manifests", args.job_id)
        runs = _query_runs_for_job(ledger, args.job_id)
        decisions = _query_decisions(ledger, args.job_id)
        receipts = _query_by_job(ledger, "dispatch_receipts", args.job_id)
        late = _query_by_job(ledger, "late_results", args.job_id) if include_late else []
    finally:
        ledger.close()

    payload = {
        "job": json.loads(job.model_dump_json()),
        "manifests": [r["id"] for r in manifests],
        "runs": [{"id": r["id"], "status": r["status"]} for r in runs],
        "decisions": [
            {"id": d["id"], "status": d["status"], "scope": d["scope"]}
            for d in decisions
        ],
        "receipts": [r["id"] for r in receipts],
    }
    if not receipts:
        payload["note"] = _ALPHA_PREVIEW_NO_RECEIPT_NOTE
    if include_late:
        payload["late_results"] = [
            {"id": r["id"], "station_run_id": r.get("station_run_id")} for r in late
        ]

    def render(p: dict) -> int:
        print(f"Inspect job {p['job']['id']}")
        print("─" * 40)
        print(f"  Request    : {p['job']['raw_request']}")
        print(f"  Intent     : {p['job']['detected_intent']}")
        print(f"  Status     : {p['job']['status']}")
        print(f"  Autonomy   : {p['job']['autonomy_mode']}")
        print(f"  Manifests  : {', '.join(p['manifests']) or '(none)'}")
        print("  Runs:")
        for r in p["runs"]:
            print(f"    - {r['id']} ({r['status']})")
        if not p["runs"]:
            print("    (none)")
        print("  Decisions:")
        for d in p["decisions"]:
            print(f"    - {d['id']} [{d['scope']}] {d['status']}")
        if not p["decisions"]:
            print("    (none)")
        print(f"  Receipts   : {', '.join(p['receipts']) or '(none)'}")
        if not p["receipts"]:
            print(f"  Note       : {_ALPHA_PREVIEW_NO_RECEIPT_NOTE}")
        if include_late:
            print("  Late results:")
            for r in p.get("late_results", []):
                print(f"    - {r['id']} (station {r['station_run_id']})")
            if not p.get("late_results"):
                print("    (none)")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# Decision Inbox: decisions / approve / reject
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_decisions(args: Any) -> int:
    as_json = getattr(args, "as_json", False)
    job_filter = getattr(args, "job", None)
    ledger = _ledger()
    try:
        rows = _query_decisions(ledger, job_filter)
        decisions = [ledger.read_decision(r["id"]) for r in rows]
        decisions = [d for d in decisions if d is not None]
    finally:
        ledger.close()

    cards = [_decision_card(d) for d in decisions]
    payload = {"count": len(cards), "decisions": cards}

    def render(p: dict) -> int:
        if not p["decisions"]:
            scope = f" for job {job_filter}" if job_filter else ""
            print(f"Decision Inbox is empty{scope}.")
            return 0
        print(f"Decision Inbox — {p['count']} decision(s)")
        print("═" * 50)
        for c in p["decisions"]:
            _print_decision_card(c)
            print()
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_approve(args: Any) -> int:
    return _resolve_decision(args, approve=True)


@_needs_runtime
def cmd_dispatch_reject(args: Any) -> int:
    return _resolve_decision(args, approve=False)


def _resolve_decision(args: Any, *, approve: bool) -> int:
    from tokenpak.orchestration.dispatch.models.enums import DecisionStatus, ResolvedBy

    as_json = getattr(args, "as_json", False)
    verb = "approve" if approve else "reject"
    ledger = _ledger()
    try:
        decision = ledger.read_decision(args.decision_id)
        if decision is None:
            return _err(
                f"no such decision: {args.decision_id}", as_json,
                code="decision_not_found",
            )
        if decision.status != DecisionStatus.PENDING:
            return _err(
                f"decision already {_enum_value(decision.status)}: {args.decision_id}",
                as_json, code="decision_not_pending",
            )

        if approve:
            option_id = getattr(args, "option", None) or decision.recommendation.option_id
            valid = {o.id for o in decision.options}
            if option_id not in valid:
                return _err(
                    f"unknown option {option_id!r} (valid: {sorted(valid)})",
                    as_json, code="unknown_option",
                )
            decision.status = DecisionStatus.RESOLVED
            decision.resolution.selected_option_id = option_id
        else:
            decision.status = DecisionStatus.CANCELLED
            decision.resolution.selected_option_id = None

        decision.resolution.resolved_by = ResolvedBy.USER
        decision.resolution.resolved_at = datetime.now(timezone.utc)
        ledger.write_decision(decision)
    finally:
        ledger.close()

    payload = {
        "decision_id": decision.id,
        "job_id": decision.job_id,
        "action": verb,
        "status": _enum_value(decision.status),
        "selected_option_id": decision.resolution.selected_option_id,
    }

    def render(p: dict) -> int:
        mark = "✓" if approve else "✗"
        print(f"{mark} Decision {p['decision_id']} {verb}d → {p['status']}")
        if p["selected_option_id"]:
            print(f"  Selected option: {p['selected_option_id']}")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# pause / resume / cancel
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_pause(args: Any) -> int:
    return _set_control_state(args, "paused", verb="pause")


@_needs_runtime
def cmd_dispatch_resume(args: Any) -> int:
    return _set_control_state(args, "active", verb="resume")


def _set_control_state(args: Any, control_state: str, *, verb: str) -> int:
    """Record a CLI control state (paused/active) WITHOUT mutating the job's
    canonical ``status`` enum (§6 has no ``paused`` member). The flag lives in a
    sidecar control table so the canonical DispatchJob payload stays valid.
    """
    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        job = ledger.read_job(args.job_id)
        if job is None:
            return _err(f"no such job: {args.job_id}", as_json, code="job_not_found")
        prior = _read_control_state(ledger, args.job_id) or "active"
        _write_control_state(ledger, args.job_id, control_state)
        job_status = _enum_value(job.status)
    finally:
        ledger.close()

    payload = {
        "job_id": args.job_id,
        "action": verb,
        "job_status": job_status,
        "prior_control_state": prior,
        "control_state": control_state,
    }

    def render(p: dict) -> int:
        print(f"Job {p['job_id']} {verb}d: "
              f"control {p['prior_control_state']} → {p['control_state']} "
              f"(status: {p['job_status']})")
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_cancel(args: Any) -> int:
    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        job = ledger.read_job(args.job_id)
        if job is None:
            return _err(f"no such job: {args.job_id}", as_json, code="job_not_found")
        prior = _enum_value(job.status)
        _update_job_status_row(ledger, args.job_id, "cancelled")
    finally:
        ledger.close()

    payload = {
        "job_id": args.job_id,
        "action": "cancel",
        "prior_status": prior,
        "status": "cancelled",
        "note": (
            "New stations are prevented from starting. Late TIP output is "
            "captured as a LateResult and never applied (§5.6). No token refund."
        ),
    }

    def render(p: dict) -> int:
        print(f"Job {p['job_id']} cancelled: {p['prior_status']} → cancelled")
        print(f"  {p['note']}")
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_discard_late(args: Any) -> int:
    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        rows = _query_rows(
            ledger, "SELECT * FROM late_results WHERE station_run_id = ?",
            (args.station_run_id,),
        )
        if not rows:
            return _err(
                f"no late results for station run: {args.station_run_id}",
                as_json, code="no_late_results",
            )
        discarded = [r["id"] for r in rows]
        for late_id in discarded:
            _delete_row(ledger, "late_results", late_id)
    finally:
        ledger.close()

    payload = {"station_run_id": args.station_run_id, "discarded": discarded}

    def render(p: dict) -> int:
        print(f"Discarded {len(p['discarded'])} late result(s) "
              f"for station run {p['station_run_id']}:")
        for lid in p["discarded"]:
            print(f"  - {lid}")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# delivery / receipt  (public-export-eligible → public-safe sanitized)
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_receipt(args: Any) -> int:
    from tokenpak.orchestration.dispatch.public_safe import sanitize_public_obj

    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        job = ledger.read_job(args.job_id)
        if job is None:
            return _err(f"no such job: {args.job_id}", as_json, code="job_not_found")
        rows = _query_by_job(ledger, "dispatch_receipts", args.job_id)
        receipts = [ledger.read_receipt(r["id"]) for r in rows]
        receipts = [r for r in receipts if r is not None]
    finally:
        ledger.close()

    if not receipts:
        return _err(
            f"no receipt for job {args.job_id}. {_ALPHA_PREVIEW_NO_RECEIPT_NOTE}",
            as_json, code="no_receipt",
        )

    receipt = receipts[-1]
    raw = json.loads(receipt.model_dump_json())
    payload = sanitize_public_obj(raw)

    def render(p: dict) -> int:
        print(f"Receipt {p['id']}")
        print("─" * 40)
        print(f"  Job          : {p['job_id']}")
        print(f"  Run          : {p['run_id']}")
        print(f"  Route        : {p['route_id']}")
        print(f"  Final status : {p['final_status']}")
        print("  Stations:")
        for s in p.get("stations", []):
            print(f"    - {s['station_run_id']} "
                  f"[Worker {s['worker_id']}] {s['status']}")
            if s.get("result_payload_excerpt"):
                print(f"        {s['result_payload_excerpt']}")
        tele = p.get("telemetry", {})
        print("  Telemetry:")
        print(f"    input_tokens  : {tele.get('total_input_tokens', 0)}")
        print(f"    output_tokens : {tele.get('total_output_tokens', 0)}")
        print(f"    latency_ms    : {tele.get('total_latency_ms', 0)}")
        print(f"    cache_hits    : {tele.get('cache_hits', 0)}")
        cost = tele.get("estimated_cost")
        if cost is not None:
            print(f"    estimated_cost: ${cost:.4f}")
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_delivery(args: Any) -> int:
    """Show the Delivery Package view for a job.

    The Gatehouse builds a :class:`DeliveryPackage` during a run; the alpha CLI
    derives a delivery view from the persisted run + receipt (the delivery
    surfaces are public-export-eligible, so the view is public-safe sanitized).
    """
    from tokenpak.orchestration.dispatch.public_safe import sanitize_public_obj

    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        job = ledger.read_job(args.job_id)
        if job is None:
            return _err(f"no such job: {args.job_id}", as_json, code="job_not_found")
        runs = _query_runs_for_job(ledger, args.job_id)
        receipts = _query_by_job(ledger, "dispatch_receipts", args.job_id)
    finally:
        ledger.close()

    latest_run = runs[-1] if runs else None
    raw = {
        "job_id": job.id,
        "status": _enum_value(job.status),
        "intent": job.detected_intent,
        "run_id": latest_run["id"] if latest_run else None,
        "run_status": latest_run["status"] if latest_run else None,
        "receipt_id": receipts[-1]["id"] if receipts else None,
        "delivered": bool(receipts),
        "summary": (
            f"Delivery for {job.detected_intent} job {job.id}: "
            f"{len(runs)} run(s), "
            f"{'receipt available' if receipts else 'no receipt in this build'}."
        ),
    }
    if not receipts:
        raw["note"] = _ALPHA_PREVIEW_NO_RECEIPT_NOTE
    payload = sanitize_public_obj(raw)

    def render(p: dict) -> int:
        print(f"Delivery Package — job {p['job_id']}")
        print("─" * 44)
        print(f"  Status   : {p['status']}")
        print(f"  Intent   : {p['intent']}")
        if p["run_id"]:
            print(f"  Run      : {p['run_id']} ({p['run_status']})")
        print(f"  Delivered: {'yes' if p['delivered'] else 'no'}")
        if p["receipt_id"]:
            print(f"  Receipt  : {p['receipt_id']}  "
                  f"(tokenpak dispatch receipt {p['job_id']})")
        print(f"  Summary  : {p['summary']}")
        if p.get("note"):
            print(f"  Note     : {p['note']}")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# Decision card rendering
# ---------------------------------------------------------------------------


def _decision_card(decision: Any) -> dict:
    return {
        "id": decision.id,
        "job_id": decision.job_id,
        "scope": _enum_value(decision.scope),
        "title": decision.title,
        "question": decision.question,
        "reason": decision.reason,
        "risk_level": _enum_value(decision.risk_level),
        "status": _enum_value(decision.status),
        "options": [
            {
                "id": o.id,
                "label": o.label,
                "description": o.description,
                "tradeoffs": list(o.tradeoffs),
            }
            for o in decision.options
        ],
        "recommendation": {
            "option_id": decision.recommendation.option_id,
            "rationale": decision.recommendation.rationale,
        },
        "selected_option_id": decision.resolution.selected_option_id,
    }


def _print_decision_card(c: dict) -> None:
    print(f"  {c['id']}   [{c['scope']} scope · risk {c['risk_level']} · {c['status']}]")
    print(f"    {c['title']}")
    print(f"    Q: {c['question']}")
    if c["reason"]:
        print(f"    Why: {c['reason']}")
    print("    Options:")
    for o in c["options"]:
        rec = " (recommended)" if o["id"] == c["recommendation"]["option_id"] else ""
        print(f"      [{o['id']}] {o['label']}{rec}")
        if o["description"]:
            print(f"          {o['description']}")
        for t in o["tradeoffs"]:
            print(f"          · {t}")
    if c["status"] == "pending":
        print(f"    → tokenpak dispatch approve {c['id']}   "
              f"| tokenpak dispatch reject {c['id']}")


# ---------------------------------------------------------------------------
# Ledger query helpers (read/list/update beyond RunLedger's by-id readers)
# ---------------------------------------------------------------------------


def _conn(ledger) -> sqlite3.Connection:
    """The RunLedger's live connection (read/update for listing + status changes)."""
    return ledger._conn  # noqa: SLF001 — intentional: same-package CLI surface.


def _query_rows(ledger, sql: str, params: tuple) -> list[dict]:
    conn = _conn(ledger)
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.row_factory = prior


def _query_by_job(ledger, table: str, job_id: str) -> list[dict]:
    return _query_rows(ledger, f"SELECT * FROM {table} WHERE job_id = ?", (job_id,))


def _query_runs_for_job(ledger, job_id: str) -> list[dict]:
    return _query_rows(
        ledger,
        "SELECT * FROM dispatch_runs WHERE job_id = ? ORDER BY rowid ASC",
        (job_id,),
    )


def _query_decisions(ledger, job_id: Optional[str]) -> list[dict]:
    if job_id:
        return _query_rows(
            ledger,
            "SELECT * FROM dispatch_decisions WHERE job_id = ? ORDER BY rowid ASC",
            (job_id,),
        )
    return _query_rows(
        ledger, "SELECT * FROM dispatch_decisions ORDER BY rowid ASC", ()
    )


def _count_pending_decisions(ledger, job_id: str) -> int:
    rows = _query_rows(
        ledger,
        "SELECT COUNT(*) AS n FROM dispatch_decisions "
        "WHERE job_id = ? AND status = 'pending'",
        (job_id,),
    )
    return int(rows[0]["n"]) if rows else 0


def _update_job_status_row(ledger, job_id: str, status: str) -> None:
    """Update the job's status column + the persisted payload's status field.

    ``status`` MUST be a valid :class:`DispatchJobStatus` enum value (the payload
    is re-validated on read), so this is only used for real state transitions
    such as ``cancelled``. CLI-only control states (paused) go through
    :func:`_write_control_state`, which never touches the canonical payload.
    """
    conn = _conn(ledger)
    row = _query_rows(
        ledger, "SELECT payload FROM dispatch_jobs WHERE id = ?", (job_id,)
    )
    if row:
        try:
            payload = json.loads(row[0]["payload"])
            payload["status"] = status
            new_payload = json.dumps(payload)
        except (ValueError, KeyError):
            new_payload = row[0]["payload"]
        with conn:
            conn.execute(
                "UPDATE dispatch_jobs SET status = ?, payload = ? WHERE id = ?",
                (status, new_payload, job_id),
            )
    else:
        with conn:
            conn.execute(
                "UPDATE dispatch_jobs SET status = ? WHERE id = ?",
                (status, job_id),
            )


# -- CLI control-state sidecar (pause/resume; not part of the §6 enum) -------

_CONTROL_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS dispatch_job_control ("
    "job_id TEXT PRIMARY KEY, control_state TEXT, updated_at TEXT)"
)


def _ensure_control_table(ledger) -> None:
    conn = _conn(ledger)
    with conn:
        conn.execute(_CONTROL_TABLE_DDL)


def _write_control_state(ledger, job_id: str, control_state: str) -> None:
    _ensure_control_table(ledger)
    conn = _conn(ledger)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO dispatch_job_control (job_id, control_state, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
            "control_state = excluded.control_state, updated_at = excluded.updated_at",
            (job_id, control_state, now),
        )


def _read_control_state(ledger, job_id: str) -> Optional[str]:
    _ensure_control_table(ledger)
    rows = _query_rows(
        ledger,
        "SELECT control_state FROM dispatch_job_control WHERE job_id = ?",
        (job_id,),
    )
    return rows[0]["control_state"] if rows else None


def _delete_row(ledger, table: str, row_id: str) -> None:
    conn = _conn(ledger)
    with conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def _enum_value(value: Any) -> Any:
    """Return ``.value`` for an Enum, else the value itself (str passthrough)."""
    return getattr(value, "value", value)


__all__ = [
    "build_dispatch_parser",
    "cmd_dispatch_run",
    "cmd_dispatch_status",
    "cmd_dispatch_inspect",
    "cmd_dispatch_decisions",
    "cmd_dispatch_approve",
    "cmd_dispatch_reject",
    "cmd_dispatch_pause",
    "cmd_dispatch_resume",
    "cmd_dispatch_cancel",
    "cmd_dispatch_discard_late",
    "cmd_dispatch_delivery",
    "cmd_dispatch_receipt",
]
