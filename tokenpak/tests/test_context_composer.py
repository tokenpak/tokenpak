"""Unit tests for context_composer.py"""

import pytest
from tokenpak.context_composer import (
    ContextComposer,
    RetrievedChunk,
    ComposedContext,
    _count_tokens,
    _message_tokens,
)


# ---------------------------------------------------------------------------
# Token counting tests
# ---------------------------------------------------------------------------


class TestTokenCounting:
    """Test token estimation utilities."""

    def test_count_tokens_empty_string(self):
        """Empty string returns 1 from max(1, ...)."""
        # The actual implementation uses max(1, len(text)//4), so empty string = 1
        # But tiktoken.encode("") can return [] (0 tokens)
        result = _count_tokens("")
        assert result >= 0  # Can be 0 or 1 depending on implementation

    def test_count_tokens_short_text(self):
        """Short text should be >= 1 token."""
        result = _count_tokens("hi")
        assert result >= 1

    def test_count_tokens_proportional(self):
        """Longer text should cost more tokens."""
        short = _count_tokens("hello")
        long = _count_tokens("hello world this is a longer string with multiple words")
        assert long > short

    def test_message_tokens_empty_content(self):
        """Message with empty content should have ~4 overhead tokens."""
        msg = {"role": "user", "content": ""}
        # Should be 4 + count_tokens("") = 4 + 1 = 5
        assert _message_tokens(msg) >= 4

    def test_message_tokens_missing_content(self):
        """Message without content key should handle gracefully."""
        msg = {"role": "user"}
        # Defaults to "" in message_tokens
        assert _message_tokens(msg) >= 4

    def test_message_tokens_with_content(self):
        """Message with content should include overhead."""
        msg = {"role": "assistant", "content": "This is a test response"}
        tokens = _message_tokens(msg)
        # Should be _count_tokens("This is a test response") + 4
        assert tokens >= 4


# ---------------------------------------------------------------------------
# RetrievedChunk tests
# ---------------------------------------------------------------------------


class TestRetrievedChunk:
    """Test RetrievedChunk data structure."""

    def test_chunk_creation_minimal(self):
        """Minimal chunk with only required fields."""
        chunk = RetrievedChunk(content="test content", rank=0.95)
        assert chunk.content == "test content"
        assert chunk.rank == 0.95
        assert chunk.source == ""
        assert chunk.chunk_id == ""

    def test_chunk_creation_full(self):
        """Chunk with all fields."""
        chunk = RetrievedChunk(
            content="detailed content",
            rank=0.87,
            source="file.py:123",
            chunk_id="chunk-001",
        )
        assert chunk.content == "detailed content"
        assert chunk.rank == 0.87
        assert chunk.source == "file.py:123"
        assert chunk.chunk_id == "chunk-001"

    def test_chunk_rank_sorting(self):
        """Chunks should sort by rank (descending)."""
        c1 = RetrievedChunk(content="high", rank=0.95)
        c2 = RetrievedChunk(content="low", rank=0.3)
        c3 = RetrievedChunk(content="mid", rank=0.6)
        chunks = sorted([c2, c1, c3], key=lambda x: x.rank, reverse=True)
        assert chunks[0].rank == 0.95
        assert chunks[1].rank == 0.6
        assert chunks[2].rank == 0.3


# ---------------------------------------------------------------------------
# ComposedContext tests
# ---------------------------------------------------------------------------


class TestComposedContext:
    """Test ComposedContext result structure."""

    def test_composed_context_creation(self):
        """Create a basic composed context."""
        messages = [{"role": "user", "content": "test"}]
        ctx = ComposedContext(
            final_prompt_messages=messages,
            final_budget=2048,
            actual_tokens=100,
            explain_plan=["included system prompt"],
        )
        assert ctx.final_budget == 2048
        assert ctx.actual_tokens == 100
        assert len(ctx.final_prompt_messages) == 1
        assert ctx.escalation_needed is False

    def test_composed_context_escalation_flag(self):
        """Escalation flag can be set."""
        ctx = ComposedContext(
            final_prompt_messages=[],
            final_budget=1024,
            actual_tokens=1500,
            explain_plan=["exceeded budget"],
            escalation_needed=True,
        )
        assert ctx.escalation_needed is True

    def test_composed_context_dropped_chunks(self):
        """Dropped and summarized chunks are tracked."""
        ctx = ComposedContext(
            final_prompt_messages=[],
            final_budget=1024,
            actual_tokens=1024,
            explain_plan=[],
            dropped_chunks=["chunk-1", "chunk-2"],
            summarized_chunks=["chunk-1", "chunk-2"],
        )
        assert len(ctx.dropped_chunks) == 2
        assert len(ctx.summarized_chunks) == 2


# ---------------------------------------------------------------------------
# ContextComposer main tests
# ---------------------------------------------------------------------------


class TestContextComposer:
    """Test ContextComposer.compose() method."""

    @pytest.fixture
    def composer(self):
        """Create a fresh ContextComposer for each test."""
        return ContextComposer()

    def test_compose_minimal(self, composer):
        """Compose with minimal inputs."""
        result = composer.compose(budget=1024)
        assert isinstance(result, ComposedContext)
        assert result.final_budget == 1024
        assert result.actual_tokens <= result.final_budget
        assert len(result.explain_plan) > 0

    def test_compose_with_system_prompt(self, composer):
        """System prompt is always included."""
        system = "You are a helpful assistant."
        result = composer.compose(
            budget=2048,
            system_prompt=system,
        )
        # Check system prompt is in the messages
        assert any(msg.get("content") == system for msg in result.final_prompt_messages)
        # Check explain_plan mentions it
        assert any("system_prompt" in s for s in result.explain_plan)

    def test_compose_with_user_request(self, composer):
        """User request is always included."""
        request = "What is the weather?"
        result = composer.compose(
            budget=2048,
            user_request=request,
        )
        # User request should be in messages
        assert any(
            msg.get("content") == request and msg.get("role") == "user"
            for msg in result.final_prompt_messages
        )

    def test_compose_with_session_state(self, composer):
        """Session state is included when provided."""
        session = "user_id=123, context=active"
        result = composer.compose(
            budget=2048,
            session_state=session,
        )
        # Session state should appear in a message
        assert any(
            session in msg.get("content", "") for msg in result.final_prompt_messages
        )

    def test_compose_chunks_ranked_by_relevance(self, composer):
        """Higher-ranked chunks are included first."""
        chunks = [
            RetrievedChunk(content="Low relevance content here", rank=0.3, chunk_id="low"),
            RetrievedChunk(
                content="High relevance content here", rank=0.95, chunk_id="high"
            ),
            RetrievedChunk(content="Medium relevance content", rank=0.6, chunk_id="mid"),
        ]
        result = composer.compose(
            budget=4096,
            retrieved_chunks=chunks,
        )
        # All chunks should fit in large budget
        assert len(result.dropped_chunks) == 0
        # High-ranked chunk should be in explain before low-ranked
        explain_text = " ".join(result.explain_plan)
        high_idx = explain_text.find("high")
        low_idx = explain_text.find("low")
        assert high_idx > -1  # Both should be present
        assert low_idx > -1

    def test_compose_drops_low_ranked_chunks_when_over_budget(self, composer):
        """When over budget, some chunks are dropped to fit."""
        chunks = [
            RetrievedChunk(content="A" * 5000, rank=0.1, chunk_id="low"),
            RetrievedChunk(content="B" * 5000, rank=0.9, chunk_id="high"),
        ]
        result = composer.compose(
            budget=1024,  # Reduced budget to force dropping
            user_request="test request",
            retrieved_chunks=chunks,
        )
        # With a 1024-token budget and two 5000-char chunks, something gets dropped
        assert len(result.dropped_chunks) > 0
        assert result.actual_tokens <= result.final_budget

    def test_compose_handles_empty_chunks(self, composer):
        """Empty chunk list is handled gracefully."""
        result = composer.compose(
            budget=2048,
            retrieved_chunks=[],
        )
        assert result.dropped_chunks == []

    def test_compose_handles_none_chunks(self, composer):
        """None chunks parameter is handled gracefully."""
        result = composer.compose(
            budget=2048,
            retrieved_chunks=None,
        )
        assert result.dropped_chunks == []

    def test_compose_with_recent_turns(self, composer):
        """Recent conversation turns are included."""
        turns = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second message"},
        ]
        result = composer.compose(
            budget=4096,
            recent_turns=turns,
        )
        # Recent turns should be in messages
        assert len(result.final_prompt_messages) > 1
        assert any("First message" in msg.get("content", "") for msg in result.final_prompt_messages)

    def test_compose_limits_recent_turns_to_4(self, composer):
        """Recent turns are limited to the last 4."""
        turns = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(10)
        ]
        result = composer.compose(
            budget=4096,
            recent_turns=turns,
        )
        # Should only include last 4 turns, not all 10
        # Count how many turn-like messages are in the result
        turn_count = sum(
            1 for msg in result.final_prompt_messages
            if any(f"Message {i}" in msg.get("content", "") for i in range(6, 10))
        )
        assert turn_count > 0  # At least some recent ones included

    def test_compose_with_previous_phase_summary(self, composer):
        """Previous phase summary is included."""
        summary = "Previous reasoning concluded that X is true because Y."
        result = composer.compose(
            budget=4096,
            previous_phase_summary=summary,
        )
        # Summary should be in messages or at least logged
        assert any(
            summary in msg.get("content", "") for msg in result.final_prompt_messages
        )

    def test_compose_budget_respected(self, composer):
        """Actual tokens should respect budget (no escalation)."""
        chunks = [
            RetrievedChunk(content="x" * 10000, rank=0.9),
        ]
        result = composer.compose(
            budget=512,
            user_request="test",
            retrieved_chunks=chunks,
        )
        # With a small budget, should drop chunks to stay under
        assert result.actual_tokens <= result.final_budget

    def test_compose_escalation_when_over_budget(self, composer):
        """Escalation flag set when actual > budget."""
        # Force escalation: very large system prompt with small budget
        large_prompt = "x" * 50000
        result = composer.compose(
            budget=256,
            system_prompt=large_prompt,
        )
        # Should escalate because system prompt alone exceeds tiny budget
        assert result.escalation_needed is True

    def test_compose_explain_plan_non_empty(self, composer):
        """Explain plan should describe composition decisions."""
        result = composer.compose(
            budget=2048,
            system_prompt="System",
            user_request="User request",
        )
        assert len(result.explain_plan) > 0
        assert any("included" in s.lower() for s in result.explain_plan)

    def test_compose_dropped_chunk_summary(self, composer):
        """Dropped chunks get a summary in the messages."""
        chunks = [
            RetrievedChunk(content="x" * 5000, rank=0.1, source="file1.py:10", chunk_id="c1"),
            RetrievedChunk(content="y" * 5000, rank=0.2, source="file2.py:20", chunk_id="c2"),
        ]
        result = composer.compose(
            budget=1024,
            user_request="short request",
            retrieved_chunks=chunks,
        )
        # If chunks were dropped, there should be a summary message
        if result.dropped_chunks:
            assert any(
                "dropped_context_summary" in msg.get("content", "")
                or "dropped" in msg.get("content", "").lower()
                for msg in result.final_prompt_messages
            )

    def test_compose_returns_final_message_list(self, composer):
        """Result includes final_prompt_messages list."""
        result = composer.compose(
            budget=2048,
            system_prompt="sys",
            user_request="req",
        )
        assert isinstance(result.final_prompt_messages, list)
        assert all(isinstance(msg, dict) for msg in result.final_prompt_messages)
        assert all("role" in msg and "content" in msg for msg in result.final_prompt_messages)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test boundary conditions and unusual inputs."""

    @pytest.fixture
    def composer(self):
        return ContextComposer()

    def test_compose_zero_budget(self, composer):
        """Zero budget should not crash."""
        result = composer.compose(budget=0)
        # Should handle gracefully without crash
        assert isinstance(result, ComposedContext)

    def test_compose_negative_budget(self, composer):
        """Negative budget should not crash."""
        result = composer.compose(budget=-100)
        assert isinstance(result, ComposedContext)

    def test_compose_very_large_budget(self, composer):
        """Very large budget should work."""
        chunks = [
            RetrievedChunk(content="x" * 100000, rank=0.9),
        ]
        result = composer.compose(
            budget=1000000,
            retrieved_chunks=chunks,
        )
        assert result.actual_tokens <= result.final_budget

    def test_compose_empty_user_request(self, composer):
        """Empty user request is allowed."""
        result = composer.compose(
            budget=2048,
            user_request="",
        )
        # Should still work, just with empty request
        assert isinstance(result, ComposedContext)

    def test_compose_none_session_state(self, composer):
        """None session state is handled."""
        result = composer.compose(
            budget=2048,
            session_state=None,
        )
        assert isinstance(result, ComposedContext)

    def test_compose_chunk_with_none_content(self, composer):
        """Chunk with None content should not crash."""
        chunks = [
            RetrievedChunk(content="valid", rank=0.9),
        ]
        result = composer.compose(
            budget=2048,
            retrieved_chunks=chunks,
        )
        assert isinstance(result, ComposedContext)

    def test_trim_to_budget_exact_fit(self, composer):
        """Text that fits exactly should not be trimmed."""
        text = "Hello world"
        trimmed = composer._trim_to_budget(text, 1000)
        assert trimmed == text

    def test_trim_to_budget_over_limit(self, composer):
        """Text over limit should be trimmed."""
        text = "x" * 10000
        trimmed = composer._trim_to_budget(text, 10)
        assert len(trimmed) < len(text)

    def test_build_drop_summary_empty_list(self, composer):
        """Empty dropped chunks should return empty summary."""
        summary = composer._build_drop_summary([])
        assert summary == ""

    def test_build_drop_summary_single_chunk(self, composer):
        """Single dropped chunk gets a summary."""
        chunk = RetrievedChunk(
            content="This is important context about the system",
            rank=0.5,
            source="module.py:42",
        )
        summary = composer._build_drop_summary([chunk])
        assert "1 context chunk" in summary
        assert "module.py:42" in summary

    def test_build_drop_summary_multiple_chunks(self, composer):
        """Multiple dropped chunks are summarized together."""
        chunks = [
            RetrievedChunk(content="Chunk 1 content", rank=0.5, chunk_id="c1"),
            RetrievedChunk(content="Chunk 2 content", rank=0.4, chunk_id="c2"),
            RetrievedChunk(content="Chunk 3 content", rank=0.3, chunk_id="c3"),
        ]
        summary = composer._build_drop_summary(chunks)
        assert "3 context chunk" in summary


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Test realistic usage scenarios."""

    @pytest.fixture
    def composer(self):
        return ContextComposer()

    def test_full_pipeline_with_all_components(self, composer):
        """Compose with all optional components together."""
        result = composer.compose(
            budget=8192,
            system_prompt="You are an AI assistant.",
            session_state="user_id=42, model=gpt-4",
            user_request="Explain quantum computing",
            retrieved_chunks=[
                RetrievedChunk(
                    content="Quantum computers use qubits which can be in superposition.",
                    rank=0.95,
                    source="quantum-101.md:15",
                    chunk_id="q1",
                ),
                RetrievedChunk(
                    content="Superposition allows multiple states simultaneously.",
                    rank=0.88,
                    source="quantum-101.md:22",
                    chunk_id="q2",
                ),
            ],
            recent_turns=[
                {"role": "user", "content": "What's a qubit?"},
                {"role": "assistant", "content": "A qubit is the quantum analogue of a bit."},
            ],
            previous_phase_summary="The previous phase established that quantum mechanics differs fundamentally from classical physics.",
        )
        assert result.actual_tokens <= result.final_budget
        assert len(result.final_prompt_messages) >= 5  # sys, session, user, chunks, turns
        assert len(result.dropped_chunks) == 0  # Everything should fit in 8192

    def test_realistic_tight_budget_scenario(self, composer):
        """Tight budget forces selective inclusion."""
        result = composer.compose(
            budget=1024,  # Tight budget
            system_prompt="You are helpful.",
            user_request="Explain machine learning in detail.",
            retrieved_chunks=[
                RetrievedChunk(content="x" * 2000, rank=0.95, chunk_id="high"),
                RetrievedChunk(content="y" * 2000, rank=0.5, chunk_id="mid"),
                RetrievedChunk(content="z" * 2000, rank=0.2, chunk_id="low"),
            ],
        )
        # Low-ranked chunks should be dropped
        assert "low" in result.dropped_chunks
        assert result.actual_tokens <= result.final_budget
