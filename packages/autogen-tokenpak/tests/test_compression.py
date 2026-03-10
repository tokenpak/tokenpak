"""Unit tests for TokenPak AutoGen context compression."""

import pytest
from autogen_tokenpak import (
    TokenPakConversationHook,
    TokenPakCompressionReport,
    AgentContextConfig,
)


class MockAgent:
    """Mock AutoGen agent for testing."""

    def __init__(self, name: str) -> None:
        """Initialize mock agent."""
        self.name = name
        self.original_get_context_called = False

    def get_context(self) -> dict:
        """Return mock context."""
        self.original_get_context_called = True
        return {
            "system_prompt": "You are a helpful assistant. " * 10,
            "messages": [
                {"role": "user", "content": "Hello world. " * 5},
                {"role": "assistant", "content": "Hi there! " * 5},
            ],
            "tools": [
                {
                    "name": "search",
                    "description": "Search for information on the internet. " * 3,
                    "parameters": {
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query string. " * 2,
                            }
                        }
                    },
                },
            ],
        }


class TestTokenPakCompressionReport:
    """Tests for TokenPakCompressionReport."""

    def test_report_creation(self) -> None:
        """Test creating a compression report."""
        report = TokenPakCompressionReport(
            agent_name="test_agent",
            original_tokens=1000,
            compressed_tokens=700,
            compression_ratio=0.3,
            messages_compressed=5,
            tools_compressed=1,
            system_prompt_length=100,
        )
        assert report.agent_name == "test_agent"
        assert report.original_tokens == 1000
        assert report.compressed_tokens == 700
        assert report.compression_ratio == 0.3

    def test_report_to_dict(self) -> None:
        """Test converting report to dictionary."""
        report = TokenPakCompressionReport(
            agent_name="test_agent",
            original_tokens=1000,
            compressed_tokens=700,
            compression_ratio=0.3,
            messages_compressed=5,
            tools_compressed=1,
            system_prompt_length=100,
        )
        report_dict = report.to_dict()
        assert isinstance(report_dict, dict)
        assert report_dict["agent_name"] == "test_agent"
        assert report_dict["compression_ratio"] == "30.00%"

    def test_report_str(self) -> None:
        """Test string representation of report."""
        report = TokenPakCompressionReport(
            agent_name="test_agent",
            original_tokens=1000,
            compressed_tokens=700,
            compression_ratio=0.3,
            messages_compressed=5,
            tools_compressed=1,
            system_prompt_length=100,
        )
        report_str = str(report)
        assert "TokenPak Compression Report" in report_str
        assert "test_agent" in report_str
        assert "1000" in report_str


class TestAgentContextConfig:
    """Tests for AgentContextConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = AgentContextConfig()
        assert config.max_tokens == 4096
        assert config.preserve_recent_messages == 5
        assert config.compress_system_prompt is True
        assert config.compress_tools is True
        assert config.compress_history is True

    def test_custom_config(self) -> None:
        """Test custom configuration."""
        config = AgentContextConfig(
            max_tokens=2048,
            preserve_recent_messages=3,
            compress_system_prompt=False,
        )
        assert config.max_tokens == 2048
        assert config.preserve_recent_messages == 3
        assert config.compress_system_prompt is False
        assert config.compress_tools is True


class TestTokenPakConversationHook:
    """Tests for TokenPakConversationHook."""

    def test_hook_initialization(self) -> None:
        """Test hook initialization."""
        hook = TokenPakConversationHook()
        assert len(hook.agents_patched) == 0
        assert len(hook.reports) == 0

    def test_compress_agent(self) -> None:
        """Test patching an agent with compression."""
        hook = TokenPakConversationHook()
        agent = MockAgent("test_agent")

        hook.compress_agent(agent)

        assert "test_agent" in hook.agents_patched
        assert agent.get_context is not None

    def test_get_context_compression(self) -> None:
        """Test that get_context applies compression."""
        hook = TokenPakConversationHook()
        agent = MockAgent("test_agent")
        hook.compress_agent(agent)

        context = agent.get_context()

        assert "system_prompt" in context
        assert "messages" in context
        assert "tools" in context
        assert agent.original_get_context_called is True

    def test_compression_report_generated(self) -> None:
        """Test that compression report is generated."""
        hook = TokenPakConversationHook()
        agent = MockAgent("test_agent")
        hook.compress_agent(agent)

        context = agent.get_context()
        report = hook.get_report("test_agent")

        assert report is not None
        assert report.agent_name == "test_agent"
        assert report.original_tokens > 0
        assert report.compressed_tokens > 0

    def test_restore_agent(self) -> None:
        """Test restoring agent to original state."""
        hook = TokenPakConversationHook()
        agent = MockAgent("test_agent")
        hook.compress_agent(agent)

        assert "test_agent" in hook.agents_patched

        hook.restore_agent(agent)

        assert "test_agent" not in hook.agents_patched

    def test_multiple_agents(self) -> None:
        """Test patching multiple agents."""
        hook = TokenPakConversationHook()
        agent1 = MockAgent("agent1")
        agent2 = MockAgent("agent2")

        hook.compress_agent(agent1)
        hook.compress_agent(agent2)

        assert len(hook.agents_patched) == 2
        assert "agent1" in hook.agents_patched
        assert "agent2" in hook.agents_patched

    def test_text_normalization(self) -> None:
        """Test text normalization."""
        text = "Hello  \n  world   \n   test"
        normalized = TokenPakConversationHook._normalize_text(text)
        assert normalized == "Hello world test"
        assert "  " not in normalized
        assert "\n" not in normalized

    def test_token_estimation(self) -> None:
        """Test token estimation."""
        text = "a" * 100
        tokens = TokenPakConversationHook._estimate_tokens(text)
        # Expecting ~100/4 = 25 tokens
        assert tokens >= 20 and tokens <= 30

    def test_context_with_empty_messages(self) -> None:
        """Test compression with empty messages list."""
        hook = TokenPakConversationHook()
        agent = MockAgent("test_agent")

        # Override get_context to return empty messages
        original_get_context = agent.get_context

        def mock_get_context() -> dict:
            context = original_get_context()
            context["messages"] = []
            return context

        agent.get_context = mock_get_context
        hook.compress_agent(agent, AgentContextConfig())

        # Patch the agent's get_context to use our wrapper
        agent.get_context = lambda: mock_get_context()

    def test_context_without_optional_fields(self) -> None:
        """Test compression with missing optional fields."""
        hook = TokenPakConversationHook()

        class MinimalAgent:
            name = "minimal"

            def get_context(self) -> dict:
                return {"system_prompt": "Hello"}

        agent = MinimalAgent()  # type: ignore
        hook.compress_agent(agent, AgentContextConfig())

        context = agent.get_context()
        assert "system_prompt" in context
        report = hook.get_report("minimal")
        assert report is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
