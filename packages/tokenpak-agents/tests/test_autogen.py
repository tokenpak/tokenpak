"""Tests for tokenpak_agents.autogen."""

from tokenpak.agent.agentic.handoff import HandoffBlock
from tokenpak_agents.autogen import TokenPakAssistant, TokenPakGroupChat, TokenPakMessage


def test_message_compress_content_no_change_when_short():
    assert TokenPakMessage.compress_content("hello", max_tokens=10) == "hello"


def test_message_compress_content_truncates_when_long():
    text = "a" * 1000
    out = TokenPakMessage.compress_content(text, max_tokens=10)
    assert out.endswith("...")
    assert len(out) == 43


def test_message_compress_content_zero_max_tokens():
    assert TokenPakMessage.compress_content("abc", max_tokens=0) == "..."


def test_message_compress_message_preserves_keys():
    msg = {"role": "user", "content": "x" * 1000, "meta": 1}
    out = TokenPakMessage.compress_message(msg, max_tokens=5)
    assert out["role"] == "user"
    assert out["meta"] == 1
    assert out["content"].endswith("...")


def test_assistant_constructor_properties():
    assistant = TokenPakAssistant(name="alice", budget=500, temp=0.7)
    assert assistant.name == "alice"
    assert assistant.budget == 500
    assert assistant.kwargs["temp"] == 0.7


def test_assistant_receive_message_with_sender_name():
    assistant = TokenPakAssistant(name="alice")
    assistant.receive_message("hello", sender_name="bob")
    assert assistant.get_messages(compress=False)[0]["role"] == "bob"


def test_assistant_receive_message_with_sender_object_name():
    class Sender:
        name = "charlie"

    assistant = TokenPakAssistant(name="alice")
    assistant.receive_message("hello", sender=Sender())
    assert assistant.get_messages(compress=False)[0]["role"] == "charlie"


def test_assistant_receive_message_default_role_agent():
    assistant = TokenPakAssistant(name="alice")
    assistant.receive_message("hello")
    assert assistant.get_messages(compress=False)[0]["role"] == "agent"


def test_assistant_get_messages_uncompressed_copy():
    assistant = TokenPakAssistant(name="alice")
    assistant.receive_message("hello", sender_name="u")
    msgs = assistant.get_messages(compress=False)
    msgs.append({"role": "x", "content": "y"})
    assert len(assistant.get_messages(compress=False)) == 1


def test_assistant_generate_reply_format():
    assistant = TokenPakAssistant(name="alice")
    assistant.receive_message("hello", sender_name="u")
    assert assistant.generate_reply() == "[alice reply based on 1 messages]"


def test_assistant_prepare_and_apply_handoff_roundtrip():
    alice = TokenPakAssistant(name="alice")
    bob = TokenPakAssistant(name="bob")
    alice.receive_message("work item", sender_name="user")
    wire = alice.prepare_handoff(to_agent="bob", what_was_done="step1", whats_next="step2")
    pack = bob.apply_handoff_wire(wire)
    assert pack.to_prompt()
    assert bob.get_messages(compress=False)[0]["role"] == "alice"


def test_assistant_prepare_handoff_includes_extra_blocks():
    assistant = TokenPakAssistant(name="a")
    wire = assistant.prepare_handoff(
        to_agent="b", extra_blocks=[HandoffBlock(type="note", id="n1", content="extra")]
    )
    receiver = TokenPakAssistant(name="b")
    pack = receiver.apply_handoff_wire(wire)
    assert "extra" in pack.to_prompt()


def test_assistant_prepare_handoff_without_messages_still_valid_wire():
    assistant = TokenPakAssistant(name="a")
    wire = assistant.prepare_handoff(to_agent="b")
    receiver = TokenPakAssistant(name="b")
    pack = receiver.apply_handoff_wire(wire)
    assert isinstance(pack.to_prompt(), str)


def test_assistant_budget_limits_compressed_messages():
    assistant = TokenPakAssistant(name="a", budget=5)
    assistant.receive_message("x" * 60, sender_name="u1")
    assistant.receive_message("y" * 8, sender_name="u2")
    msgs = assistant.get_messages(compress=True)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "u2"


def test_groupchat_add_message_appends():
    chat = TokenPakGroupChat(agents=[])
    chat.add_message({"role": "a", "content": "hello"})
    assert len(chat.messages) == 1


def test_groupchat_compress_history_under_budget_keeps_all():
    chat = TokenPakGroupChat(agents=[], budget=100)
    chat.add_message({"role": "a", "content": "hello"})
    chat.add_message({"role": "b", "content": "world"})
    assert len(chat._compress_history()) == 2


def test_groupchat_compress_history_over_budget_keeps_tail():
    chat = TokenPakGroupChat(agents=[], budget=3)
    chat.add_message({"role": "a", "content": "x" * 40})
    chat.add_message({"role": "b", "content": "ok"})
    compressed = chat._compress_history()
    assert len(compressed) == 1
    assert compressed[0]["role"] == "b"
