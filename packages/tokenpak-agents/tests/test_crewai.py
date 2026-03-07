"""Tests for tokenpak_agents.crewai."""

from tokenpak_agents.crewai import TokenPakContext, TokenPakCrew, TokenPakHandoff
from tokenpak.agent.agentic.handoff import HandoffBlock


def test_context_default_per_agent_budget():
    ctx = TokenPakContext(total_budget=8000)
    assert ctx.allocate_budget("a") == 2000


def test_context_custom_per_agent_budget():
    ctx = TokenPakContext(total_budget=8000, per_agent_budget=123)
    assert ctx.allocate_budget("a") == 123


def test_context_record_usage_tracks_value():
    ctx = TokenPakContext()
    ctx.record_usage("a", 300)
    assert ctx.get_usage()["a"] == 300


def test_context_record_usage_clamps_negative_to_zero():
    ctx = TokenPakContext()
    ctx.record_usage("a", -3)
    assert ctx.get_usage()["a"] == 0


def test_context_get_usage_returns_copy():
    ctx = TokenPakContext()
    ctx.record_usage("a", 20)
    usage = ctx.get_usage()
    usage["a"] = 99
    assert ctx.get_usage()["a"] == 20


def test_context_remaining_budget():
    ctx = TokenPakContext(total_budget=100)
    ctx.record_usage("a", 30)
    ctx.record_usage("b", 20)
    assert ctx.remaining_budget() == 50


def test_context_remaining_budget_floor_zero():
    ctx = TokenPakContext(total_budget=10)
    ctx.record_usage("a", 999)
    assert ctx.remaining_budget() == 0


def test_context_reset_usage():
    ctx = TokenPakContext()
    ctx.record_usage("a", 1)
    ctx.reset_usage()
    assert ctx.get_usage() == {}


def test_handoff_prepare_and_receive_wire():
    handoff = TokenPakHandoff()
    wire = handoff.prepare_handoff({"k": "v"}, from_agent="alice", to_agent="bob")
    unpacked = handoff.receive_handoff_wire(wire)
    assert unpacked["from_agent"] == "alice"
    assert unpacked["to_agent"] == "bob"
    assert "k: v" in unpacked["prompt"]


def test_handoff_prepare_sets_summary_when_fields_provided():
    handoff = TokenPakHandoff()
    wire = handoff.prepare_handoff(
        {"x": "y"},
        from_agent="a",
        to_agent="b",
        what_was_done="research",
        whats_next="write",
    )
    unpacked = handoff.receive_handoff_wire(wire)
    assert unpacked["summary"] == "Done: research | Next: write"


def test_handoff_prepare_without_summary_fields_is_empty_summary():
    handoff = TokenPakHandoff()
    wire = handoff.prepare_handoff({"x": "y"}, from_agent="a", to_agent="b")
    unpacked = handoff.receive_handoff_wire(wire)
    assert unpacked["summary"] == ""


def test_handoff_prepare_accepts_extra_blocks():
    handoff = TokenPakHandoff()
    wire = handoff.prepare_handoff(
        {"x": "y"},
        from_agent="a",
        to_agent="b",
        extra_blocks=[HandoffBlock(type="note", id="n1", content="hello")],
    )
    unpacked = handoff.receive_handoff_wire(wire)
    prompt = unpacked["prompt"]
    assert "hello" in prompt


def test_handoff_prepare_handoff_dict_trims_by_keep_recent():
    handoff = TokenPakHandoff(keep_recent=2)
    trimmed = handoff.prepare_handoff_dict({"a": 1, "b": 2, "c": 3})
    assert trimmed == {"b": 2, "c": 3}


def test_handoff_prepare_handoff_dict_no_trim_if_small():
    handoff = TokenPakHandoff(keep_recent=4)
    state = {"a": 1, "b": 2}
    assert handoff.prepare_handoff_dict(state) == state


def test_handoff_receive_handoff_dict_passthrough():
    handoff = TokenPakHandoff()
    state = {"a": 1}
    assert handoff.receive_handoff(state) == state


def test_handoff_compress_state_handles_non_string_values():
    handoff = TokenPakHandoff()
    text = handoff._compress_state({"obj": {"a": 1}})
    assert text.startswith("obj: {")


def test_crew_constructor_properties():
    crew = TokenPakCrew(agents=[1, 2], tasks=["t1"], budget=1234, mode="x")
    assert crew.agents == [1, 2]
    assert crew.tasks == ["t1"]
    assert crew.budget == 1234
    assert crew.kwargs["mode"] == "x"


def test_crew_kickoff_returns_shape():
    crew = TokenPakCrew(agents=[1], tasks=[2], budget=88)
    result = crew.kickoff(foo="bar")
    assert result["output"] == "Crew execution result"
    assert result["inputs"] == {"foo": "bar"}
    assert result["agent_count"] == 1
    assert result["task_count"] == 1
    assert result["budget"] == 88


async def test_crew_akickoff_matches_kickoff_output():
    crew = TokenPakCrew(agents=[], tasks=[])
    sync = crew.kickoff(x=1)
    async_result = await crew.akickoff(x=1)
    assert async_result == sync
