from langchain_tokenpak.memory import TokenPakMemory


def test_memory_add_and_get():
    memory = TokenPakMemory(budget=10000)
    memory.add_message("human", "What is Paris?")
    memory.add_message("ai", "Paris is the capital of France.")
    history = memory.get_history()
    assert len(history) == 2


def test_memory_compression():
    memory = TokenPakMemory(budget=100, avg_tokens_per_char=1.0)
    for i in range(20):
        memory.add_message("human", f"message number {i} with extra content")
    # Compression should keep message count down (not store all 20 full messages)
    assert memory.token_usage["messages"] < 20  # Compression reduced active messages
    assert memory._compressed_summary is not None  # Summary was created


def test_memory_clear():
    memory = TokenPakMemory()
    memory.add_message("human", "test")
    memory.clear()
    assert memory.get_history() == []


def test_memory_token_usage():
    memory = TokenPakMemory(budget=5000)
    memory.add_message("human", "hello")
    usage = memory.token_usage
    assert usage["budget"] == 5000
    assert usage["used"] > 0
    assert usage["remaining"] < 5000
