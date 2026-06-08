"""Tests for the Dispatch runtime + deterministic route selection (P-RUNTIME-01).

Covers Standards Delta v0 §5.8 (precedence + alpha scoring + confidence
thresholds), §4.3 / §16 (route registry + station binding), and §11 (dynamic
worker/route binding by capability intersection):

  * route registry load of the 3 packaged routes, with station shapes asserted;
  * dynamic capability binding — a route binds when a worker has the required
    caps, and fails / is penalized when none do (NOT by hardcoded worker id);
  * each precedence layer exercised (explicit route → project rule → exact
    route_trigger → intent classification → registry tie-break → decision);
  * confidence-threshold behavior (>=60 auto-dispatch, 40-59 decision, <40 refuse);
  * alpha-scoring sanity + the alpha_placeholder / recalibrate_before tagging;
  * the LLM-suggestion path with a mock TipClient — schema-bound, advisory only,
    and provably unable to dispatch on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Dispatch is pydantic-native; deps ship via the opt-in `dispatch` extra. PyYAML
# is a hard dependency of the route/worker loaders.
pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from tokenpak.orchestration.dispatch import dispatch as rt_mod
from tokenpak.orchestration.dispatch.dispatch import (
    DISPATCH_SCORING_METADATA,
    THRESHOLD_AUTO_DISPATCH,
    THRESHOLD_DECISION_FLOOR,
    DispatchRuntime,
    InvalidRouteSuggestion,
    ProjectRules,
    RouteScore,
    RouteSuggester,
    RouteSuggestion,
    score_route,
)
from tokenpak.orchestration.dispatch.frontdock import FrontDock
from tokenpak.orchestration.dispatch.models.enums import (
    AutonomyMode,
    DecisionStatus,
    RiskLevel,
)
from tokenpak.orchestration.dispatch.models.route import (
    DispatchRoute,
    RouteStation,
    RouteTriggers,
)
from tokenpak.orchestration.dispatch.registry.routes import (
    DispatchRouteRegistry,
    RouteProfileError,
    RouteResolutionError,
    bind_route,
    default_route_registry,
    is_worker_station,
    resolve_station_workers,
    route_is_bindable,
)
from tokenpak.orchestration.dispatch.registry.workers import (
    DispatchWorkerRegistry,
    default_worker_registry,
)

_PACKAGED_ROUTES_DIR = (
    Path(rt_mod.__file__).resolve().parent / "registry" / "routes"
)


# ---------------------------------------------------------------------------
# Route registry — packaged profiles
# ---------------------------------------------------------------------------


def test_route_registry_loads_three_packaged_routes():
    reg = default_route_registry()
    assert reg.ids() == [
        "route.code_task.v1",
        "route.doc_task.v1",
        "route.quick_answer.v1",
    ]


def test_code_task_route_shape():
    route = default_route_registry().get("route.code_task.v1")
    assert route.triggers.intents == ["code_task"]
    assert route.default_risk == RiskLevel.MEDIUM
    station_ids = [s.id for s in route.stations]
    assert station_ids == ["build", "review"]
    build = route.stations[0]
    assert build.required_role == "builder"
    assert build.prompt_overlay == "overlay.code_builder.v1"
    assert set(build.required_capabilities) == {"code_drafting", "patch_generation"}
    review = route.stations[1]
    assert review.required_role == "reviewer"
    assert review.required_capabilities == ["semantic_review"]


def test_quick_answer_route_is_single_builder_station():
    route = default_route_registry().get("route.quick_answer.v1")
    assert [s.id for s in route.stations] == ["answer"]
    assert route.stations[0].required_role == "builder"
    assert route.stations[0].required_capabilities == ["answer_generation"]
    assert route.default_risk == RiskLevel.LOW


def test_route_registry_for_intent_lookup():
    reg = default_route_registry()
    assert [r.id for r in reg.for_intent("code_task")] == ["route.code_task.v1"]
    assert [r.id for r in reg.for_intent("quick_answer")] == ["route.quick_answer.v1"]
    assert reg.for_intent("nonexistent_intent") == []


def test_route_registry_rejects_unknown_capability_fail_loud(tmp_path):
    """A route declaring an unknown station capability is rejected at load (§5.2)."""

    bad = tmp_path / "routes"
    bad.mkdir()
    (bad / "route.rogue.v1.yaml").write_text(
        "id: route.rogue.v1\n"
        "name: Rogue\n"
        "description: bad route\n"
        "default_risk: low\n"
        "triggers:\n"
        "  intents: [code_task]\n"
        "stations:\n"
        "  - id: build\n"
        "    required_role: builder\n"
        "    required_capabilities: [exfiltrate_secrets]\n"
        "    output_schema: station_result.v1\n"
    )
    with pytest.raises(RouteProfileError) as exc:
        DispatchRouteRegistry.from_dir(bad)
    assert "exfiltrate_secrets" in str(exc.value)


def test_route_registry_rejects_duplicate_id(tmp_path):
    d = tmp_path / "routes"
    d.mkdir()
    body = (
        "id: route.dup.v1\n"
        "name: Dup\n"
        "description: d\n"
        "default_risk: low\n"
        "triggers:\n"
        "  intents: [quick_answer]\n"
        "stations:\n"
        "  - id: answer\n"
        "    required_role: builder\n"
        "    required_capabilities: [answer_generation]\n"
        "    output_schema: station_result.v1\n"
    )
    (d / "route.a.yaml").write_text(body)
    (d / "route.b.yaml").write_text(body)
    with pytest.raises(RouteProfileError):
        DispatchRouteRegistry.from_dir(d)


# ---------------------------------------------------------------------------
# Dynamic capability binding (§11) — NOT hardcoded worker ids
# ---------------------------------------------------------------------------


def test_station_binds_to_worker_by_capability_intersection():
    routes = default_route_registry()
    workers = default_worker_registry()
    build = routes.get("route.code_task.v1").stations[0]
    eligible = resolve_station_workers(build, workers)
    # The builder is resolved dynamically because it HAS code_drafting +
    # patch_generation, not because the route names its id.
    assert [w.id for w in eligible] == ["worker.builder.default.v1"]


def test_bind_route_returns_per_station_worker_lists():
    routes = default_route_registry()
    workers = default_worker_registry()
    bindings = bind_route(routes.get("route.code_task.v1"), workers)
    assert {k: [w.id for w in v] for k, v in bindings.items()} == {
        "build": ["worker.builder.default.v1"],
        "review": ["worker.reviewer.default.v1"],
    }


def test_route_fails_to_bind_when_no_worker_has_capabilities():
    """A station whose required caps no worker satisfies fails loud (§11)."""

    routes = default_route_registry()
    # A worker registry with only a reviewer (no builder) cannot staff a
    # builder station — binding fails, by capability/role, never by id.
    reviewer_only = DispatchWorkerRegistry(
        {"worker.reviewer.default.v1": default_worker_registry().get(
            "worker.reviewer.default.v1"
        )}
    )
    code_route = routes.get("route.code_task.v1")
    assert route_is_bindable(code_route, reviewer_only) is False
    with pytest.raises(RouteResolutionError) as exc:
        bind_route(code_route, reviewer_only)
    assert exc.value.station_id == "build"
    assert "builder" in exc.value.reason


def test_route_fails_to_bind_when_role_present_but_caps_missing():
    """Role matches but a station capability is absent → still fails (§16)."""

    workers = default_worker_registry()
    # A custom route whose build station demands a capability the builder lacks.
    route = DispatchRoute(
        id="route.custom.v1",
        name="Custom",
        description="needs a cap the builder lacks",
        triggers=RouteTriggers(intents=["code_task"]),
        default_risk=RiskLevel.MEDIUM,
        stations=[
            RouteStation(
                id="build",
                required_role="builder",
                required_capabilities=["semantic_review"],  # builder lacks this
                output_schema="station_result.v1",
            )
        ],
    )
    assert resolve_station_workers(route.stations[0], workers) == []
    with pytest.raises(RouteResolutionError) as exc:
        bind_route(route, workers)
    assert "semantic_review" in exc.value.reason


def test_system_component_station_is_not_worker_bound():
    station = RouteStation(
        id="deliver",
        system_component="delivery_dock",
        output_schema="delivery.v1",
    )
    assert is_worker_station(station) is False
    assert resolve_station_workers(station, default_worker_registry()) == []


# ---------------------------------------------------------------------------
# Alpha scoring sanity + tagging
# ---------------------------------------------------------------------------


def test_scoring_metadata_is_tagged_alpha_placeholder():
    assert DISPATCH_SCORING_METADATA["status"] == "alpha_placeholder"
    assert DISPATCH_SCORING_METADATA["recalibrate_before"] == "v0.1-beta"


def test_thresholds_match_standards_delta():
    assert THRESHOLD_AUTO_DISPATCH == 60
    assert THRESHOLD_DECISION_FLOOR == 40


def test_score_route_matching_intent_scores_high():
    intake = FrontDock().intake("Refactor the parser function and add tests")
    route = default_route_registry().get("route.code_task.v1")
    score = score_route(route, intake.job, default_worker_registry())
    # exact_route_trigger_match (+40) + intent_match (+25) dominate; the job has
    # no material missing info and the autonomy default permits dispatch.
    assert score.score >= THRESHOLD_AUTO_DISPATCH
    names = [name for name, _ in score.components]
    assert "exact_route_trigger_match" in names
    assert "intent_match" in names


def test_score_route_unbindable_gets_forbidden_penalty():
    intake = FrontDock().intake("Refactor the parser function")
    route = default_route_registry().get("route.code_task.v1")
    reviewer_only = DispatchWorkerRegistry(
        {"worker.reviewer.default.v1": default_worker_registry().get(
            "worker.reviewer.default.v1"
        )}
    )
    score = score_route(route, intake.job, reviewer_only)
    names = [name for name, _ in score.components]
    assert "forbidden_action_required" in names
    assert score.bindable is False
    # The -100 forbidden penalty drives confidence below the refuse floor.
    assert score.confidence < THRESHOLD_DECISION_FLOOR


def test_score_route_material_missing_info_penalized_not_soft_probes():
    job = FrontDock().intake("Refactor the parser function").job  # soft probes only
    route = default_route_registry().get("route.code_task.v1")
    workers = default_worker_registry()
    # Soft probe gaps are present on the job but must NOT trigger the penalty.
    assert job.missing_info  # FrontDock populated soft probes
    soft = score_route(route, job, workers, has_material_missing_info=False)
    material = score_route(route, job, workers, has_material_missing_info=True)
    soft_names = [n for n, _ in soft.components]
    material_names = [n for n, _ in material.components]
    assert "missing_required_info" not in soft_names
    assert "missing_required_info" in material_names
    assert material.score < soft.score


# ---------------------------------------------------------------------------
# Precedence layer 1 — explicit user route
# ---------------------------------------------------------------------------


def test_explicit_route_bare_name_selected():
    intake = FrontDock().intake("What is a tuple?")  # would auto-route to quick_answer
    out = DispatchRuntime().select_route(intake, explicit_route="code_task")
    assert out.precedence_layer == "explicit_route"
    assert out.route.id == "route.code_task.v1"
    assert out.status == "auto_dispatch"
    assert out.confidence == 100


def test_explicit_route_full_id_selected():
    intake = FrontDock().intake("anything")
    out = DispatchRuntime().select_route(intake, explicit_route="route.doc_task.v1")
    assert out.route.id == "route.doc_task.v1"
    assert out.precedence_layer == "explicit_route"


def test_explicit_unknown_route_yields_decision():
    intake = FrontDock().intake("What is a tuple?")
    out = DispatchRuntime().select_route(intake, explicit_route="not_a_route")
    assert out.status == "decision"
    assert out.decision is not None
    assert "not a known route" in out.decision.question


def test_explicit_route_unstaffable_refused():
    intake = FrontDock().intake("What is a tuple?")
    reviewer_only = DispatchWorkerRegistry(
        {"worker.reviewer.default.v1": default_worker_registry().get(
            "worker.reviewer.default.v1"
        )}
    )
    rt = DispatchRuntime(worker_registry=reviewer_only)
    out = rt.select_route(intake, explicit_route="code_task")
    assert out.status == "refused"
    assert out.precedence_layer == "explicit_route"


# ---------------------------------------------------------------------------
# Precedence layer 2 — project rule
# ---------------------------------------------------------------------------


def test_project_rule_forces_route_over_intent():
    intake = FrontDock().intake("What is a tuple?")  # intent = quick_answer
    rules = ProjectRules(intent_routes={"quick_answer": "route.doc_task.v1"})
    out = DispatchRuntime().select_route(intake, project_rules=rules)
    assert out.precedence_layer == "project_rule"
    assert out.route.id == "route.doc_task.v1"


def test_empty_project_rules_are_a_noop():
    intake = FrontDock().intake("What is a tuple?")
    out = DispatchRuntime().select_route(intake, project_rules=ProjectRules())
    # Falls through to trigger match, not the project-rule layer.
    assert out.precedence_layer == "exact_route_trigger_match"
    assert out.route.id == "route.quick_answer.v1"


def test_explicit_route_outranks_project_rule():
    intake = FrontDock().intake("What is a tuple?")
    rules = ProjectRules(intent_routes={"quick_answer": "route.doc_task.v1"})
    out = DispatchRuntime().select_route(
        intake, explicit_route="code_task", project_rules=rules
    )
    assert out.precedence_layer == "explicit_route"
    assert out.route.id == "route.code_task.v1"


# ---------------------------------------------------------------------------
# Precedence layer 3 — exact route_trigger match
# ---------------------------------------------------------------------------


def test_exact_trigger_match_selects_route():
    intake = FrontDock().intake("Refactor the parser function")
    out = DispatchRuntime().select_route(intake)
    assert out.precedence_layer == "exact_route_trigger_match"
    assert out.route.id == "route.code_task.v1"


# ---------------------------------------------------------------------------
# Precedence layer 4 — intent classification (>1 trigger route)
# ---------------------------------------------------------------------------


def test_intent_classification_ambiguous_id_match_falls_through_to_tie_break():
    """Two id-encoded matches are NOT a unique layer-4 match → fall through.

    When two routes both encode the intent in their id, layer 4's uniqueness
    guard does not fire; the runtime falls through to the registry tie-break
    (layer 5) / decision (layer 6) rather than spuriously picking one.
    """

    routes = default_route_registry()
    # Second route declares the SAME intent AND also contains "code_task" in its
    # id, so neither layer 3 (unique trigger) nor layer 4 (unique id-encoded
    # match) can decide — the runtime must not arbitrarily choose.
    second = DispatchRoute(
        id="route.code_task_alt.v1",
        name="Code Task Alt",
        description="alt",
        triggers=RouteTriggers(intents=["code_task"]),
        default_risk=RiskLevel.MEDIUM,
        stations=[
            RouteStation(
                id="build",
                required_role="builder",
                required_capabilities=["code_drafting"],
                output_schema="station_result.v1",
            )
        ],
    )
    merged = DispatchRouteRegistry(
        {r.id: r for r in routes.all()} | {second.id: second}
    )
    rt = DispatchRuntime(route_registry=merged)
    intake = FrontDock().intake("Refactor the parser function")
    out = rt.select_route(intake)
    # Two equally-scored code_task routes tie → a decision, never a silent pick.
    assert out.precedence_layer in {"registry_tie_break", "dispatch_decision"}
    assert out.status == "decision"
    assert out.decision is not None


def test_intent_classification_unique_match_selected():
    routes = default_route_registry()
    # Second route shares the intent but its id does NOT contain the intent
    # string, so exactly one trigger route is id-encoded -> layer 4 selects it.
    other = DispatchRoute(
        id="route.generic_builder.v1",
        name="Generic",
        description="shares intent, id has no intent substring",
        triggers=RouteTriggers(intents=["code_task"]),
        default_risk=RiskLevel.MEDIUM,
        stations=[
            RouteStation(
                id="build",
                required_role="builder",
                required_capabilities=["code_drafting"],
                output_schema="station_result.v1",
            )
        ],
    )
    merged = DispatchRouteRegistry(
        {r.id: r for r in routes.all()} | {other.id: other}
    )
    rt = DispatchRuntime(route_registry=merged)
    intake = FrontDock().intake("Refactor the parser function")
    out = rt.select_route(intake)
    assert out.precedence_layer == "intent_classification"
    assert out.route.id == "route.code_task.v1"


# ---------------------------------------------------------------------------
# Precedence layer 5/6 — registry tie-break + decision
# ---------------------------------------------------------------------------


def test_unknown_intent_yields_decision():
    intake = FrontDock().intake("xyzzy plugh")  # no keyword match -> unknown
    assert intake.job.detected_intent == "unknown"
    out = DispatchRuntime().select_route(intake)
    assert out.status == "decision"
    assert out.decision is not None
    assert out.decision.status == DecisionStatus.PENDING
    # The decision offers every registered route + cancel.
    option_ids = [o.id for o in out.decision.options]
    assert "route.code_task.v1" in option_ids
    assert "cancel" in option_ids


# ---------------------------------------------------------------------------
# Confidence thresholds (§5.8)
# ---------------------------------------------------------------------------


def test_high_confidence_auto_dispatches_when_autonomy_permits():
    intake = FrontDock().intake(
        "Refactor the parser function",
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )
    out = DispatchRuntime().select_route(intake)
    assert out.confidence >= THRESHOLD_AUTO_DISPATCH
    assert out.status == "auto_dispatch"
    # The chosen route is fully bound (stations staffed) on auto-dispatch.
    assert set(out.bindings) == {"build", "review"}


def test_high_confidence_needs_approval_under_advisory_autonomy():
    intake = FrontDock().intake(
        "Refactor the parser function", autonomy_mode=AutonomyMode.ADVISORY
    )
    out = DispatchRuntime().select_route(intake)
    assert out.confidence >= THRESHOLD_AUTO_DISPATCH
    # advisory never auto-dispatches: route is chosen but held for approval.
    assert out.status == "needs_approval"


def test_mid_band_confidence_creates_decision(monkeypatch):
    """A 40-59 score produces a DispatchDecision, not an auto-dispatch."""

    intake = FrontDock().intake("Refactor the parser function")
    route = default_route_registry().get("route.code_task.v1")

    # Force the scorer to return a mid-band score for this route.
    def _mid_score(r, job, workers, *, suggestion=None, has_material_missing_info=False):
        return RouteScore(route_id=r.id, score=50, components=(("x", 50),), bindable=True)

    monkeypatch.setattr(rt_mod, "score_route", _mid_score)
    out = DispatchRuntime().select_route(intake)
    assert out.confidence == 50
    assert out.status == "decision"
    assert out.decision is not None
    # The mid-band decision recommends the scored route.
    assert out.decision.recommendation.option_id == route.id


def test_low_confidence_refuses(monkeypatch):
    intake = FrontDock().intake("Refactor the parser function")

    def _low_score(r, job, workers, *, suggestion=None, has_material_missing_info=False):
        return RouteScore(route_id=r.id, score=10, components=(("x", 10),), bindable=True)

    monkeypatch.setattr(rt_mod, "score_route", _low_score)
    out = DispatchRuntime().select_route(intake)
    assert out.confidence == 10
    assert out.status == "refused"
    assert out.is_refused


# ---------------------------------------------------------------------------
# LLM suggestion path — schema-bound, advisory only, never dispatches
# ---------------------------------------------------------------------------


class _MockSuggestClient:
    """Deterministic mock route-suggest client (the TIP boundary in tests)."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def suggest_route(self, request, candidate_route_ids):
        self.calls.append((request, tuple(candidate_route_ids)))
        return self.payload


def test_route_suggestion_schema_gate_accepts_valid_payload():
    s = RouteSuggestion.from_payload(
        {
            "route_id": "route.code_task.v1",
            "confidence": 72,
            "reasons": ["mentions a patch"],
            "missing_info": [],
            "risk_flags": ["touches_cli"],
        }
    )
    assert s.route_id == "route.code_task.v1"
    assert s.confidence == 72
    assert s.reasons == ("mentions a patch",)
    assert s.risk_flags == ("touches_cli",)


@pytest.mark.parametrize(
    "payload",
    [
        {"confidence": 50},  # missing route_id
        {"route_id": "", "confidence": 50},  # empty route_id
        {"route_id": "route.x.v1", "confidence": "high"},  # non-numeric confidence
        {"route_id": "route.x.v1", "confidence": 250},  # out of range
        {"route_id": "route.x.v1", "confidence": True},  # bool is not a number
    ],
)
def test_route_suggestion_schema_gate_rejects_bad_payload(payload):
    with pytest.raises(InvalidRouteSuggestion):
        RouteSuggestion.from_payload(payload)


def test_suggester_discards_out_of_vocabulary_route():
    client = _MockSuggestClient({"route_id": "route.invented.v9", "confidence": 99})
    suggester = RouteSuggester(client)
    # The suggested route is not among the candidates -> discarded (None).
    assert suggester.suggest("req", ["route.code_task.v1"]) is None


def test_suggester_returns_none_without_client():
    assert RouteSuggester(None).suggest("req", ["route.code_task.v1"]) is None


def test_suggester_survives_client_exception():
    class _Boom:
        def suggest_route(self, request, candidate_route_ids):
            raise RuntimeError("provider down")

    assert RouteSuggester(_Boom()).suggest("req", ["route.code_task.v1"]) is None


def test_llm_suggestion_is_advisory_and_cannot_dispatch_alone():
    """An LLM suggestion nudges the score but the deterministic threshold rules.

    For an unknown intent (no deterministic route match), even a high-confidence
    LLM suggestion only adds the file_context_hint nudge (+15) — not enough to
    clear the auto-dispatch threshold. The LLM never directly dispatches.
    """

    intake = FrontDock().intake("xyzzy plugh")  # unknown intent
    client = _MockSuggestClient(
        {"route_id": "route.quick_answer.v1", "confidence": 95}
    )
    rt = DispatchRuntime(suggester=RouteSuggester(client))
    out = rt.select_route(intake)
    # The suggester WAS consulted...
    assert client.calls
    # ...but the deterministic layer did not auto-dispatch on the LLM's word.
    assert out.status != "auto_dispatch"


def test_llm_suggestion_corroborates_deterministic_choice():
    """When the LLM agrees with a deterministic route, it adds a positive nudge."""

    intake = FrontDock().intake("Refactor the parser function")
    client = _MockSuggestClient(
        {"route_id": "route.code_task.v1", "confidence": 80}
    )
    rt = DispatchRuntime(suggester=RouteSuggester(client))
    out = rt.select_route(intake)
    assert out.route.id == "route.code_task.v1"
    assert out.status == "auto_dispatch"
    # The file_context_hint nudge from the corroborating suggestion is recorded.
    assert any("file_context_hint_match" in r for r in out.reasons)
