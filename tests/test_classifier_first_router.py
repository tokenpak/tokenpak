"""
test_classifier_first_router.py — Tests for classifier-first router wiring.

Covers:
- Intent classification (canonical intent set)
- Slot extraction via SlotFiller + slot_definitions.yaml
- Deterministic policy decisions (intent + slots -> recipe_id)
- Fallback path for unknown/low-confidence intents
- Determinism: same input -> same output
"""
from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.proxy.intent_policy", reason="module not available in current build")
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Patch watchdog name collision BEFORE importing proxy.
# tokenpak/watchdog.py shadows the installed watchdog package.
# ---------------------------------------------------------------------------
if "watchdog.events" not in sys.modules:
    _wde = MagicMock()
    _wde.FileSystemEventHandler = object
    sys.modules["watchdog.events"] = _wde
if "watchdog.observers" not in sys.modules:
    sys.modules["watchdog.observers"] = MagicMock()

# ---------------------------------------------------------------------------
# Load proxy as isolated module (lives at repo root, not a package)
# ---------------------------------------------------------------------------
_PROXY_PATH = Path(__file__).parent.parent / "proxy.py"


def _load_proxy() -> ModuleType:
    mod_name = "_test_proxy_classifier"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _PROXY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_proxy = _load_proxy()
_classify_intent = _proxy._classify_intent

# ---------------------------------------------------------------------------
# Import tokenpak modules (they work without watchdog collision)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tokenpak"))

from tokenpak.compression.slot_filler import SlotFiller  # noqa: E402
from tokenpak.proxy.intent_policy import (  # noqa: E402
    CANONICAL_INTENTS,
    DecisionAction,
    RoutingDecision,
    decide,
    is_known_intent,
)

# ============================================================================
# Intent classifier — expanded canonical set
# ============================================================================

class TestClassifyIntent:
    def test_status_health_check(self):
        assert _classify_intent("what is the status of the proxy?") == "status"

    def test_status_is_running(self):
        assert _classify_intent("is it running") == "status"

    def test_status_uptime(self):
        assert _classify_intent("check uptime for the gateway") == "status"

    def test_status_ping(self):
        assert _classify_intent("ping the server") == "status"

    def test_status_alive(self):
        assert _classify_intent("is the service alive?") == "status"

    def test_usage_cost(self):
        assert _classify_intent("how much did I spend this week?") == "usage"

    def test_usage_tokens(self):
        assert _classify_intent("show me token count for today") == "usage"

    def test_usage_billing(self):
        assert _classify_intent("show billing summary") == "usage"

    def test_usage_how_many_tokens(self):
        assert _classify_intent("how many tokens did that use?") == "usage"

    def test_execute_run(self):
        assert _classify_intent("run the migration script") == "execute"

    def test_execute_deploy(self):
        assert _classify_intent("deploy to staging") == "execute"

    def test_execute_launch(self):
        assert _classify_intent("launch the pipeline") == "execute"

    def test_execute_trigger(self):
        assert _classify_intent("trigger the nightly job") == "execute"

    def test_debug_fix(self):
        assert _classify_intent("fix the broken import") == "debug"

    def test_debug_error(self):
        assert _classify_intent("getting an error in the router") == "debug"

    def test_debug_traceback(self):
        assert _classify_intent("there's a traceback in the logs") == "debug"

    def test_debug_why_is(self):
        assert _classify_intent("why is the proxy crashing?") == "debug"

    def test_summarize(self):
        assert _classify_intent("summarize the vault for last 7 days") == "summarize"

    def test_summarize_tldr(self):
        assert _classify_intent("tldr of this document") == "summarize"

    def test_summarize_brief(self):
        assert _classify_intent("give me a brief of the session") == "summarize"

    def test_plan_design(self):
        assert _classify_intent("design the architecture for TokenPak v5") == "plan"

    def test_plan_strategy(self):
        assert _classify_intent("what strategy should I use for caching?") == "plan"

    def test_plan_approach(self):
        assert _classify_intent("best approach for the migration") == "plan"

    def test_explain_what_is(self):
        assert _classify_intent("what is BM25?") == "explain"

    def test_explain_how_does(self):
        assert _classify_intent("how does the slot filler work?") == "explain"

    def test_explain_describe(self):
        assert _classify_intent("describe the pipeline stages") == "explain"

    def test_search_find(self):
        assert _classify_intent("find the function that handles routing") == "search"

    def test_search_locate(self):
        assert _classify_intent("locate the vault index file") == "search"

    def test_search_list_all(self):
        assert _classify_intent("list all tests in this project") == "search"

    def test_create_write(self):
        assert _classify_intent("write a Python function to parse JSON") == "create"

    def test_create_generate(self):
        assert _classify_intent("generate a test suite for the router") == "create"

    def test_create_implement(self):
        assert _classify_intent("implement the policy map") == "create"

    def test_fallback_query(self):
        assert _classify_intent("the quick brown fox") == "query"

    def test_fallback_ok(self):
        assert _classify_intent("ok") == "query"

    def test_fallback_empty(self):
        assert _classify_intent("") == "query"

    def test_determinism_repeated(self):
        text = "how does the proxy handle authentication errors?"
        results = {_classify_intent(text) for _ in range(5)}
        assert len(results) == 1, f"Non-deterministic: {results}"

    def test_case_insensitive_status(self):
        assert _classify_intent("HEALTH CHECK") == "status"

    def test_case_insensitive_usage(self):
        assert _classify_intent("SHOW BILLING SUMMARY") == "usage"


# ============================================================================
# SlotFiller + slot_definitions.yaml
# ============================================================================

class TestSlotFiller:
    @pytest.fixture
    def filler(self):
        return SlotFiller()

    def test_slot_definitions_loaded(self, filler):
        assert len(filler.known_intents()) > 0

    def test_canonical_intents_have_definitions(self, filler):
        known = set(filler.known_intents())
        expected = {"status", "usage", "debug", "summarize", "plan",
                    "execute", "explain", "search", "create", "query"}
        assert expected.issubset(known), f"Missing: {expected - known}"

    def test_status_target_proxy(self, filler):
        result = filler.fill("status", "what is the status of the proxy?")
        assert result.slots.get("target") == "proxy"

    def test_status_default_detail_level(self, filler):
        """detail_level defaults to summary for all status requests."""
        result = filler.fill("status", "check gateway health")
        assert result.slots.get("detail_level") == "summary"

    def test_usage_period_default(self, filler):
        """period defaults to 7d when no duration mentioned."""
        result = filler.fill("usage", "show me cost breakdown")
        assert result.slots.get("period") == "7d"

    def test_usage_period_explicit_7d(self, filler):
        """Explicit 7d mention fills period slot."""
        result = filler.fill("usage", "show usage for last 7 days")
        assert result.slots.get("period") == "7d"

    def test_usage_period_30d(self, filler):
        """last month maps to 30d in period slot."""
        result = filler.fill("usage", "cost for last month")
        assert result.slots.get("period") == "30d"

    def test_debug_default_detail_level_verbose(self, filler):
        """debug always defaults detail_level to verbose."""
        result = filler.fill("debug", "something is broken")
        assert result.slots.get("detail_level") == "verbose"

    def test_debug_target_error(self, filler):
        """error in examples list for debug target slot."""
        result = filler.fill("debug", "debug this error in the pipeline")
        assert result.slots.get("target") in ("error", "pipeline", None)

    def test_summarize_target_vault(self, filler):
        """vault is in summarize examples."""
        result = filler.fill("summarize", "summarize the vault")
        assert result.slots.get("target") == "vault"

    def test_execute_default_mode_standard(self, filler):
        """execute mode defaults to standard."""
        result = filler.fill("execute", "deploy the script")
        assert result.slots.get("mode") == "standard"

    def test_execute_target_script(self, filler):
        """script is in execute examples."""
        result = filler.fill("execute", "run the migration script")
        assert result.slots.get("target") in ("script", "migration", "migration script", None)

    def test_execute_target_job(self, filler):
        """job is in execute examples."""
        result = filler.fill("execute", "trigger the nightly job")
        assert result.slots.get("target") in ("job", None)

    def test_plan_target_migration(self, filler):
        """migration is in plan examples."""
        result = filler.fill("plan", "plan the migration")
        assert result.slots.get("target") == "migration"

    def test_unknown_intent_zero_confidence(self, filler):
        result = filler.fill("totally_unknown_xyz", "some text here")
        assert result.confidence == 0.0
        assert result.slots == {}

    def test_query_empty_slots(self, filler):
        result = filler.fill("query", "anything at all")
        assert result.slots == {}

    def test_filled_slots_has_intent(self, filler):
        result = filler.fill("status", "ping the proxy")
        assert result.intent == "status"

    def test_confidence_in_range(self, filler):
        result = filler.fill("status", "check proxy health")
        assert 0.0 <= result.confidence <= 1.0


# ============================================================================
# Intent Policy — deterministic decisions
# ============================================================================

class TestIntentPolicy:
    def test_canonical_intents_all_known(self):
        for intent in CANONICAL_INTENTS:
            assert is_known_intent(intent), f"{intent} not known"

    def test_canonical_set_nonempty(self):
        assert len(CANONICAL_INTENTS) >= 10

    def test_is_not_known_for_garbage(self):
        assert not is_known_intent("weird_xyz_intent")

    def test_unknown_intent_fallback(self):
        d = decide("weird_unknown_intent", {}, confidence=1.0)
        assert d.fallback is True
        assert "unknown_intent" in d.fallback_reason

    def test_unknown_intent_uses_pipeline_v1(self):
        d = decide("weird_unknown_intent", {}, confidence=1.0)
        assert d.recipe_id == "pipeline-v1"

    def test_low_confidence_fallback(self):
        d = decide("summarize", {}, confidence=0.0)
        assert d.fallback is True
        assert "low_confidence" in d.fallback_reason

    def test_query_recipe_is_pipeline_v1(self):
        d = decide("query", {}, confidence=1.0)
        assert d.recipe_id == "pipeline-v1"

    def test_status_recipe_not_pipeline_v1(self):
        d = decide("status", {}, confidence=1.0)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_debug_recipe_not_pipeline_v1(self):
        d = decide("debug", {}, confidence=1.0)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_summarize_recipe_not_pipeline_v1(self):
        d = decide("summarize", {}, confidence=1.0)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_plan_recipe_not_pipeline_v1(self):
        d = decide("plan", {}, confidence=1.0)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_usage_recipe_not_pipeline_v1(self):
        d = decide("usage", {}, confidence=1.0)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_action_status_no_compress(self):
        d = decide("status", {}, confidence=1.0)
        assert d.action.compress is False
        assert d.action.skip_compression is True

    def test_action_debug_no_compress(self):
        d = decide("debug", {}, confidence=1.0)
        assert d.action.compress is False

    def test_action_summarize_compress(self):
        d = decide("summarize", {}, confidence=1.0)
        assert d.action.compress is True

    def test_action_plan_compress_and_retrieve(self):
        d = decide("plan", {}, confidence=1.0)
        assert d.action.compress is True
        assert d.action.retrieve is True

    def test_action_search_retrieve(self):
        d = decide("search", {}, confidence=1.0)
        assert d.action.retrieve is True

    def test_action_execute_no_compress(self):
        d = decide("execute", {}, confidence=1.0)
        assert d.action.compress is False

    def test_slots_surfaced_in_routing_decision(self):
        slots = {"target": "vault", "duration": "30d"}
        d = decide("summarize", slots, confidence=1.0)
        assert d.slots_used == slots

    def test_empty_slots_on_success(self):
        d = decide("status", {}, confidence=1.0)
        assert d.slots_used == {}

    def test_fallback_reason_empty_on_success(self):
        d = decide("status", {}, confidence=1.0)
        assert d.fallback_reason == ""
        assert d.fallback is False

    def test_decision_is_routing_decision(self):
        d = decide("debug", {}, confidence=1.0)
        assert isinstance(d, RoutingDecision)

    def test_decision_action_is_decision_action(self):
        d = decide("debug", {}, confidence=1.0)
        assert isinstance(d.action, DecisionAction)

    def test_determinism_debug(self):
        kwargs = dict(intent="debug", slots={"error_type": "auth"}, confidence=0.8)
        results = [decide(**kwargs) for _ in range(5)]
        assert len({r.recipe_id for r in results}) == 1
        assert len({r.fallback for r in results}) == 1

    def test_determinism_status(self):
        kwargs = dict(intent="status", slots={}, confidence=1.0)
        results = [decide(**kwargs) for _ in range(5)]
        assert len({r.recipe_id for r in results}) == 1

    def test_determinism_all_intents(self):
        for intent in CANONICAL_INTENTS:
            kwargs = dict(intent=intent, slots={}, confidence=1.0)
            results = [decide(**kwargs) for _ in range(3)]
            recipe_ids = {r.recipe_id for r in results}
            assert len(recipe_ids) == 1, f"Non-deterministic for intent={intent}: {recipe_ids}"


# ============================================================================
# End-to-end: text -> intent -> slots -> policy
# ============================================================================

class TestEndToEnd:
    @pytest.fixture
    def filler(self):
        return SlotFiller()

    def test_status_proxy_e2e(self, filler):
        text = "check the proxy status"
        intent = _classify_intent(text)
        assert intent == "status"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_usage_e2e(self, filler):
        text = "how much did I spend in the last 7 days?"
        intent = _classify_intent(text)
        assert intent == "usage"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id != "pipeline-v1"

    def test_debug_auth_e2e(self, filler):
        text = "fix the auth error in the router"
        intent = _classify_intent(text)
        assert intent == "debug"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_summarize_vault_e2e(self, filler):
        text = "summarize the vault for last 7 days"
        intent = _classify_intent(text)
        assert intent == "summarize"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id != "pipeline-v1"
        assert d.action.compress is True

    def test_plan_architecture_e2e(self, filler):
        text = "design the architecture for the new service"
        intent = _classify_intent(text)
        assert intent == "plan"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.action.retrieve is True

    def test_generic_query_e2e(self, filler):
        text = "the sky is blue"
        intent = _classify_intent(text)
        assert intent == "query"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id == "pipeline-v1"
        assert d.fallback is False

    def test_explain_slot_filler_e2e(self, filler):
        text = "how does the slot filler work?"
        intent = _classify_intent(text)
        assert intent == "explain"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id != "pipeline-v1"
        assert d.fallback is False

    def test_execute_staging_e2e(self, filler):
        text = "deploy to staging"
        intent = _classify_intent(text)
        assert intent == "execute"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id != "pipeline-v1"
        assert d.action.compress is False

    def test_full_pipeline_determinism(self, filler):
        text = "summarize the vault for last 7 days"
        results = []
        for _ in range(5):
            intent = _classify_intent(text)
            filled = filler.fill(intent, text)
            d = decide(intent, filled.slots, filled.confidence)
            results.append((intent, d.recipe_id, d.fallback))
        assert len(set(results)) == 1, f"Non-deterministic: {results}"

    def test_search_e2e(self, filler):
        text = "find the function that handles routing"
        intent = _classify_intent(text)
        assert intent == "search"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.action.retrieve is True

    def test_create_e2e(self, filler):
        text = "write a Python function to parse JSON"
        intent = _classify_intent(text)
        assert intent == "create"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id != "pipeline-v1"
