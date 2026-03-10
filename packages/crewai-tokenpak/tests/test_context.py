"""Tests for TokenPakContext helpers."""

from crewai_tokenpak import AgentContextConfig, TokenPakContext


def test_default_context_allocation() -> None:
    ctx = TokenPakContext(total_budget=4000)
    assert ctx.allocate_budget("agent_1") == 1000


def test_agent_override_budget() -> None:
    ctx = TokenPakContext(
        total_budget=4000,
        agent_overrides={"writer": AgentContextConfig(budget=777)},
    )
    assert ctx.allocate_budget("writer") == 777
    assert ctx.allocate_budget("researcher") == 1000


def test_usage_recording_and_report() -> None:
    ctx = TokenPakContext(total_budget=100, per_agent_budget=25)
    ctx.record_usage("a", 12)
    ctx.record_usage("b", -5)
    assert ctx.get_usage() == {"a": 12, "b": 0}
    assert ctx.remaining_budget() == 88
    assert ctx.report()["agents_tracked"] == 2


def test_reset_usage() -> None:
    ctx = TokenPakContext(total_budget=100)
    ctx.record_usage("a", 50)
    ctx.reset_usage()
    assert ctx.get_usage() == {}
    assert ctx.remaining_budget() == 100


def test_compress_text_under_budget_normalizes_whitespace() -> None:
    ctx = TokenPakContext(total_budget=100)
    result = ctx.compress_text("  hello  \n\n world \n", budget=20)
    assert result.text == "hello\nworld"
    assert result.compressed_tokens <= 20


def test_compress_text_dedupes_repeated_lines() -> None:
    ctx = TokenPakContext(total_budget=100)
    text = "\n".join(["repeat this line"] * 12)
    result = ctx.compress_text(text, budget=10)
    assert result.was_compressed is True
    assert result.text == "repeat this line"


def test_compress_text_clips_when_needed() -> None:
    ctx = TokenPakContext(total_budget=100)
    text = "A" * 500
    result = ctx.compress_text(text, budget=8)
    assert result.was_compressed is True
    assert result.compressed_tokens <= 8
    assert " ... " in result.text or len(result.text) <= 32
