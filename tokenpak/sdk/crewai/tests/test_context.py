from crewai_tokenpak.context import TokenPakContext
from crewai_tokenpak.crew import TokenPakCrew
from crewai_tokenpak.handoff import TokenPakHandoff


def test_context_register_agent():
    ctx = TokenPakContext(total_budget=8000)
    ctx.register_agent("researcher", budget=3000)
    assert ctx.get_usage("researcher")["budget"] == 3000


def test_context_add_and_get():
    ctx = TokenPakContext(total_budget=8000)
    ctx.register_agent("writer", budget=5000)
    success = ctx.add_context("writer", "Paris is the capital of France.")
    assert success
    assert "Paris" in ctx.get_context("writer")


def test_context_budget_exceeded():
    ctx = TokenPakContext(total_budget=8000, avg_tokens_per_char=1.0)
    ctx.register_agent("analyst", budget=5)
    ctx.add_context("analyst", "abc")
    success = ctx.add_context("analyst", "this is way too long text that exceeds budget")
    assert not success


def test_context_get_usage():
    ctx = TokenPakContext(total_budget=8000)
    ctx.register_agent("agent1", budget=2000)
    ctx.add_context("agent1", "some content here")
    usage = ctx.get_usage("agent1")
    assert usage["budget"] == 2000
    assert usage["used"] > 0
    assert usage["remaining"] < 2000


def test_handoff_compress_short():
    handoff = TokenPakHandoff(budget=10000)
    state = {"output": "This is the result.", "task": "research"}
    compressed = handoff.compress(state)
    assert compressed["output"] == "This is the result."


def test_handoff_compress_long():
    handoff = TokenPakHandoff(budget=50, avg_tokens_per_char=1.0)
    state = {"output": "A" * 200}
    compressed = handoff.compress(state)
    assert len(compressed["output"]) < 200


def test_handoff_package():
    handoff = TokenPakHandoff(budget=10000)
    result = handoff.package("Agent output", {"source": "web"})
    assert "output" in result


def test_crew_budget_status():
    class MockCrew:
        def kickoff(self, **kwargs):
            return "done"

    crew = TokenPakCrew(crew=MockCrew(), total_budget=8000)
    assert crew.budget_status["total_budget"] == 8000


def test_crew_kickoff():
    class MockCrew:
        def kickoff(self, **kwargs):
            return "mission accomplished"

    crew = TokenPakCrew(crew=MockCrew(), total_budget=8000)
    assert crew.kickoff() == "mission accomplished"
