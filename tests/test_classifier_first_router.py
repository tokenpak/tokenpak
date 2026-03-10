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

import os
import sys

sys.path.insert(0, os.path.expanduser("~/tokenpak"))
sys.path.insert(0, os.path.expanduser("~/tokenpak/tokenpak"))

import pytest

from proxy_v4 import _classify_intent
from tokenpak.agent.compression.slot_filler import SlotFiller
from tokenpak.agent.proxy.intent_policy import (
    decide,
    is_known_intent,
    CANONICAL_INTENTS,
)


# ---------------------------------------------------------------------------
# Intent classifier
# ---------------------------------------------------------------------------

class TestClassifyIntent:
    def test_status_health_check(self):
        assert _classify_intent("what is the status of the proxy?") == "status"

    def test_status_is_running(self):
        assert _classify_intent("is it running") == "status"

    def test_status_uptime(self):
        assert _classify_intent("check uptime for the gateway") == "status"

    def test_usage_cost(self):
        assert _classify_intent("how much did I spend this week?") == "usage"

    def test_usage_tokens(self):
        assert _classify_intent("show me token count for today") == "usage"

    def test_usage_billing(self):
        assert _classify_intent("show billing summary") == "usage"

    def test_execute_run(self):
        assert _classify_intent("run the migration script") == "execute"

    def test_execute_deploy(self):
        assert _classify_intent("deploy to staging") == "execute"

    def test_execute_launch(self):
        assert _classify_intent("launch the pipeline") == "execute"

    def test_debug_fix(self):
        assert _classify_intent("fix the broken import") == "debug"

    def test_debug_error(self):
        assert _classify_intent("getting an error in the router") == "debug"

    def test_debug_traceback(self):
        assert _classify_intent("there's a traceback in the logs") == "debug"

    def test_summarize(self):
        assert _classify_intent("summarize the vault for last 7 days") == "summarize"

    def test_summarize_tldr(self):
        assert _classify_intent("tldr of this document") == "summarize"

    def test_plan_design(self):
        assert _classify_intent("design the architecture for TokenPak v5") == "plan"

    def test_plan_roadmap(self):
        assert _classify_intent("create a roadmap for the Q2 sprint") in ("plan", "create")

    def test_explain_what_is(self):
        assert _classify_intent("what is BM25?") == "explain"

    def test_explain_how_does(self):
        assert _classify_intent("how does the slot filler work?") == "explain"

    def test_search_find(self):
        assert _classify_intent("find the function that handles routing") == "search"

    def test_search_locate(self):
        assert _classify_intent("locate the vault index file") == "search"

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

    def test_determinism(self):
        text = "how does the proxy handle authentication errors?"
        results = {_classify_intent(text) for _ in range(5)}
        assert len(results) == 1


# ---------------------------------------------------------------------------
# SlotFiller
# ---------------------------------------------------------------------------

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
        assert expected.issubset(known), f"Missing intents: {expected - known}"

    def test_status_target_slot(self, filler):
        result = filler.fill("status", "what is the status of the proxy?")
        assert result.slots.get("target") == "proxy"

    def test_usage_duration_default(self, filler):
        result = filler.fill("usage", "show me cost breakdown")
        assert result.slots.get("duration") == "7d"

    def test_usage_duration_explicit(self, filler):
        result = filler.fill("usage", "show usage for last 7 days")
        assert result.slots.get("duration") == "7d"

    def test_debug_error_type_auth(self, filler):
        result = filler.fill("debug", "getting an auth error in production")
        assert result.slots.get("error_type") == "auth"

    def test_debug_error_type_timeout(self, filler):
        result = filler.fill("debug", "the connection keeps timing out")
        assert result.slots.get("error_type") == "timeout"

    def test_summarize_target(self, filler):
        result = filler.fill("summarize", "summarize the vault notes from last week")
        assert result.slots.get("target") == "vault"

    def test_execute_env_staging(self, filler):
        result = filler.fill("execute", "deploy the script to staging")
        assert result.slots.get("env") == "staging"

    def test_execute_env_prod(self, filler):
        result = filler.fill("execute", "run in prod")
        assert result.slots.get("env") == "prod"

    def test_unknown_intent_zero_confidence(self, filler):
        result = filler.fill("totally_unknown_xyz", "some text here")
        assert result.confidence == 0.0
        assert result.slots == {}

    def test_query_empty_slots(self, filler):
        result = filler.fill("query", "anything at all")
        assert result.slots == {}


# ---------------------------------------------------------------------------
# Intent Policy (deterministic)
# ---------------------------------------------------------------------------

class TestIntentPolicy:
    def test_canonical_intents_all_known(self):
        for intent in CANONICAL_INTENTS:
            assert is_known_intent(intent)

    def test_unknown_intent_fallback(self):
        d = decide("weird_unknown_intent", {}, confidence=1.0)
        assert d.fallback is True
        assert "unknown_intent" in d.fallback_reason
        assert d.recipe_id == "pipeline-v1"

    def test_low_confidence_fallback(self):
        d = decide("summarize", {}, confidence=0.1)
        assert d.fallback is True
        assert "low_confidence" in d.fallback_reason

    def test_status_bypasses_confidence_threshold(self):
        d = decide("status", {}, confidence=0.0)
        assert d.fallback is False

    def test_execute_bypasses_confidence_threshold(self):
        d = decide("execute", {}, confidence=0.0)
        assert d.fallback is False

    def test_query_always_pipeline_v1(self):
        d = decide("query", {}, confidence=1.0)
        assert d.recipe_id == "pipeline-v1"
        assert d.fallback is False

    def test_status_with_target(self):
        d = decide("status", {"target": "proxy"}, confidence=1.0)
        assert d.recipe_id == "recipe:status/proxy"

    def test_status_without_target(self):
        d = decide("status", {}, confidence=1.0)
        assert d.recipe_id == "recipe:status"

    def test_usage_with_duration(self):
        d = decide("usage", {"duration": "7d"}, confidence=1.0)
        assert d.recipe_id == "recipe:usage/7d"

    def test_debug_with_error_type(self):
        d = decide("debug", {"error_type": "auth"}, confidence=1.0)
        assert d.recipe_id == "recipe:debug/auth"

    def test_debug_error_type_takes_priority_over_target(self):
        d = decide("debug", {"error_type": "timeout", "target": "proxy"}, confidence=1.0)
        assert d.recipe_id == "recipe:debug/timeout"

    def test_create_with_artifact(self):
        d = decide("create", {"artifact": "function"}, confidence=1.0)
        assert d.recipe_id == "recipe:create/function"

    def test_action_profile_status_no_compress(self):
        d = decide("status", {}, confidence=1.0)
        assert d.action.compress is False
        assert d.action.priority == "high"

    def test_action_profile_plan_compress_inject_capsule(self):
        d = decide("plan", {}, confidence=1.0)
        assert d.action.compress is True
        assert d.action.vault_inject is True
        assert d.action.capsule is True

    def test_action_profile_execute_no_compress(self):
        d = decide("execute", {}, confidence=1.0)
        assert d.action.compress is False
        assert d.action.priority == "high"

    def test_action_profile_debug_high_max_tokens(self):
        d = decide("debug", {}, confidence=1.0)
        assert d.action.max_tokens >= 4000

    def test_determinism_repeated_calls(self):
        kwargs = dict(
            intent="debug",
            slots={"error_type": "timeout", "target": "proxy"},
            confidence=0.8,
        )
        results = [decide(**kwargs) for _ in range(5)]
        recipe_ids = {r.recipe_id for r in results}
        fallbacks = {r.fallback for r in results}
        assert len(recipe_ids) == 1
        assert len(fallbacks) == 1

    def test_slots_surfaced_in_decision(self):
        slots = {"target": "vault", "duration": "30d"}
        d = decide("summarize", slots, confidence=0.9)
        assert d.slots_used == slots


# ---------------------------------------------------------------------------
# End-to-end: text -> intent -> slots -> policy
# ---------------------------------------------------------------------------

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
        assert d.recipe_id == "recipe:status/proxy"
        assert d.fallback is False

    def test_usage_7d_e2e(self, filler):
        text = "how much did I spend in the last 7 days?"
        intent = _classify_intent(text)
        assert intent == "usage"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id == "recipe:usage/7d"

    def test_debug_auth_e2e(self, filler):
        text = "fix the auth error in the router"
        intent = _classify_intent(text)
        assert intent == "debug"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert "recipe:debug" in d.recipe_id

    def test_generic_query_e2e(self, filler):
        text = "the sky is blue"
        intent = _classify_intent(text)
        assert intent == "query"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id == "pipeline-v1"
        assert d.fallback is False

    def test_full_pipeline_determinism(self, filler):
        text = "summarize the vault for last 7 days"
        results = []
        for _ in range(5):
            intent = _classify_intent(text)
            filled = filler.fill(intent, text)
            d = decide(intent, filled.slots, filled.confidence)
            results.append((intent, d.recipe_id, d.fallback))
        assert len(set(results)) == 1, f"Non-deterministic: {results}"

    def test_explain_e2e(self, filler):
        text = "how does the slot filler work?"
        intent = _classify_intent(text)
        assert intent == "explain"
        filled = filler.fill(intent, text)
        d = decide(intent, filled.slots, filled.confidence)
        assert d.recipe_id.startswith("recipe:explain")
        assert d.fallback is False
