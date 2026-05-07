from autogen_tokenpak.assistant import TokenPakAssistant
from autogen_tokenpak.groupchat import TokenPakGroupChat
from autogen_tokenpak.message import TokenPakMessage, compress_messages


def test_message_basic():
    msg = TokenPakMessage(role="user", content="What is Paris?")
    assert msg.role == "user"
    assert msg.content == "What is Paris?"
    assert msg.token_count >= 1


def test_message_budget_trim():
    msg = TokenPakMessage(
        role="user", content="A" * 200, budget=10, avg_tokens_per_char=0.25
    )
    assert len(msg.content) <= 45


def test_message_to_dict():
    msg = TokenPakMessage(role="assistant", content="Paris is in France.")
    d = msg.to_dict()
    assert d["role"] == "assistant"
    assert d["content"] == "Paris is in France."


def test_compress_messages_small():
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    result = compress_messages(messages, budget=10000)
    assert len(result) == 2


def test_compress_messages_large():
    messages = [{"role": "user", "content": "A" * 1000} for _ in range(20)]
    result = compress_messages(messages, budget=500, avg_tokens_per_char=0.25)
    assert len(result) <= 20


def test_compress_messages_keeps_recent():
    messages = [
        {"role": "user", "content": "old message"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "recent message"},
    ]
    result = compress_messages(messages, budget=20)
    contents = [m["content"] for m in result]
    assert any("recent" in c for c in contents)


class MockAgent:
    name = "mock_assistant"

    def generate_reply(self, messages=None, sender=None, **kwargs):
        return "mock reply"

    def initiate_chat(self, recipient, message, **kwargs):
        return "chat initiated"


def test_assistant_create():
    agent = TokenPakAssistant(agent=MockAgent(), budget=6000)
    assert agent.budget == 6000
    assert agent.name == "mock_assistant"


def test_assistant_compress_history():
    agent = TokenPakAssistant(agent=MockAgent(), budget=1000)
    history = [{"role": "user", "content": "message"}] * 5
    compressed = agent.compress_history(history)
    assert isinstance(compressed, list)


def test_assistant_budget_status():
    agent = TokenPakAssistant(agent=MockAgent(), budget=6000)
    assert agent.budget_status["budget"] == 6000


class MockGroupChat:
    messages = [{"role": "user", "content": "test"}]


class MockManager:
    pass


def test_groupchat_create():
    tpgc = TokenPakGroupChat(
        groupchat=MockGroupChat(), manager=MockManager(), budget=4000
    )
    assert tpgc.budget == 4000


def test_groupchat_get_history():
    tpgc = TokenPakGroupChat(
        groupchat=MockGroupChat(), manager=MockManager(), budget=4000
    )
    history = tpgc.get_compressed_history()
    assert isinstance(history, list)


def test_groupchat_message_count():
    tpgc = TokenPakGroupChat(
        groupchat=MockGroupChat(), manager=MockManager(), budget=4000
    )
    assert tpgc.message_count == 1


def test_groupchat_budget_status():
    tpgc = TokenPakGroupChat(
        groupchat=MockGroupChat(), manager=MockManager(), budget=4000
    )
    assert tpgc.budget_status["budget"] == 4000
