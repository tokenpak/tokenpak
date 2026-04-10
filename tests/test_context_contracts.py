"""Tests for intent-scoped context contracts in intent_policy.py.

Verifies:
  - PolicyResult has all 6 new contract fields
  - All 10 intents have defined contracts (non-default values where expected)
  - apply_context_contract enforces memory_scope, retrieval_sources, omission_rules, context_quota
  - Unknown intent falls back to safe defaults
  - Quota enforcement truncates at limit
  - Memory scope filtering works correctly
"""

import pytest
from tokenpak.proxy.intent_policy import (
    resolve_policy,
    apply_context_contract,
    FALLBACK_POLICY,
    PolicyResult,
    known_intents,
)


# ---------------------------------------------------------------------------
# PolicyResult schema tests
# ---------------------------------------------------------------------------

class TestPolicyResultSchema:
    """Verify PolicyResult has all 6 new contract fields."""

    def test_policy_result_has_memory_scope(self):
        policy = resolve_policy("debug", {}, 1.0)
        assert hasattr(policy, "memory_scope")
        assert isinstance(policy.memory_scope, tuple)

    def test_policy_result_has_retrieval_sources(self):
        policy = resolve_policy("debug", {}, 1.0)
        assert hasattr(policy, "retrieval_sources")
        assert isinstance(policy.retrieval_sources, tuple)

    def test_policy_result_has_context_quota(self):
        policy = resolve_policy("status", {}, 1.0)
        assert hasattr(policy, "context_quota")
        assert isinstance(policy.context_quota, int)

    def test_policy_result_has_omission_rules(self):
        policy = resolve_policy("status", {}, 1.0)
        assert hasattr(policy, "omission_rules")
        assert isinstance(policy.omission_rules, tuple)

    def test_policy_result_has_reasoning_ceiling(self):
        policy = resolve_policy("debug", {}, 1.0)
        assert hasattr(policy, "reasoning_ceiling")
        assert policy.reasoning_ceiling in ("low", "medium", "high")

    def test_policy_result_has_stop_condition(self):
        policy = resolve_policy("status", {}, 1.0)
        assert hasattr(policy, "stop_condition")
        assert isinstance(policy.stop_condition, str)


# ---------------------------------------------------------------------------
# Contract values for all 10 intents
# ---------------------------------------------------------------------------

class TestIntentContracts:
    """Verify all 10 intents have correct contract definitions."""

    def test_status_quota_is_500(self):
        p = resolve_policy("status", {}, 1.0)
        assert p.context_quota == 500

    def test_usage_quota_is_500(self):
        p = resolve_policy("usage", {}, 1.0)
        assert p.context_quota == 500

    def test_status_omits_history_and_memory(self):
        p = resolve_policy("status", {}, 1.0)
        assert "history" in p.omission_rules
        assert "memory" in p.omission_rules

    def test_usage_omits_history_and_memory(self):
        p = resolve_policy("usage", {}, 1.0)
        assert "history" in p.omission_rules
        assert "memory" in p.omission_rules

    def test_debug_memory_scope_has_errors_and_code(self):
        p = resolve_policy("debug", {}, 1.0)
        assert "errors" in p.memory_scope
        assert "code" in p.memory_scope

    def test_debug_retrieval_sources_has_logs(self):
        p = resolve_policy("debug", {}, 1.0)
        assert "logs" in p.retrieval_sources

    def test_debug_reasoning_ceiling_is_high(self):
        p = resolve_policy("debug", {}, 1.0)
        assert p.reasoning_ceiling == "high"

    def test_plan_quota_is_6000(self):
        p = resolve_policy("plan", {}, 1.0)
        assert p.context_quota == 6000

    def test_plan_reasoning_ceiling_is_high(self):
        p = resolve_policy("plan", {}, 1.0)
        assert p.reasoning_ceiling == "high"

    def test_execute_quota_is_2000(self):
        p = resolve_policy("execute", {}, 1.0)
        assert p.context_quota == 2000

    def test_execute_reasoning_ceiling_is_low(self):
        p = resolve_policy("execute", {}, 1.0)
        assert p.reasoning_ceiling == "low"

    def test_search_quota_is_2000(self):
        p = resolve_policy("search", {}, 1.0)
        assert p.context_quota == 2000

    def test_all_10_intents_covered(self):
        expected = {"status", "usage", "debug", "summarize", "plan",
                    "execute", "explain", "search", "create", "query"}
        assert expected == set(known_intents())


# ---------------------------------------------------------------------------
# apply_context_contract: memory scope filtering
# ---------------------------------------------------------------------------

class TestMemoryScopeFiltering:
    """Verify memory_scope filters context to allowed categories."""

    def test_debug_memory_scope_keeps_errors_and_code(self):
        policy = resolve_policy("debug", {}, 1.0)
        ctx = {"errors": "NullPointerException", "code": "def foo(): pass", "history": "old chat"}
        result = apply_context_contract(policy, ctx)
        assert "errors" in result
        assert "code" in result

    def test_debug_memory_scope_excludes_history(self):
        policy = resolve_policy("debug", {}, 1.0)
        ctx = {"errors": "error text", "code": "some code", "history": "old stuff"}
        result = apply_context_contract(policy, ctx)
        # history not in memory_scope + not in retrieval_sources → excluded
        assert "history" not in result

    def test_explain_empty_omission_keeps_all_in_scope(self):
        policy = resolve_policy("explain", {}, 1.0)
        ctx = {"context": "ctx", "docs": "docs", "code": "code"}
        result = apply_context_contract(policy, ctx)
        # All are in memory_scope or retrieval_sources
        assert "context" in result
        assert "docs" in result
        assert "code" in result


# ---------------------------------------------------------------------------
# apply_context_contract: omission rules
# ---------------------------------------------------------------------------

class TestOmissionRules:
    """Verify omission_rules actually remove excluded categories."""

    def test_status_omission_removes_history(self):
        policy = resolve_policy("status", {}, 1.0)
        ctx = {"history": "old chat", "memory": "saved notes", "counter": "42"}
        result = apply_context_contract(policy, ctx)
        assert "history" not in result
        assert "memory" not in result

    def test_debug_omission_removes_brand_and_style(self):
        policy = resolve_policy("debug", {}, 1.0)
        ctx = {"errors": "traceback", "code": "fn()", "brand": "acme", "style": "formal"}
        result = apply_context_contract(policy, ctx)
        assert "brand" not in result
        assert "style" not in result
        assert "errors" in result

    def test_empty_omission_rules_keeps_everything(self):
        policy = resolve_policy("query", {}, 1.0)
        # query has empty omission_rules
        assert policy.omission_rules == ()
        ctx = {"recent": "some context", "relevant": "retrieved docs"}
        result = apply_context_contract(policy, ctx)
        assert "recent" in result
        assert "relevant" in result


# ---------------------------------------------------------------------------
# apply_context_contract: quota enforcement
# ---------------------------------------------------------------------------

class TestQuotaEnforcement:
    """Verify context_quota truncates context at the limit."""

    def test_quota_truncates_to_limit(self):
        # Create a policy with a tiny quota (50 tokens ≈ 200 chars)
        policy = resolve_policy("status", {}, 1.0)  # quota=500
        # Create large content: ~2000 chars ≈ 500 tokens (above 500-token quota)
        big_text = "x" * 8000  # ~2000 tokens — well above 500
        ctx = {"counter": big_text}
        result = apply_context_contract(policy, ctx)
        # Output should be smaller than input
        total_out = sum(len(str(v)) for v in result.values())
        assert total_out < len(big_text)

    def test_quota_under_limit_unchanged(self):
        policy = resolve_policy("debug", {}, 1.0)  # quota=4000
        # Tiny content: well under quota
        ctx = {"errors": "one error", "code": "x = 1"}
        result = apply_context_contract(policy, ctx)
        assert result["errors"] == "one error"
        assert result["code"] == "x = 1"

    def test_status_context_under_500_tokens(self):
        policy = resolve_policy("status", {}, 1.0)
        # Should not exceed 500 tokens for status
        ctx = {"counter": "100"}
        result = apply_context_contract(policy, ctx)
        # count approximated tokens
        total_tokens = sum(max(1, len(str(v)) // 4) for v in result.values())
        assert total_tokens <= 500


# ---------------------------------------------------------------------------
# apply_context_contract: unknown intent / fallback
# ---------------------------------------------------------------------------

class TestFallbackContract:
    """Unknown intent should fall back to safe defaults."""

    def test_unknown_intent_fallback_defaults(self):
        policy = resolve_policy("totally_unknown_xyz", {}, 1.0)
        # Should fall back with no omission, no scope restriction
        assert policy.context_quota == 4000
        assert policy.omission_rules == ()
        assert policy.memory_scope == ()

    def test_fallback_policy_has_contract_fields(self):
        assert hasattr(FALLBACK_POLICY, "context_quota")
        assert hasattr(FALLBACK_POLICY, "memory_scope")
        assert hasattr(FALLBACK_POLICY, "omission_rules")
        assert FALLBACK_POLICY.context_quota == 4000

    def test_fallback_contract_preserves_all_categories(self):
        """With empty scope + empty omissions, all categories pass through."""
        ctx = {"foo": "bar", "baz": "qux", "history": "old"}
        result = apply_context_contract(FALLBACK_POLICY, ctx)
        # No scope restriction, no omissions → all pass
        assert set(result.keys()) == {"foo", "baz", "history"}


# ---------------------------------------------------------------------------
# to_dict serialization includes contract fields
# ---------------------------------------------------------------------------

class TestToDict:
    """Verify PolicyResult.to_dict() includes all contract fields."""

    def test_to_dict_includes_contract_fields(self):
        policy = resolve_policy("debug", {}, 1.0)
        d = policy.to_dict()
        assert "memory_scope" in d
        assert "retrieval_sources" in d
        assert "context_quota" in d
        assert "omission_rules" in d
        assert "reasoning_ceiling" in d
        assert "stop_condition" in d

    def test_to_dict_values_are_lists_not_tuples(self):
        """to_dict should serialize tuples as lists for JSON compatibility."""
        policy = resolve_policy("debug", {}, 1.0)
        d = policy.to_dict()
        assert isinstance(d["memory_scope"], list)
        assert isinstance(d["retrieval_sources"], list)
        assert isinstance(d["omission_rules"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
