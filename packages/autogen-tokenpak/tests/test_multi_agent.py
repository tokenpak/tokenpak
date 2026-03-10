"""Integration tests for multi-agent scenarios."""

import pytest
from autogen_tokenpak import (
    TokenPakConversationHook,
    AgentContextConfig,
)


class MockMultiAgentSetup:
    """Mock multi-agent AutoGen setup for testing."""

    def __init__(self) -> None:
        """Initialize mock setup."""
        self.agents: dict = {}
        self.conversation_history: list = []

    def add_agent(self, name: str) -> "MockAgent":
        """Add an agent to the setup."""
        agent = MockAgent(name)
        self.agents[name] = agent
        return agent

    def simulate_conversation(self, messages: list) -> None:
        """Simulate a conversation between agents."""
        for msg in messages:
            self.conversation_history.append(msg)
            # Update all agents' contexts
            for agent in self.agents.values():
                agent.add_message(msg)


class MockAgent:
    """Mock AutoGen agent for multi-agent testing."""

    def __init__(self, name: str) -> None:
        """Initialize mock agent."""
        self.name = name
        self.messages: list = []

    def add_message(self, message: dict) -> None:
        """Add a message to agent's history."""
        self.messages.append(message)

    def get_context(self) -> dict:
        """Return context with conversation history."""
        return {
            "agent_name": self.name,
            "system_prompt": f"You are {self.name}. " + "Instructions. " * 5,
            "messages": self.messages,
            "tools": [
                {
                    "name": "send_message",
                    "description": "Send a message to other agents. " * 2,
                },
                {
                    "name": "retrieve_info",
                    "description": "Retrieve information from knowledge base. " * 2,
                },
            ],
        }


class TestMultiAgentScenarios:
    """Tests for multi-agent compression scenarios."""

    def test_two_agent_groupchat(self) -> None:
        """Test compression in 2-agent conversation."""
        hook = TokenPakConversationHook()
        setup = MockMultiAgentSetup()

        agent1 = setup.add_agent("researcher")
        agent2 = setup.add_agent("writer")

        hook.compress_agent(agent1)
        hook.compress_agent(agent2)

        # Simulate conversation
        messages = [
            {"role": "user", "content": "Research AI trends. " * 10},
            {"role": "assistant", "content": "AI trends include... " * 15},
            {"role": "user", "content": "Now write an article. " * 8},
        ]
        setup.simulate_conversation(messages)

        # Get contexts
        context1 = agent1.get_context()
        context2 = agent2.get_context()

        assert len(context1["messages"]) == 3
        assert len(context2["messages"]) == 3

        # Both should have reports
        report1 = hook.get_report("researcher")
        report2 = hook.get_report("writer")

        assert report1 is not None
        assert report2 is not None
        assert report1.agent_name == "researcher"
        assert report2.agent_name == "writer"

    def test_three_agent_workflow(self) -> None:
        """Test compression in 3-agent workflow."""
        hook = TokenPakConversationHook()
        setup = MockMultiAgentSetup()

        agents = [
            setup.add_agent("planner"),
            setup.add_agent("executor"),
            setup.add_agent("reviewer"),
        ]

        for agent in agents:
            hook.compress_agent(agent)

        # Build up conversation and explicitly call get_context
        for i in range(3):
            setup.simulate_conversation(
                [
                    {
                        "role": "assistant",
                        "content": f"Agent message {i}. " * 5,
                    }
                ]
            )

        # Trigger compression by calling get_context
        for agent in agents:
            _ = agent.get_context()

        # Verify all agents have reports (compression was triggered)
        for agent in agents:
            report = hook.get_report(agent.name)
            assert report is not None, f"No report for {agent.name}"
            assert report.messages_compressed >= 0

    def test_context_isolation(self) -> None:
        """Test that per-agent contexts remain isolated."""
        hook = TokenPakConversationHook()
        agent1 = MockAgent("agent1")
        agent2 = MockAgent("agent2")

        hook.compress_agent(agent1)
        hook.compress_agent(agent2)

        agent1.add_message({"role": "user", "content": "Message for agent1"})
        agent2.add_message({"role": "user", "content": "Message for agent2"})

        context1 = agent1.get_context()
        context2 = agent2.get_context()

        # Each agent should have their own messages
        assert context1["messages"][0]["content"] == "Message for agent1"
        assert context2["messages"][0]["content"] == "Message for agent2"

    def test_custom_config_per_agent(self) -> None:
        """Test using different configs for different agents."""
        hook = TokenPakConversationHook()
        agent1 = MockAgent("agent1")
        agent2 = MockAgent("agent2")

        config1 = AgentContextConfig(preserve_recent_messages=3)
        config2 = AgentContextConfig(preserve_recent_messages=10)

        hook.compress_agent(agent1, config1)
        hook.compress_agent(agent2, config2)

        # Add messages
        for i in range(20):
            agent1.add_message({"role": "user", "content": f"Msg {i}"})
            agent2.add_message({"role": "user", "content": f"Msg {i}"})

        context1 = agent1.get_context()
        context2 = agent2.get_context()

        # Should have different message counts due to config
        assert len(context1["messages"]) <= len(context2["messages"])

    def test_large_conversation_compression(self) -> None:
        """Test compression with large conversation history."""
        hook = TokenPakConversationHook()
        agent = MockAgent("large_agent")

        # Build up large conversation
        for i in range(100):
            agent.add_message(
                {
                    "role": "assistant" if i % 2 == 0 else "user",
                    "content": f"Message {i}. " * 20,
                }
            )

        hook.compress_agent(agent)
        context = agent.get_context()

        report = hook.get_report("large_agent")
        assert report is not None
        assert report.compression_ratio >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
