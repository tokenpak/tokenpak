"""Tests for TokenPakMemory."""

from langchain_tokenpak import TokenPakMemory


def test_memory_add_messages():
    """Test adding messages."""
    memory = TokenPakMemory(max_tokens=2000)
    memory.add_user_message("Hello")
    memory.add_ai_message("Hi!")
    assert len(memory) == 2


def test_memory_clear():
    """Test clearing memory."""
    memory = TokenPakMemory()
    memory.add_user_message("Test")
    assert len(memory) == 1
    memory.clear()
    assert len(memory) == 0
