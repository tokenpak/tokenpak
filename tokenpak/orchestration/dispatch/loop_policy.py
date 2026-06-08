"""StationLoopPolicy precedence resolution + stop-condition logic (§5.4).

The station runner runs a **bounded loop** per a resolved
:class:`~tokenpak.orchestration.dispatch.models.common.StationLoopPolicy`. This
module owns the two parts of the §5.4 contract that are pure policy (no I/O, no
worker call):

* **Precedence resolution** — :func:`resolve_loop_policy` collapses the four
  policy sources into a single effective policy following the §5.4 precedence
  ``station_override > route_default > worker_default > system_default``. The
  station override (``RouteStation.loop_policy``) wins; absent that, a route
  default (``route_defaults[intent]`` wall-seconds, §5.4 "Route defaults"); absent
  that, the worker default (``DispatchWorker.default_loop_policy``); absent that,
  the §5.4 system default (``StationLoopPolicy()`` field defaults).

* **Stop-condition evaluation** — :func:`evaluate_stop` maps the loop's live
  state onto the §5.4 closed ``stop_when`` enum. The set is EXACT (the round-6
  §4.5 removal of ``station_goal_satisfied`` is honored — there is no such member
  in :class:`LoopStopCondition` and this module never invents one). The returned
  :class:`LoopOutcome` carries the stop condition plus whether the loop produced
  a schema-valid output (the §5.4 ``on_exhausted`` actions are applied by the
  station runner, not here).

System default (§5.4): ``max_iterations: 2, max_tool_calls: 6,
max_wall_seconds: 600`` — these are the :class:`StationLoopPolicy` field
defaults, so :func:`system_default_loop_policy` simply constructs one.

Route defaults (§5.4, round-6 §4.6) are *wall-second* overrides keyed by route
intent: ``quick_answer: 120``, ``doc_task: 900``, ``code_task: 1800``. They are
alpha placeholders (recalibrate before beta from Run Ledger data) and only
override ``max_wall_seconds`` — the iteration / tool-call budgets fall through to
the worker or system default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .models.common import StationLoopPolicy, WorkerLoopDefault
from .models.enums import LoopStopCondition

# ---------------------------------------------------------------------------
# Route wall-second defaults (Standards Delta v0 §5.4, round-6 §4.6)
# ---------------------------------------------------------------------------
#
# status: alpha_placeholder; recalibrate_before: v0.1-beta. These are GUT-FEEL
# wall-second budgets keyed by route intent, transcribed verbatim from §5.4.
# They override ONLY max_wall_seconds; iteration / tool-call budgets fall through
# to the worker/system default. Do not treat any number here as tuned.
ROUTE_WALL_SECOND_DEFAULTS: dict[str, int] = {
    "quick_answer": 120,  # status: alpha_placeholder
    "doc_task": 900,  # status: alpha_placeholder
    "code_task": 1800,  # status: alpha_placeholder
}

ROUTE_DEFAULTS_METADATA: dict[str, str] = {
    "status": "alpha_placeholder",
    "recalibrate_before": "v0.1-beta",
}


def system_default_loop_policy() -> StationLoopPolicy:
    """Return the §5.4 system-default loop policy (2 / 6 / 600).

    These are the :class:`StationLoopPolicy` field defaults, so constructing one
    with no arguments yields the system default. Centralised here so the runner
    never hardcodes the three numbers.
    """

    return StationLoopPolicy()


def resolve_loop_policy(
    *,
    station_override: Optional[StationLoopPolicy] = None,
    worker_default: Optional[WorkerLoopDefault] = None,
    route_intent: Optional[str] = None,
    route_wall_seconds: Optional[Mapping[str, int]] = None,
) -> StationLoopPolicy:
    """Collapse the four policy sources into one effective policy (§5.4 precedence).

    Precedence (highest first): ``station_override`` > route default >
    ``worker_default`` > system default.

    * If ``station_override`` is set it is returned verbatim — a station-level
      policy is fully authoritative (it already carries its own stop_when /
      on_exhausted sets).
    * Otherwise the three integer budgets are resolved field-by-field. The
      iteration / tool-call budgets come from the worker default when present,
      else the system default. The wall-second budget comes from the route
      default for ``route_intent`` when present, else the worker default, else
      the system default.
    * ``stop_when`` / ``on_exhausted`` always come from the system default's full
      closed sets (§5.4): the worker default only specifies the three budget
      integers, and a route wall-second override does not change the stop set.
    """

    if station_override is not None:
        return station_override

    system = system_default_loop_policy()
    route_table = route_wall_seconds if route_wall_seconds is not None else ROUTE_WALL_SECOND_DEFAULTS

    if worker_default is not None:
        max_iterations = worker_default.max_iterations
        max_tool_calls = worker_default.max_tool_calls
        worker_wall = worker_default.max_wall_seconds
    else:
        max_iterations = system.max_iterations
        max_tool_calls = system.max_tool_calls
        worker_wall = system.max_wall_seconds

    if route_intent is not None and route_intent in route_table:
        max_wall_seconds = route_table[route_intent]
    else:
        max_wall_seconds = worker_wall

    return StationLoopPolicy(
        max_iterations=max_iterations,
        max_tool_calls=max_tool_calls,
        max_wall_seconds=max_wall_seconds,
        # The system default carries the full closed stop_when / on_exhausted
        # sets; a budget-only worker/route override never narrows them.
        stop_when=list(system.stop_when),
        on_exhausted=list(system.on_exhausted),
    )


# ---------------------------------------------------------------------------
# Stop-condition evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopState:
    """Live state of a station loop, evaluated against the policy each iteration.

    All counters are *post-iteration* values (i.e. after the iteration that just
    ran). ``output_schema_valid`` is True once the worker produced a schema-valid
    result; ``pending_tool_requests`` is True when the worker asked for a tool
    call that has not yet been satisfied (the loop must iterate again to run it).
    """

    iteration_count: int
    tool_call_count: int
    wall_seconds: int
    output_schema_valid: bool
    pending_tool_requests: bool
    cancel_requested: bool = False
    tool_policy_violation: bool = False
    fatal_error: bool = False


@dataclass(frozen=True)
class LoopOutcome:
    """The resolved §5.4 stop condition for a loop, or ``None`` to keep looping.

    ``stop_condition`` is ``None`` while the loop should continue, otherwise the
    exact :class:`LoopStopCondition` that fired. ``exhausted`` is True only for
    the ``loop_budget_exhausted`` condition (it drives the §5.4 ``on_exhausted``
    actions in the station runner). ``produced_valid_output`` mirrors the loop
    state's ``output_schema_valid`` for the runner's convenience.
    """

    stop_condition: Optional[LoopStopCondition]
    exhausted: bool
    produced_valid_output: bool

    @property
    def should_stop(self) -> bool:
        return self.stop_condition is not None


def evaluate_stop(state: LoopState, policy: StationLoopPolicy) -> LoopOutcome:
    """Map loop ``state`` onto the §5.4 closed ``stop_when`` enum.

    Evaluation order is the contract — the highest-severity reasons win so the
    runner records the most specific stop cause:

    1. ``cancel_requested`` — cancellation propagates immediately (§5.6).
    2. ``fatal_error`` — an unrecoverable error this iteration.
    3. ``tool_policy_violation`` — a denied/over-budget tool call.
    4. ``output_schema_valid AND no_pending_tool_requests`` — the success exit.
    5. ``loop_budget_exhausted`` — any of the three budgets reached/exceeded.

    Returns a :class:`LoopOutcome` whose ``stop_condition`` is ``None`` only when
    none of the closed conditions hold (the loop should run another iteration).
    The §4.5-removed ``station_goal_satisfied`` is never produced.
    """

    # 1. Cancellation (§5.6) — propagates before anything else.
    if state.cancel_requested:
        return LoopOutcome(
            stop_condition=LoopStopCondition.CANCEL_REQUESTED,
            exhausted=False,
            produced_valid_output=state.output_schema_valid,
        )

    # 2. Fatal error.
    if state.fatal_error:
        return LoopOutcome(
            stop_condition=LoopStopCondition.FATAL_ERROR,
            exhausted=False,
            produced_valid_output=state.output_schema_valid,
        )

    # 3. Tool policy violation.
    if state.tool_policy_violation:
        return LoopOutcome(
            stop_condition=LoopStopCondition.TOOL_POLICY_VIOLATION,
            exhausted=False,
            produced_valid_output=state.output_schema_valid,
        )

    # 4. Success exit: schema-valid output AND nothing left to run.
    if state.output_schema_valid and not state.pending_tool_requests:
        return LoopOutcome(
            stop_condition=LoopStopCondition.OUTPUT_SCHEMA_VALID_AND_NO_PENDING_TOOL_REQUESTS,
            exhausted=False,
            produced_valid_output=True,
        )

    # 5. Budget exhaustion — any ceiling reached.
    if (
        state.iteration_count >= policy.max_iterations
        or state.tool_call_count >= policy.max_tool_calls
        or state.wall_seconds >= policy.max_wall_seconds
    ):
        return LoopOutcome(
            stop_condition=LoopStopCondition.LOOP_BUDGET_EXHAUSTED,
            exhausted=True,
            produced_valid_output=state.output_schema_valid,
        )

    # No closed condition holds → keep looping.
    return LoopOutcome(
        stop_condition=None,
        exhausted=False,
        produced_valid_output=state.output_schema_valid,
    )


__all__ = [
    "ROUTE_WALL_SECOND_DEFAULTS",
    "ROUTE_DEFAULTS_METADATA",
    "system_default_loop_policy",
    "resolve_loop_policy",
    "LoopState",
    "LoopOutcome",
    "evaluate_stop",
]
