"""Tests for TokenPakAssistant."""

from autogen_tokenpak import TokenPakAssistant


def test_assistant_creation():
    """Test assistant creation."""
    assistant = TokenPakAssistant(name="test", budget=2000)
    assert assistant.name == "test"
    assert assistant.budget == 2000
