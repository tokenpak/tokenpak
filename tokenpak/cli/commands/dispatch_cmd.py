# SPDX-License-Identifier: Apache-2.0
"""``tokenpak dispatch`` CLI — Decision Inbox + dispatch verbs (P-CLI-01).

TokenPak Dispatch is an OSS workflow-control layer that
turns ad-hoc requests into scoped, station-based, resumable, gated, auditable
work packages. This module is the **CLI-first** control surface for v0.1-alpha
(MCP is post-alpha; the CLI ships first).

Command group::

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
  ``--json`` fallback for scripting.
* User-facing output uses plain **Worker / Route / Station** terminology. The
  legacy worker-alias bigram is excluded by the verification gate.
* Receipt + Delivery output is run through the public-safe sanitizer
  (:func:`tokenpak.orchestration.dispatch.public_safe.sanitize_public_text`)
  before display — these surfaces are public-export-eligible.
"""

from __future__ import annotations

import argparse
import functools
import importlib
import importlib.util
import json
import sqlite3
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol, TypedDict, TypeVar, cast

if TYPE_CHECKING:
    from tokenpak.orchestration.dispatch.ledger.db import RunLedger
    from tokenpak.orchestration.dispatch.models.decision import DispatchDecision

_PayloadT = TypeVar("_PayloadT")
_SqlValue = str | int | float | bytes | None


class _RouteTriggers(Protocol):
    intents: Sequence[str]


class _RouteStation(Protocol):
    id: str
    required_role: str | None
    required_capabilities: Sequence[str]


class _RouteProfile(Protocol):
    id: str
    name: str
    triggers: _RouteTriggers
    stations: Sequence[_RouteStation]


class _RouteRegistry(Protocol):
    def all(self) -> list[_RouteProfile]: ...


class _ReceiptStationRow(TypedDict):
    station_run_id: str
    worker_id: str
    status: str
    result_payload_excerpt: str


class _ReceiptTelemetryPayload(TypedDict):
    total_input_tokens: int
    total_output_tokens: int
    total_latency_ms: int
    cache_hits: int
    estimated_cost: float | None


class _ReceiptPayload(TypedDict):
    id: str
    job_id: str
    run_id: str
    route_id: str
    final_status: str
    stations: list[_ReceiptStationRow]
    telemetry: _ReceiptTelemetryPayload


class _DeliveryPayload(TypedDict, total=False):
    job_id: str
    status: object
    intent: str
    run_id: object
    run_status: object
    receipt_id: object
    delivered: bool
    summary: str
    note: str


class _DecisionOptionCard(TypedDict):
    id: str
    label: str
    description: str
    tradeoffs: list[str]


class _DecisionRecommendationCard(TypedDict):
    option_id: str
    rationale: str


class _DecisionCard(TypedDict):
    id: str
    job_id: str
    scope: str
    title: str
    question: str
    reason: str
    risk_level: str
    status: str
    options: list[_DecisionOptionCard]
    recommendation: _DecisionRecommendationCard
    selected_option_id: str | None


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


def build_dispatch_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
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
        "--route",
        dest="route",
        default=None,
        help="Force an explicit Route (e.g. code_task); overrides auto-routing",
    )
    p_run.add_argument(
        "--autonomy",
        dest="autonomy",
        default=None,
        choices=["advisory", "draft", "dispatch_with_approval", "auto_dispatch_limited"],
        help="Autonomy mode override (default depends on caller)",
    )
    p_run.add_argument(
        "--ci",
        dest="ci",
        action="store_true",
        help="CI/automation caller; default autonomy = auto_dispatch_limited",
    )
    p_run.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=(
            "Draft only; default autonomy = draft. Performs intake + route "
            "selection without persisting anything (no ledger writes)"
        ),
    )
    p_run.add_argument(
        "--confirm",
        dest="confirm",
        action="store_true",
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
        "--late",
        dest="late",
        action="store_true",
        help="Include late results (post-cancellation TIP output)",
    )
    _add_json(p_inspect)
    p_inspect.set_defaults(func=cmd_dispatch_inspect)

    # -- decisions (inbox list) ---------------------------------------------
    p_dec = dsub.add_parser("decisions", help="List Decision Inbox cards")
    p_dec.add_argument(
        "--job",
        dest="job",
        default=None,
        help="Filter to one job id",
    )
    _add_json(p_dec)
    p_dec.set_defaults(func=cmd_dispatch_decisions)

    # -- approve -------------------------------------------------------------
    p_appr = dsub.add_parser("approve", help="Approve a pending decision")
    p_appr.add_argument("decision_id", help="Decision id (decision_…)")
    p_appr.add_argument(
        "--option",
        dest="option",
        default=None,
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
    p_dlate = dsub.add_parser("discard-late", help="Discard a late result for a station run")
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

    # -- routes (read-only discovery) ---------------------------------------
    p_routes = dsub.add_parser(
        "routes",
        help="List available Dispatch routes (read-only discovery)",
    )
    _add_json(p_routes)
    p_routes.set_defaults(func=cmd_dispatch_routes)

    # -- workers (read-only discovery) --------------------------------------
    p_workers = dsub.add_parser(
        "workers",
        help="List available Dispatch workers (read-only discovery)",
    )
    _add_json(p_workers)
    p_workers.set_defaults(func=cmd_dispatch_workers)

    def _print_dispatch_help(_args: argparse.Namespace) -> int:
        p.print_help()
        return 0

    p.set_defaults(func=_print_dispatch_help)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable output",
    )


# ---------------------------------------------------------------------------
# Ledger access
# ---------------------------------------------------------------------------


def _ledger() -> RunLedger:
    """Open the Run Ledger at the canonical Dispatch path (honors TOKENPAK_HOME)."""
    from tokenpak.orchestration.dispatch.ledger.db import RunLedger

    return RunLedger()


def _ledger_path() -> Path:
    from tokenpak.orchestration.dispatch.ledger.db import ledger_db_path

    return ledger_db_path()


def _emit(
    payload: _PayloadT,
    as_json: bool,
    render: Callable[[_PayloadT], int],
) -> int:
    """Common emit helper: JSON branch or human render. Returns the exit code."""
    if as_json:
        print(json.dumps(payload, indent=2, default=str, sort_keys=True))
        if isinstance(payload, dict):
            raw_exit_code = payload.get("_rc", 0)
            if isinstance(raw_exit_code, int):
                return raw_exit_code
        return 0
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

# The ``[dispatch]`` extra deps declared in pyproject optional-dependencies.
# The Dispatch record models are pydantic-native and JSON-Schema round-trips use
# jsonschema; both are kept out of the slim core, so a base install can have the
# runtime *source* present yet lack these.
_DISPATCH_DEPS: tuple[str, ...] = ("pydantic", "jsonschema")

_DISPATCH_RUNTIME_UNAVAILABLE_MSG = (
    "Dispatch runtime is source/main-only in TokenPak v0.1-alpha. This build "
    "ships the Dispatch CLI and registry/schema data but not the runtime engine. "
    "The optional `[dispatch]` extra installs preview dependencies for running "
    "Dispatch from a source checkout — it does not bundle a packaged runtime. "
    "Run Dispatch from a source/main install to use this verb."
)


def _dispatch_deps_missing_msg(missing: list[str]) -> str:
    """Build the base-install dependency-gap message (truthful remedy: the extra).

    Distinct from :data:`_DISPATCH_RUNTIME_UNAVAILABLE_MSG`: the runtime engine IS
    present, so telling the tester to "use a source/main install" would be a false
    path (they already have one). The real fix is installing the ``[dispatch]``
    extra, so the message names the missing deps and the exact install command.
    """
    names = ", ".join(missing)
    return (
        f"Dispatch needs the optional `[dispatch]` dependencies ({names}), which "
        "are not installed. The Dispatch runtime engine ships in this build, but "
        "its record models are pydantic-native. Install the extra to run "
        "Dispatch:\n"
        "    pip install 'tokenpak[dispatch]'\n"
        "(This is a base-install dependency gap, not a missing runtime — a "
        "source/main install still needs the `[dispatch]` extra.)"
    )


def _missing_dispatch_deps() -> list[str]:
    """Return the ``[dispatch]`` extra deps that are not importable (side-effect free).

    Uses top-level :func:`importlib.util.find_spec` (which does not execute the
    module), so probing for pydantic/jsonschema never imports them and never
    triggers the pydantic-native package ``__init__``.
    """
    missing: list[str] = []
    for mod in _DISPATCH_DEPS:
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        except (ImportError, ValueError):
            missing.append(mod)
    return missing


def _dispatch_runtime_source_present() -> bool:
    """Return ``True`` when the Dispatch runtime module *file* ships in this build.

    Detects the runtime file on disk WITHOUT importing it. Importing the runtime —
    or even ``find_spec`` on the sentinel submodule — executes the
    ``tokenpak.orchestration.dispatch`` package ``__init__``, which imports the
    pydantic-native models; in a source install *without* the ``[dispatch]`` deps
    that import raises and the runtime masquerades as absent. We instead locate the
    file via the lazily-imported (pydantic-free) ``tokenpak.orchestration`` package
    path, so "runtime present but deps missing" is distinguishable from "runtime
    genuinely absent" (the slim wheel, where the file is not shipped at all).
    """
    try:
        spec = importlib.util.find_spec("tokenpak.orchestration")
    except (ImportError, ValueError):
        return False
    locations = getattr(spec, "submodule_search_locations", None) if spec else None
    if not locations:
        return False
    runtime_leaf = _DISPATCH_RUNTIME_SENTINEL.rsplit(".", 1)[-1] + ".py"
    for base in locations:
        if (Path(base) / "dispatch" / runtime_leaf).is_file():
            return True
    return False


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


def _needs_runtime(
    fn: Callable[[argparse.Namespace], int],
) -> Callable[[argparse.Namespace], int]:
    """Degrade a runtime-touching dispatch verb to a truthful, actionable message.

    Three environments are distinguished so a tester is never pointed at a false
    remedy, and none raise a raw ``ModuleNotFoundError`` traceback:

    * **runtime engine absent** (e.g. the slim released wheel ships CLI + data
      only): the concise "source/main-only" notice — the runtime file is not in
      this build.
    * **runtime present but ``[dispatch]`` deps absent** (a base source install
      without the extra): a ``pip install 'tokenpak[dispatch]'`` message. Before
      this fix the pydantic-native package ``__init__`` failed to import and the
      verb wrongly reported the runtime as source/main-only — pointing a tester
      who already has a source install at a non-fix.
    * **fully available**: the wrapped handler runs.
    """

    @functools.wraps(fn)
    def _wrapper(args: argparse.Namespace) -> int:
        as_json = bool(getattr(args, "as_json", False))
        if not _dispatch_runtime_source_present():
            return _err(
                _DISPATCH_RUNTIME_UNAVAILABLE_MSG,
                as_json,
                code="dispatch_runtime_unavailable",
            )
        missing = _missing_dispatch_deps()
        if missing:
            return _err(
                _dispatch_deps_missing_msg(missing),
                as_json,
                code="dispatch_dependencies_missing",
            )
        if not _dispatch_runtime_available():
            return _err(
                _DISPATCH_RUNTIME_UNAVAILABLE_MSG,
                as_json,
                code="dispatch_runtime_unavailable",
            )
        return fn(args)

    return _wrapper


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _default_autonomy(args: argparse.Namespace) -> str:
    """Resolve the default autonomy mode for the caller.

    Precedence: explicit ``--autonomy`` > ``--dry-run`` (draft) > ``--ci``
    (auto_dispatch_limited) > bare CLI default (dispatch_with_approval).
    """
    explicit = getattr(args, "autonomy", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    if getattr(args, "dry_run", False):
        return "draft"
    if getattr(args, "ci", False):
        return "auto_dispatch_limited"
    return "dispatch_with_approval"


@_needs_runtime
def cmd_dispatch_run(args: argparse.Namespace) -> int:
    from tokenpak.orchestration.dispatch.dispatch import DispatchRuntime
    from tokenpak.orchestration.dispatch.frontdock import FrontDock

    as_json = getattr(args, "as_json", False)
    autonomy = _default_autonomy(args)
    dry_run = bool(getattr(args, "dry_run", False))

    intake = FrontDock().intake(args.request, autonomy_mode=autonomy)
    runtime = DispatchRuntime()
    outcome = runtime.select_route(intake, explicit_route=getattr(args, "route", None))

    # Decisions come from FrontDock (blocking gap) and/or route selection.
    decisions = [d for d in (intake.decision, outcome.decision) if d is not None]

    # A dry run is WRITE-FREE: intake + route selection happen in memory only.
    # The ledger is not even opened (opening creates the DB file and applies
    # migrations), so a dry run leaves the on-disk ledger byte-identical.
    if not dry_run:
        ledger = _ledger()
        try:
            ledger.write_job(intake.job)
            ledger.write_manifest(intake.manifest)
            if outcome.route is not None:
                ledger.write_route(outcome.route)
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
        "dry_run": dry_run,
        "persisted": not dry_run,
    }
    if dry_run:
        payload["note"] = (
            "Dry run: nothing was persisted (no job, manifest, route, or "
            "decision records were written)."
        )

    def render(_payload: object) -> int:
        print("Dispatch run" + ("  (dry-run — nothing persisted)" if dry_run else ""))
        print("────────────")
        if dry_run:
            print("  (dry run — nothing persisted)")
        print(f"  Job        : {intake.job.id}")
        print(f"  Intent     : {intake.job.detected_intent}")
        print(f"  Autonomy   : {autonomy}")
        if outcome.route is not None:
            print(f"  Route      : {outcome.route.name} ({outcome.route.id})")
        else:
            print("  Route      : (none selected)")
        print(
            f"  Selection  : {outcome.status}  "
            f"[layer={outcome.precedence_layer}, confidence={outcome.confidence}]"
        )
        if decisions:
            print()
            print("  Decision Inbox:")
            for decision in decisions:
                print(f"    • {decision.id}  →  tokenpak dispatch approve {decision.id}")
        if intake.job.assumptions:
            print("  Assumptions:")
            for assumption in intake.job.assumptions:
                print(f"    - {assumption}")
        if intake.job.missing_info:
            print("  Missing info:")
            for missing in intake.job.missing_info:
                print(f"    - {missing}")
        if dry_run:
            print()
            print(
                "  → Dry-run: draft only. No job, manifest, route, or decision "
                "was written to the ledger."
            )
            return 0
        if outcome.status == "auto_dispatch":
            print()
            print(
                f"  → Route auto-dispatched. Inspect with `tokenpak dispatch status {intake.job.id}`."
            )
        elif outcome.status == "needs_approval":
            print()
            print(
                "  → Route selected but needs approval (autonomy gate). "
                "Confirm or raise autonomy to dispatch."
            )
        elif outcome.status == "refused":
            print()
            print("  → No route was confident enough to dispatch (refused).")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# status / inspect
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_status(args: argparse.Namespace) -> int:
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

    def render(_payload: object) -> int:
        print(f"Job {job.id}")
        print("─" * (4 + len(job.id)))
        print(f"  Status          : {_enum_value(job.status)}")
        if control_state != "active":
            print(f"  Control state   : {control_state}")
        print(f"  Intent          : {job.detected_intent}")
        print(f"  Autonomy        : {_enum_value(job.autonomy_mode)}")
        print(f"  Runs            : {len(runs)}")
        if latest is not None:
            print(f"  Latest run      : {latest['id']} ({latest['status']})")
        print(f"  Pending decisions: {pending}")
        if job.source_task_packet_id:
            print(f"  Task packet     : {job.source_task_packet_id}")
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_inspect(args: argparse.Namespace) -> int:
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
            {"id": d["id"], "status": d["status"], "scope": d["scope"]} for d in decisions
        ],
        "receipts": [r["id"] for r in receipts],
    }
    if not receipts:
        payload["note"] = _ALPHA_PREVIEW_NO_RECEIPT_NOTE
    if include_late:
        payload["late_results"] = [
            {"id": r["id"], "station_run_id": r.get("station_run_id")} for r in late
        ]

    def render(_payload: object) -> int:
        print(f"Inspect job {job.id}")
        print("─" * 40)
        print(f"  Request    : {job.raw_request}")
        print(f"  Intent     : {job.detected_intent}")
        print(f"  Status     : {_enum_value(job.status)}")
        print(f"  Autonomy   : {_enum_value(job.autonomy_mode)}")
        print(f"  Manifests  : {', '.join(_row_text(row, 'id') for row in manifests) or '(none)'}")
        print("  Runs:")
        for run in runs:
            print(f"    - {run['id']} ({run['status']})")
        if not runs:
            print("    (none)")
        print("  Decisions:")
        for decision in decisions:
            print(f"    - {decision['id']} [{decision['scope']}] {decision['status']}")
        if not decisions:
            print("    (none)")
        print(f"  Receipts   : {', '.join(_row_text(row, 'id') for row in receipts) or '(none)'}")
        if not receipts:
            print(f"  Note       : {_ALPHA_PREVIEW_NO_RECEIPT_NOTE}")
        if include_late:
            print("  Late results:")
            for late_result in late:
                print(
                    f"    - {_row_text(late_result, 'id')} "
                    f"(station {late_result.get('station_run_id')})"
                )
            if not late:
                print("    (none)")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# Decision Inbox: decisions / approve / reject
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_decisions(args: argparse.Namespace) -> int:
    as_json = getattr(args, "as_json", False)
    job_filter = getattr(args, "job", None)
    ledger = _ledger()
    try:
        rows = _query_decisions(ledger, job_filter)
        loaded_decisions = [ledger.read_decision(_row_text(row, "id")) for row in rows]
        decisions = [decision for decision in loaded_decisions if decision is not None]
    finally:
        ledger.close()

    cards = [_decision_card(d) for d in decisions]
    payload = {"count": len(cards), "decisions": cards}

    def render(_payload: object) -> int:
        if not cards:
            scope = f" for job {job_filter}" if job_filter else ""
            print(f"Decision Inbox is empty{scope}.")
            return 0
        print(f"Decision Inbox — {len(cards)} decision(s)")
        print("═" * 50)
        for card in cards:
            _print_decision_card(card)
            print()
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_approve(args: argparse.Namespace) -> int:
    return _resolve_decision(args, approve=True)


@_needs_runtime
def cmd_dispatch_reject(args: argparse.Namespace) -> int:
    return _resolve_decision(args, approve=False)


def _resolve_decision(args: argparse.Namespace, *, approve: bool) -> int:
    from tokenpak.orchestration.dispatch.models.enums import DecisionStatus, ResolvedBy

    as_json = getattr(args, "as_json", False)
    verb = "approve" if approve else "reject"
    ledger = _ledger()
    try:
        decision = ledger.read_decision(args.decision_id)
        if decision is None:
            return _err(
                f"no such decision: {args.decision_id}",
                as_json,
                code="decision_not_found",
            )
        if decision.status != DecisionStatus.PENDING:
            return _err(
                f"decision already {_enum_value(decision.status)}: {args.decision_id}",
                as_json,
                code="decision_not_pending",
            )

        if approve:
            option_id = getattr(args, "option", None) or decision.recommendation.option_id
            valid = {o.id for o in decision.options}
            if option_id not in valid:
                return _err(
                    f"unknown option {option_id!r} (valid: {sorted(valid)})",
                    as_json,
                    code="unknown_option",
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

    def render(_payload: object) -> int:
        mark = "✓" if approve else "✗"
        print(f"{mark} Decision {decision.id} {verb}d → {_enum_value(decision.status)}")
        if decision.resolution.selected_option_id:
            print(f"  Selected option: {decision.resolution.selected_option_id}")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# pause / resume / cancel
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_pause(args: argparse.Namespace) -> int:
    return _set_control_state(args, "paused", verb="pause")


@_needs_runtime
def cmd_dispatch_resume(args: argparse.Namespace) -> int:
    return _set_control_state(args, "active", verb="resume")


def _set_control_state(args: argparse.Namespace, control_state: str, *, verb: str) -> int:
    """Record a CLI control state (paused/active) WITHOUT mutating the job's
    canonical ``status`` enum (which has no ``paused`` member). The flag lives in a
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

    def render(_payload: object) -> int:
        print(
            f"Job {args.job_id} {verb}d: control {prior} → {control_state} (status: {job_status})"
        )
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_cancel(args: argparse.Namespace) -> int:
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
            "captured as a LateResult and never applied. No token refund."
        ),
    }

    def render(_payload: object) -> int:
        print(f"Job {args.job_id} cancelled: {prior} → cancelled")
        print(
            "  New stations are prevented from starting. Late TIP output is "
            "captured as a LateResult and never applied. No token refund."
        )
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_discard_late(args: argparse.Namespace) -> int:
    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        rows = _query_rows(
            ledger,
            "SELECT * FROM late_results WHERE station_run_id = ?",
            (args.station_run_id,),
        )
        if not rows:
            return _err(
                f"no late results for station run: {args.station_run_id}",
                as_json,
                code="no_late_results",
            )
        discarded = [_row_text(row, "id") for row in rows]
        for late_id in discarded:
            _delete_row(ledger, "late_results", late_id)
    finally:
        ledger.close()

    payload = {"station_run_id": args.station_run_id, "discarded": discarded}

    def render(_payload: object) -> int:
        print(f"Discarded {len(discarded)} late result(s) for station run {args.station_run_id}:")
        for late_id in discarded:
            print(f"  - {late_id}")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# delivery / receipt  (public-export-eligible → public-safe sanitized)
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_receipt(args: argparse.Namespace) -> int:
    from tokenpak.orchestration.dispatch.public_safe import sanitize_public_obj

    as_json = getattr(args, "as_json", False)
    ledger = _ledger()
    try:
        job = ledger.read_job(args.job_id)
        if job is None:
            return _err(f"no such job: {args.job_id}", as_json, code="job_not_found")
        rows = _query_by_job(ledger, "dispatch_receipts", args.job_id)
        loaded_receipts = [ledger.read_receipt(_row_text(row, "id")) for row in rows]
        receipts = [receipt for receipt in loaded_receipts if receipt is not None]
    finally:
        ledger.close()

    if not receipts:
        return _err(
            f"no receipt for job {args.job_id}. {_ALPHA_PREVIEW_NO_RECEIPT_NOTE}",
            as_json,
            code="no_receipt",
        )

    receipt = receipts[-1]
    raw = json.loads(receipt.model_dump_json())
    payload = cast(_ReceiptPayload, sanitize_public_obj(raw))

    def render(p: _ReceiptPayload) -> int:
        print(f"Receipt {p['id']}")
        print("─" * 40)
        print(f"  Job          : {p['job_id']}")
        print(f"  Run          : {p['run_id']}")
        print(f"  Route        : {p['route_id']}")
        print(f"  Final status : {p['final_status']}")
        print("  Stations:")
        for s in p.get("stations", []):
            print(f"    - {s['station_run_id']} [Worker {s['worker_id']}] {s['status']}")
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
def cmd_dispatch_delivery(args: argparse.Namespace) -> int:
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
    raw: _DeliveryPayload = {
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
    payload = cast(_DeliveryPayload, sanitize_public_obj(raw))

    def render(p: _DeliveryPayload) -> int:
        print(f"Delivery Package — job {p['job_id']}")
        print("─" * 44)
        print(f"  Status   : {p['status']}")
        print(f"  Intent   : {p['intent']}")
        if p["run_id"]:
            print(f"  Run      : {p['run_id']} ({p['run_status']})")
        print(f"  Delivered: {'yes' if p['delivered'] else 'no'}")
        if p["receipt_id"]:
            print(f"  Receipt  : {p['receipt_id']}  (tokenpak dispatch receipt {p['job_id']})")
        print(f"  Summary  : {p['summary']}")
        if p.get("note"):
            print(f"  Note     : {p['note']}")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# Discovery: routes / workers  (read-only registry enumeration)
# ---------------------------------------------------------------------------


@_needs_runtime
def cmd_dispatch_routes(args: argparse.Namespace) -> int:
    """List discoverable Dispatch routes (packaged defaults + user overrides).

    Read-only discovery: enumerates the *merged* route registry (packaged
    defaults shadowed by any user routes under ``<tokenpak-home>/dispatch/routes/``)
    so a tester can find legal route ids/names — plus each route's stations —
    without reading source. Touches no ledger and executes no runtime.
    """
    routes_module = importlib.import_module("tokenpak.orchestration.dispatch.registry.routes")
    merged_route_registry = cast(
        Callable[[], _RouteRegistry],
        getattr(routes_module, "merged_route_registry"),
    )
    user_routes_dir = cast(
        Callable[[], Path],
        getattr(routes_module, "user_routes_dir"),
    )

    as_json = getattr(args, "as_json", False)
    try:
        routes = merged_route_registry().all()
        overrides_dir = str(user_routes_dir())
    except (ValueError, OSError) as exc:
        return _err(
            f"failed to load route registry: {exc}",
            as_json,
            code="route_registry_error",
        )

    route_rows = [
        {
            "id": r.id,
            "name": r.name,
            "intents": list(r.triggers.intents),
            "stations": [
                {
                    "id": s.id,
                    "required_role": s.required_role,
                    "required_capabilities": list(s.required_capabilities),
                }
                for s in r.stations
            ],
        }
        for r in routes
    ]
    payload = {
        "count": len(route_rows),
        "user_routes_dir": overrides_dir,
        "routes": route_rows,
    }

    def render(_payload: object) -> int:
        if not routes:
            print("No Dispatch routes registered.")
        else:
            print(f"Dispatch routes — {len(routes)} registered")
            print("═" * 50)
            for route in routes:
                print(f"  {route.id}   {route.name}")
                print(f"      intents : {', '.join(route.triggers.intents) or '(none)'}")
                station_ids = ", ".join(station.id for station in route.stations) or "(none)"
                print(f"      stations: {station_ids}")
        print()
        print(f"  User route overrides: {overrides_dir}")
        return 0

    return _emit(payload, as_json, render)


@_needs_runtime
def cmd_dispatch_workers(args: argparse.Namespace) -> int:
    """List discoverable Dispatch workers + prompt overlays (packaged + user).

    Read-only discovery: enumerates the packaged worker registry (worker ids,
    roles, capabilities) plus the discoverable prompt overlays (packaged defaults
    shadowed by user overlays under ``<tokenpak-home>/dispatch/overlays/``) so a
    tester can find legal worker ids/capabilities without reading source. Touches
    no ledger and executes no runtime.
    """
    from tokenpak.orchestration.dispatch.registry.workers import (
        WorkerProfileError,
        default_overlay_loader,
        default_worker_registry,
        user_overlay_dir,
    )

    as_json = getattr(args, "as_json", False)
    try:
        workers = default_worker_registry().all()
        overlay_ids = default_overlay_loader().ids()
        overrides_dir = str(user_overlay_dir())
    except (WorkerProfileError, OSError) as exc:
        return _err(
            f"failed to load worker registry: {exc}",
            as_json,
            code="worker_registry_error",
        )

    worker_rows = [
        {
            "id": w.id,
            "name": getattr(w, "name", None),
            "roles": list(w.roles),
            "capabilities": list(w.capabilities),
        }
        for w in workers
    ]
    payload = {
        "count": len(worker_rows),
        "user_overlay_dir": overrides_dir,
        "overlays": list(overlay_ids),
        "workers": worker_rows,
    }

    def render(_payload: object) -> int:
        if not workers:
            print("No Dispatch workers registered.")
        else:
            print(f"Dispatch workers — {len(workers)} registered")
            print("═" * 50)
            for worker in workers:
                print(f"  {worker.id}")
                print(f"      roles       : {', '.join(worker.roles) or '(none)'}")
                print(f"      capabilities: {', '.join(worker.capabilities) or '(none)'}")
        print()
        print(f"  Overlays: {', '.join(overlay_ids) or '(none)'}")
        print(f"  User overlay overrides: {overrides_dir}")
        return 0

    return _emit(payload, as_json, render)


# ---------------------------------------------------------------------------
# Decision card rendering
# ---------------------------------------------------------------------------


def _decision_card(decision: DispatchDecision) -> _DecisionCard:
    return _DecisionCard(
        id=decision.id,
        job_id=decision.job_id,
        scope=_enum_text(decision.scope),
        title=decision.title,
        question=decision.question,
        reason=decision.reason,
        risk_level=_enum_text(decision.risk_level),
        status=_enum_text(decision.status),
        options=[
            _DecisionOptionCard(
                id=option.id,
                label=option.label,
                description=option.description,
                tradeoffs=list(option.tradeoffs),
            )
            for option in decision.options
        ],
        recommendation=_DecisionRecommendationCard(
            option_id=decision.recommendation.option_id,
            rationale=decision.recommendation.rationale,
        ),
        selected_option_id=decision.resolution.selected_option_id,
    )


def _print_decision_card(c: _DecisionCard) -> None:
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
        print(f"    → tokenpak dispatch approve {c['id']}   | tokenpak dispatch reject {c['id']}")


# ---------------------------------------------------------------------------
# Ledger query helpers (read/list/update beyond RunLedger's by-id readers)
# ---------------------------------------------------------------------------


def _row_text(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Dispatch ledger field {key!r} is not text")
    return value


def _conn(ledger: RunLedger) -> sqlite3.Connection:
    """The RunLedger's live connection (read/update for listing + status changes)."""
    return ledger._conn  # noqa: SLF001 — intentional: same-package CLI surface.


def _query_rows(
    ledger: RunLedger,
    sql: str,
    params: tuple[_SqlValue, ...],
) -> list[dict[str, object]]:
    conn = _conn(ledger)
    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [cast(dict[str, object], dict(row)) for row in conn.execute(sql, params)]
    finally:
        conn.row_factory = prior


def _query_by_job(ledger: RunLedger, table: str, job_id: str) -> list[dict[str, object]]:
    return _query_rows(ledger, f"SELECT * FROM {table} WHERE job_id = ?", (job_id,))


def _query_runs_for_job(ledger: RunLedger, job_id: str) -> list[dict[str, object]]:
    return _query_rows(
        ledger,
        "SELECT * FROM dispatch_runs WHERE job_id = ? ORDER BY rowid ASC",
        (job_id,),
    )


def _query_decisions(ledger: RunLedger, job_id: Optional[str]) -> list[dict[str, object]]:
    if job_id:
        return _query_rows(
            ledger,
            "SELECT * FROM dispatch_decisions WHERE job_id = ? ORDER BY rowid ASC",
            (job_id,),
        )
    return _query_rows(ledger, "SELECT * FROM dispatch_decisions ORDER BY rowid ASC", ())


def _count_pending_decisions(ledger: RunLedger, job_id: str) -> int:
    rows = _query_rows(
        ledger,
        "SELECT COUNT(*) AS n FROM dispatch_decisions WHERE job_id = ? AND status = 'pending'",
        (job_id,),
    )
    if not rows:
        return 0
    count = rows[0].get("n")
    return int(count) if isinstance(count, (int, str)) else 0


def _update_job_status_row(ledger: RunLedger, job_id: str, status: str) -> None:
    """Update the job's status column + the persisted payload's status field.

    ``status`` MUST be a valid :class:`DispatchJobStatus` enum value (the payload
    is re-validated on read), so this is only used for real state transitions
    such as ``cancelled``. CLI-only control states (paused) go through
    :func:`_write_control_state`, which never touches the canonical payload.
    """
    conn = _conn(ledger)
    row = _query_rows(ledger, "SELECT payload FROM dispatch_jobs WHERE id = ?", (job_id,))
    if row:
        stored_payload = _row_text(row[0], "payload")
        try:
            decoded = json.loads(stored_payload)
            if not isinstance(decoded, dict):
                raise ValueError("Dispatch job payload is not an object")
            payload = cast(dict[str, object], decoded)
            payload["status"] = status
            new_payload = json.dumps(payload)
        except (ValueError, KeyError):
            new_payload = stored_payload
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


# -- CLI control-state sidecar (pause/resume; not part of the status enum) ---

_CONTROL_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS dispatch_job_control ("
    "job_id TEXT PRIMARY KEY, control_state TEXT, updated_at TEXT)"
)


def _ensure_control_table(ledger: RunLedger) -> None:
    conn = _conn(ledger)
    with conn:
        conn.execute(_CONTROL_TABLE_DDL)


def _write_control_state(ledger: RunLedger, job_id: str, control_state: str) -> None:
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


def _read_control_state(ledger: RunLedger, job_id: str) -> Optional[str]:
    _ensure_control_table(ledger)
    rows = _query_rows(
        ledger,
        "SELECT control_state FROM dispatch_job_control WHERE job_id = ?",
        (job_id,),
    )
    if not rows:
        return None
    control_state = rows[0].get("control_state")
    return control_state if isinstance(control_state, str) else None


def _delete_row(ledger: RunLedger, table: str, row_id: str) -> None:
    conn = _conn(ledger)
    with conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def _enum_value(value: object) -> object:
    """Return ``.value`` for an Enum, else the value itself (str passthrough)."""
    return value.value if isinstance(value, Enum) else value


def _enum_text(value: object) -> str:
    """Return the stable string representation of an enum-backed CLI value."""
    return str(_enum_value(value))


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
    "cmd_dispatch_routes",
    "cmd_dispatch_workers",
]
