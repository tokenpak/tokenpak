"""Tests for TokenPakContext."""

from crewai_tokenpak import TokenPakContext


def test_context_allocation():
    """Test budget allocation."""
    ctx = TokenPakContext(total_budget=4000)
    budget = ctx.allocate_budget("agent_1")
    assert budget == 1000  # 4000 // 4
