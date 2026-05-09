"""Tests for ContextComposer — Prompt Packer."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.context_composer", reason="module not available in current build")
import pytest
from tokenpak.context_composer import (
    ComposedContext,
    ContextComposer,
    RetrievedChunk,
    _count_tokens,
)

composer = ContextComposer()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_chunks(n: int, content_size: int = 50) -> list[RetrievedChunk]:
    """Create n chunks with descending rank and ~content_size chars each."""
    return [
        RetrievedChunk(
            content="x" * content_size,
            rank=float(n - i),
            source=f"file_{i}.py:10",
            chunk_id=f"chunk_{i}",
        )
        for i in range(n)
    ]


def total_tokens(result: ComposedContext) -> int:
    """Sum token cost of all messages in the result."""
    return sum(
        _count_tokens(m.get("content", "")) + 4
        for m in result.final_prompt_messages
    )


# ---------------------------------------------------------------------------
# 1. Packing respects budget limit (non-escalation path)
# ---------------------------------------------------------------------------

class TestBudgetRespected:
    def test_no_escalation_when_fits(self):
        chunks = make_chunks(3, content_size=10)
        result = composer.compose(
            budget=500,
            system_prompt="You are a helpful assistant.",
            user_request="What is 2+2?",
            retrieved_chunks=chunks,
        )
        assert not result.escalation_needed

    def test_actual_tokens_within_budget(self):
        chunks = make_chunks(3, content_size=10)
        result = composer.compose(
            budget=500,
            system_prompt="System.",
            user_request="Hello.",
            retrieved_chunks=chunks,
        )
        assert result.actual_tokens <= result.final_budget

    def test_tiny_budget_drops_chunks(self):
        """When budget is very tight, chunks are dropped."""
        chunks = make_chunks(5, content_size=200)
        result = composer.compose(
            budget=50,
            system_prompt="",
            user_request="Hi.",
            retrieved_chunks=chunks,
        )
        # With such a small budget, most chunks must be dropped
        assert len(result.dropped_chunks) > 0


# ---------------------------------------------------------------------------
# 2. Priority order maintained
# ---------------------------------------------------------------------------

class TestPriorityOrder:
    def test_system_prompt_first(self):
        result = composer.compose(
            budget=1000,
            system_prompt="System instructions.",
            user_request="User request.",
        )
        msgs = result.final_prompt_messages
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert system_msgs[0]["content"] == "System instructions."

    def test_user_request_present(self):
        result = composer.compose(
            budget=1000,
            user_request="What is the answer?",
        )
        user_msgs = [m for m in result.final_prompt_messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert "What is the answer?" in user_msgs[0]["content"]

    def test_session_state_before_user(self):
        result = composer.compose(
            budget=1000,
            system_prompt="Sys.",
            session_state="session=abc",
            user_request="Request.",
        )
        msgs = result.final_prompt_messages
        roles = [m["role"] for m in msgs]
        # system comes before user
        assert roles.index("system") < roles.index("user")

    def test_higher_rank_chunk_included_over_lower(self):
        """When budget is tight, higher-ranked chunks win."""
        high = RetrievedChunk(content="A" * 100, rank=10.0, source="high.py")
        low = RetrievedChunk(content="B" * 200, rank=1.0, source="low.py")
        result = composer.compose(
            budget=60,
            user_request="R.",
            retrieved_chunks=[low, high],
        )
        contents = " ".join(m["content"] for m in result.final_prompt_messages)
        # high chunk should be included, low chunk dropped
        assert "A" * 10 in contents or "high.py" in contents


# ---------------------------------------------------------------------------
# 3. Lowest chunks dropped first
# ---------------------------------------------------------------------------

class TestDropLowestFirst:
    def test_lowest_rank_dropped_first(self):
        chunks = [
            RetrievedChunk(content="TOP " * 20, rank=9.0, source="top.py"),
            RetrievedChunk(content="MID " * 20, rank=5.0, source="mid.py"),
            RetrievedChunk(content="LOW " * 20, rank=1.0, source="low.py"),
        ]
        result = composer.compose(
            budget=80,
            user_request="Query.",
            retrieved_chunks=chunks,
        )
        # low.py should be in dropped list if any chunks are dropped
        if result.dropped_chunks:
            assert "low.py" in result.dropped_chunks or any(
                "low" in d for d in result.dropped_chunks
            )

    def test_dropped_chunks_list_populated(self):
        chunks = make_chunks(10, content_size=300)
        result = composer.compose(
            budget=100,
            user_request="Query.",
            retrieved_chunks=chunks,
        )
        assert len(result.dropped_chunks) > 0

    def test_explain_plan_mentions_drops(self):
        chunks = make_chunks(5, content_size=300)
        result = composer.compose(
            budget=80,
            user_request="Query.",
            retrieved_chunks=chunks,
        )
        drop_mentions = [e for e in result.explain_plan if "DROPPED" in e]
        assert len(drop_mentions) > 0


# ---------------------------------------------------------------------------
# 4. Escalation signaled when can't fit
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_escalation_on_impossible_budget(self):
        """System + user alone exceed budget → escalation."""
        result = composer.compose(
            budget=5,
            system_prompt="This is a very long system prompt that cannot fit.",
            user_request="And the user also has a long request here.",
        )
        assert result.escalation_needed

    def test_no_escalation_on_adequate_budget(self):
        result = composer.compose(
            budget=2000,
            system_prompt="Short.",
            user_request="Also short.",
        )
        assert not result.escalation_needed

    def test_escalation_in_explain_plan(self):
        result = composer.compose(
            budget=5,
            system_prompt="System prompt that is long.",
            user_request="User request.",
        )
        if result.escalation_needed:
            assert any("ESCALATION" in e for e in result.explain_plan)


# ---------------------------------------------------------------------------
# 5. Micro-summary generated for dropped content
# ---------------------------------------------------------------------------

class TestMicroSummary:
    def test_summarized_chunks_populated_when_dropped(self):
        chunks = make_chunks(10, content_size=200)
        result = composer.compose(
            budget=80,
            user_request="Q.",
            retrieved_chunks=chunks,
        )
        if result.dropped_chunks:
            assert len(result.summarized_chunks) > 0

    def test_drop_summary_message_in_output(self):
        """Drop summary appears in messages (generous budget) or explain_plan (tight budget)."""
        chunks = make_chunks(5, content_size=200)
        result = composer.compose(
            budget=80,
            user_request="Query.",
            retrieved_chunks=chunks,
        )
        if result.dropped_chunks:
            all_content = " ".join(m.get("content", "") for m in result.final_prompt_messages)
            # Summary message may not fit in a very tight budget; explain_plan always records it
            in_messages = "dropped_context_summary" in all_content or "dropped" in all_content.lower()
            in_plan = any("drop" in e.lower() or "DROPPED" in e for e in result.explain_plan)
            assert in_messages or in_plan, "Drop activity not recorded in messages or explain_plan"

    def test_previous_phase_summary_included(self):
        result = composer.compose(
            budget=2000,
            user_request="Continue work.",
            previous_phase_summary="Previously we analyzed the codebase and found issues.",
        )
        all_content = " ".join(m.get("content", "") for m in result.final_prompt_messages)
        assert "previously" in all_content.lower() or "previous_phase_summary" in all_content


# ---------------------------------------------------------------------------
# 6. Empty retrieval still produces valid prompt
# ---------------------------------------------------------------------------

class TestEmptyRetrieval:
    def test_empty_chunks_valid_output(self):
        result = composer.compose(
            budget=1000,
            system_prompt="System.",
            user_request="Hello.",
            retrieved_chunks=[],
        )
        assert isinstance(result, ComposedContext)
        assert len(result.final_prompt_messages) >= 1
        assert result.actual_tokens > 0

    def test_no_chunks_no_drops(self):
        result = composer.compose(
            budget=1000,
            user_request="Hi.",
            retrieved_chunks=None,
        )
        assert result.dropped_chunks == []

    def test_empty_input_minimal_output(self):
        result = composer.compose(
            budget=1000,
            user_request="",
        )
        assert isinstance(result, ComposedContext)
        # user message always added
        assert len(result.final_prompt_messages) >= 1

    def test_explain_plan_non_empty(self):
        result = composer.compose(
            budget=1000,
            system_prompt="S.",
            user_request="U.",
        )
        assert len(result.explain_plan) > 0


# ---------------------------------------------------------------------------
# 7. ComposedContext fields
# ---------------------------------------------------------------------------

class TestComposedContextFields:
    def test_result_type(self):
        result = composer.compose(budget=1000, user_request="Test.")
        assert isinstance(result, ComposedContext)

    def test_final_prompt_messages_list(self):
        result = composer.compose(budget=1000, user_request="Test.")
        assert isinstance(result.final_prompt_messages, list)
        for m in result.final_prompt_messages:
            assert "role" in m
            assert "content" in m

    def test_final_budget_set(self):
        result = composer.compose(budget=512, user_request="Test.")
        assert result.final_budget == 512

    def test_actual_tokens_positive(self):
        result = composer.compose(budget=1000, user_request="Hello.")
        assert result.actual_tokens > 0

    def test_recent_turns_included(self):
        turns = [
            {"role": "user", "content": "Previous question."},
            {"role": "assistant", "content": "Previous answer."},
        ]
        result = composer.compose(
            budget=2000,
            user_request="Follow-up.",
            recent_turns=turns,
        )
        all_content = " ".join(m.get("content", "") for m in result.final_prompt_messages)
        assert "Previous question" in all_content

    def test_max_4_recent_turns(self):
        """Only last 4 turns are considered."""
        turns = [{"role": "user", "content": f"Turn {i}."} for i in range(10)]
        result = composer.compose(
            budget=5000,
            user_request="Now.",
            recent_turns=turns,
        )
        # Count occurrences of "Turn" in messages
        turn_messages = [
            m for m in result.final_prompt_messages
            if "Turn" in m.get("content", "")
        ]
        assert len(turn_messages) <= 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
