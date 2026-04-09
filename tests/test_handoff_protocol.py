"""Tests for the TokenPak agent handoff protocol.

Covers:
  - HandoffBlock creation and serialization
  - TokenPak add/get/remove/to_prompt
  - HandoffWire to_wire/from_wire round-trip
  - Top-level tokenpak imports
  - CrewAI TokenPakHandoff integration
  - AutoGen TokenPakAssistant integration
"""

from __future__ import annotations

import json
import pytest


# ---------------------------------------------------------------------------
# Top-level imports
# ---------------------------------------------------------------------------

def test_top_level_imports():
    from tokenpak import (
        TokenPak,
        Handoff,
        HandoffBlock,
        HandoffManager,
        ContextRef,
        HandoffStatus,
    )
    assert TokenPak is not None
    assert Handoff is not None
    assert HandoffBlock is not None


# ---------------------------------------------------------------------------
# HandoffBlock
# ---------------------------------------------------------------------------

def test_handoff_block_basic():
    from tokenpak import HandoffBlock
    b = HandoffBlock(type="memory", id="state_1", content="some content")
    assert b.type == "memory"
    assert b.id == "state_1"
    assert b.content == "some content"


def test_handoff_block_round_trip():
    from tokenpak import HandoffBlock
    b = HandoffBlock(type="evidence", id="ev1", content="finding", metadata={"score": 0.9})
    d = b.to_dict()
    b2 = HandoffBlock.from_dict(d)
    assert b2.type == b.type
    assert b2.id == b.id
    assert b2.content == b.content
    assert b2.metadata == b.metadata


# ---------------------------------------------------------------------------
# TokenPak
# ---------------------------------------------------------------------------

def test_token_pak_add_and_get():
    from tokenpak import TokenPak, HandoffBlock
    pack = TokenPak()
    block = HandoffBlock(type="memory", id="m1", content="data")
    pack.add(block)
    assert len(pack) == 1
    assert pack.get("m1") is block


def test_token_pak_chaining():
    from tokenpak import TokenPak, HandoffBlock
    pack = (
        TokenPak()
        .add(HandoffBlock(type="memory", id="a", content="aaa"))
        .add(HandoffBlock(type="evidence", id="b", content="bbb"))
    )
    assert len(pack) == 2


def test_token_pak_blocks_by_type():
    from tokenpak import TokenPak, HandoffBlock
    pack = TokenPak()
    pack.add(HandoffBlock(type="memory", id="m1", content="1"))
    pack.add(HandoffBlock(type="memory", id="m2", content="2"))
    pack.add(HandoffBlock(type="evidence", id="e1", content="3"))
    assert len(pack.blocks_by_type("memory")) == 2
    assert len(pack.blocks_by_type("evidence")) == 1


def test_token_pak_remove():
    from tokenpak import TokenPak, HandoffBlock
    pack = TokenPak()
    pack.add(HandoffBlock(type="memory", id="m1", content="x"))
    assert pack.remove("m1") is True
    assert len(pack) == 0
    assert pack.remove("m1") is False


def test_token_pak_to_prompt_empty():
    from tokenpak import TokenPak
    assert TokenPak().to_prompt() == ""


def test_token_pak_to_prompt_format():
    from tokenpak import TokenPak, HandoffBlock
    pack = TokenPak()
    pack.add(HandoffBlock(type="memory", id="s1", content="state here"))
    pack.add(HandoffBlock(type="evidence", id="e1", content="evidence here"))
    prompt = pack.to_prompt()
    assert "=== MEMORY [s1] ===" in prompt
    assert "state here" in prompt
    assert "=== EVIDENCE [e1] ===" in prompt
    assert "evidence here" in prompt


def test_token_pak_round_trip():
    from tokenpak import TokenPak, HandoffBlock
    pack = TokenPak()
    pack.add(HandoffBlock(type="memory", id="x", content="hello"))
    d = pack.to_dict()
    pack2 = TokenPak.from_dict(d)
    assert len(pack2) == 1
    assert pack2.get("x").content == "hello"


# ---------------------------------------------------------------------------
# HandoffWire (exported as Handoff from top-level)
# ---------------------------------------------------------------------------

def test_handoff_wire_basic():
    from tokenpak import Handoff, TokenPak, HandoffBlock
    pack = TokenPak()
    pack.add(HandoffBlock(type="memory", id="t", content="task state"))
    h = Handoff(pack=pack, from_agent="cali", to_agent="sue")
    assert h.from_agent == "cali"
    assert h.to_agent == "sue"
    assert h.id is not None


def test_handoff_wire_round_trip():
    from tokenpak import Handoff, TokenPak, HandoffBlock
    pack = TokenPak()
    pack.add(HandoffBlock(type="memory", id="task_state", content="some state"))
    pack.add(HandoffBlock(type="evidence", id="findings", content="research output"))
    h = Handoff(pack=pack, from_agent="cali", to_agent="sue", summary="Done X")
    wire = h.to_wire()
    h2 = Handoff.from_wire(wire)
    assert h2.from_agent == "cali"
    assert h2.to_agent == "sue"
    assert h2.id == h.id
    assert h2.summary == "Done X"
    prompt = h2.pack.to_prompt()
    assert "some state" in prompt
    assert "research output" in prompt


def test_handoff_wire_invalid_json():
    from tokenpak import Handoff
    with pytest.raises(ValueError, match="Invalid wire format"):
        Handoff.from_wire("not-json")


def test_handoff_wire_unknown_version():
    from tokenpak import Handoff
    with pytest.raises(ValueError, match="Unrecognised wire version"):
        Handoff.from_wire(json.dumps({"version": "other:1"}))


def test_handoff_wire_metadata():
    from tokenpak import Handoff, TokenPak
    h = Handoff(
        pack=TokenPak(),
        from_agent="cali",
        to_agent="sue",
        metadata={"sprint": 7, "priority": "p1"},
    )
    wire = h.to_wire()
    h2 = Handoff.from_wire(wire)
    assert h2.metadata["sprint"] == 7
    assert h2.metadata["priority"] == "p1"


# ---------------------------------------------------------------------------
# CrewAI TokenPakHandoff
# ---------------------------------------------------------------------------

def test_crewai_prepare_receive_wire(tmp_path):
    from crewai_tokenpak import TokenPakHandoff
    from tokenpak.agent.agentic.handoff import HandoffManager
    mgr = HandoffManager(handoff_dir=tmp_path / "hf")
    h = TokenPakHandoff(budget=1000, manager=mgr)
    wire = h.prepare_handoff(
        state={"key": "value", "step": 3},
        from_agent="cali",
        to_agent="sue",
        what_was_done="Researched topic",
        whats_next="Write report",
    )
    assert wire  # non-empty string
    result = h.receive_handoff_wire(wire)
    assert "prompt" in result
    assert "pack" in result
    assert "key: \"value\"" in result["prompt"] or "key:" in result["prompt"]


def test_crewai_legacy_dict_api():
    from crewai_tokenpak import TokenPakHandoff
    h = TokenPakHandoff(budget=500)
    state = {"a": 1, "b": 2}
    out = h.receive_handoff(state)
    assert out == state


def test_crewai_unknown_agents_no_crash(tmp_path):
    """Unknown agents: wire still produced, HandoffManager just skips."""
    from crewai_tokenpak import TokenPakHandoff
    from tokenpak.agent.agentic.handoff import HandoffManager
    mgr = HandoffManager(handoff_dir=tmp_path / "hf2")
    h = TokenPakHandoff(budget=1000, manager=mgr)
    wire = h.prepare_handoff(
        state={"x": 1},
        from_agent="unknown_bot",
        to_agent="mystery",
    )
    assert wire  # wire still produced even if HandoffManager skips


# ---------------------------------------------------------------------------
# AutoGen TokenPakAssistant
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__('importlib').util.find_spec('autogen_tokenpak'),
    reason='autogen_tokenpak package not installed'
)
def test_autogen_assistant_creation():
    from autogen_tokenpak import TokenPakAssistant
    a = TokenPakAssistant(name="alice", budget=2000)
    assert a.name == "alice"
    assert a.budget == 2000



@pytest.mark.skipif(
    not __import__('importlib').util.find_spec('autogen_tokenpak'),
    reason='autogen_tokenpak package not installed'
)
def test_autogen_receive_and_compress():
    from autogen_tokenpak import TokenPakAssistant
    a = TokenPakAssistant(name="alice", budget=2000)
    a.receive_message("Hello", sender_name="user")
    a.receive_message("Do the task", sender_name="boss")
    msgs = a.get_messages(compress=True)
    assert len(msgs) >= 1



@pytest.mark.skipif(
    not __import__('importlib').util.find_spec('autogen_tokenpak'),
    reason='autogen_tokenpak package not installed'
)
def test_autogen_prepare_apply_handoff(tmp_path):
    from autogen_tokenpak import TokenPakAssistant
    from tokenpak.agent.agentic.handoff import HandoffManager
    mgr = HandoffManager(handoff_dir=tmp_path / "hf3")
    alice = TokenPakAssistant(name="cali", budget=2000, manager=mgr)
    bob   = TokenPakAssistant(name="sue",  budget=2000, manager=mgr)

    alice.receive_message("Research quantum computing", sender_name="user")
    wire = alice.prepare_handoff(
        to_agent="sue",
        what_was_done="Researched quantum computing",
        whats_next="Write the report",
    )
    assert wire

    pack = bob.apply_handoff_wire(wire)
    assert len(pack) >= 1
    # Bob should now have conversation history
    msgs = bob.get_messages(compress=False)
    assert any("quantum" in m.get("content", "") for m in msgs)



@pytest.mark.skipif(
    not __import__('importlib').util.find_spec('autogen_tokenpak'),
    reason='autogen_tokenpak package not installed'
)
def test_autogen_handoff_wire_round_trip(tmp_path):
    from autogen_tokenpak import TokenPakAssistant
    from tokenpak import HandoffBlock, Handoff
    from tokenpak.agent.agentic.handoff import HandoffManager
    mgr = HandoffManager(handoff_dir=tmp_path / "hf4")
    a = TokenPakAssistant(name="cali", budget=2000, manager=mgr)
    extra = [HandoffBlock(type="evidence", id="ev1", content="key finding")]
    wire = a.prepare_handoff(
        to_agent="sue",
        what_was_done="Done A",
        whats_next="Next B",
        extra_blocks=extra,
    )
    h = Handoff.from_wire(wire)
    assert h.pack.get("ev1") is not None
    assert h.pack.get("ev1").content == "key finding"
