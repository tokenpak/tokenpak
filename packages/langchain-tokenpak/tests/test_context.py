"""Tests for TokenPakContextManager and TokenPakState."""

import pytest
from langchain_tokenpak import TokenPakContextManager
from langchain_tokenpak.langgraph import TokenPakState


# ── TokenPakContextManager ───────────────────────────────────────────────────

class TestTokenPakContextManager:
    """Tests for TokenPakContextManager."""

    def test_default_budgets(self):
        """Default: 70% docs, 30% memory of 8000 total."""
        mgr = TokenPakContextManager(total_budget=8000)
        assert mgr.document_budget() == 5600
        assert mgr.memory_budget() == 2400

    def test_custom_ratio(self):
        """Custom doc_ratio splits budget accordingly."""
        mgr = TokenPakContextManager(total_budget=10000, doc_ratio=0.5)
        assert mgr.document_budget() == 5000
        assert mgr.memory_budget() == 5000

    def test_min_memory_floor(self):
        """min_memory_tokens enforces minimum even when ratio would go lower."""
        mgr = TokenPakContextManager(
            total_budget=1000, doc_ratio=0.99, min_memory_tokens=200
        )
        assert mgr.memory_budget() >= 200

    def test_adjust_budget_both_fit(self):
        """When both fit, returns default split."""
        mgr = TokenPakContextManager(total_budget=8000)
        result = mgr.adjust_budget(doc_tokens=2000, memory_tokens=1000)
        assert result["doc_budget"] == mgr.document_budget()
        assert result["memory_budget"] == mgr.memory_budget()

    def test_adjust_budget_over_limit(self):
        """When usage exceeds total, budget is re-allocated proportionally."""
        mgr = TokenPakContextManager(total_budget=1000)
        result = mgr.adjust_budget(doc_tokens=800, memory_tokens=800)
        # Combined > budget; doc ratio ≈ 0.5, so allocations shift
        assert result["doc_budget"] + result["memory_budget"] <= 1000 + mgr.min_memory_tokens

    def test_repr(self):
        """__repr__ contains key info."""
        mgr = TokenPakContextManager(total_budget=4000, doc_ratio=0.6)
        r = repr(mgr)
        assert "4000" in r
        assert "TokenPakContextManager" in r

    def test_ratio_clamps_to_0_1(self):
        """doc_ratio is clamped to [0, 1]."""
        mgr_high = TokenPakContextManager(total_budget=1000, doc_ratio=2.0)
        mgr_low = TokenPakContextManager(total_budget=1000, doc_ratio=-1.0)
        assert mgr_high.document_budget() == 1000
        assert mgr_low.document_budget() == 0


# ── TokenPakState ────────────────────────────────────────────────────────────

class TestTokenPakState:
    """Tests for LangGraph TokenPakState."""

    def test_append_and_retrieve_messages(self):
        """Messages can be added and retrieved."""
        state = TokenPakState(max_tokens=4000)
        state.append_message("planner", "Plan step 1")
        state.append_message("executor", "Executed step 1")
        assert len(state) == 2
        assert state.messages[0]["agent"] == "planner"
        assert state.messages[1]["content"] == "Executed step 1"

    def test_no_compression_under_budget(self):
        """Messages are returned as-is when under budget."""
        state = TokenPakState(max_tokens=10000)
        for i in range(5):
            state.append_message("agent", f"Short msg {i}")
        msgs = state.messages
        assert len(msgs) == 5
        # None should be compressed
        assert all(not m.get("_compressed") for m in msgs)

    def test_compression_over_budget(self):
        """Older messages are compressed when state exceeds budget."""
        state = TokenPakState(max_tokens=50, keep_recent_messages=2)
        # Add messages that together exceed the small budget
        for i in range(10):
            state.append_message("agent", "X" * 200)
        msgs = state.messages
        # Recent ones (last 2) should not be compressed
        assert not msgs[-1].get("_compressed")
        assert not msgs[-2].get("_compressed")
        # Older ones should be compressed
        assert msgs[0].get("_compressed")

    def test_clear(self):
        """clear() empties the state."""
        state = TokenPakState()
        state.append_message("agent", "Hello")
        assert len(state) == 1
        state.clear()
        assert len(state) == 0
        assert state.messages == []

    def test_metadata_preserved(self):
        """Optional metadata is stored and returned."""
        state = TokenPakState()
        state.append_message("agent", "msg", metadata={"step": 3})
        assert state.messages[0]["metadata"]["step"] == 3

    def test_keep_recent_messages_boundary(self):
        """Exactly keep_recent_messages are never compressed."""
        state = TokenPakState(max_tokens=10, keep_recent_messages=3)
        for i in range(6):
            state.append_message("a", "Z" * 50)
        msgs = state.messages
        # Last 3 must not be compressed
        for msg in msgs[-3:]:
            assert not msg.get("_compressed")
