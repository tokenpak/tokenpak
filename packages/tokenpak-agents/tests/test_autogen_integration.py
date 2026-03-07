"""Tests for AutoGen TokenPak integration."""

import pytest
from tokenpak_agents.autogen import TokenPakAssistant, TokenPakGroupChat, TokenPakMessage


class TestTokenPakAssistant:
    
    def test_assistant_creation(self):
        """Test assistant initialization."""
        assistant = TokenPakAssistant(
            name="assistant",
            context_budget=4000,
        )
        assert assistant.name == "assistant"
        assert assistant.context_budget == 4000
    
    def test_receive_message(self):
        """Test receiving a message."""
        assistant = TokenPakAssistant(name="assistant")
        
        class MockSender:
            name = "sender"
        
        assistant.receive("Hello, assistant!", MockSender())
        assert len(assistant._message_history) > 0
    
    def test_send_message(self):
        """Test sending a message."""
        assistant = TokenPakAssistant(name="assistant")
        
        class MockRecipient:
            name = "recipient"
        
        assistant.send("Hello, world!", MockRecipient())
        assert len(assistant._message_history) > 0


class TestTokenPakGroupChat:
    
    def test_groupchat_creation(self):
        """Test group chat initialization."""
        agents = [{"name": "agent1"}, {"name": "agent2"}]
        chat = TokenPakGroupChat(
            agents=agents,
            context_budget=8000,
        )
        assert len(chat.agents) == 2
        assert chat.context_budget == 8000
    
    def test_add_message(self):
        """Test adding message to group chat."""
        chat = TokenPakGroupChat(agents=[])
        chat.add_message("agent1", "Hello, everyone!")
        
        assert len(chat.messages) == 1
        assert chat.messages[0]["agent"] == "agent1"
    
    def test_get_history(self):
        """Test retrieving chat history."""
        chat = TokenPakGroupChat(agents=[])
        chat.add_message("agent1", "First message")
        chat.add_message("agent2", "Second message")
        
        history = chat.get_history()
        assert len(history) == 2
        assert history[0]["agent"] == "agent1"


class TestTokenPakMessage:
    
    def test_message_with_content(self):
        """Test TokenPak message creation."""
        msg = TokenPakMessage(content="Hello, world!")
        assert msg.content == "Hello, world!"
        assert "Hello, world!" in str(msg)
    
    def test_message_with_pack(self):
        """Test TokenPak message with pack data."""
        pack = {"blocks": []}
        msg = TokenPakMessage(pack=pack)
        
        msg_str = str(msg)
        assert "TokenPak" in msg_str
