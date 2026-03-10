"""Tests for tool schema compression."""

import pytest
from autogen_tokenpak import TokenPakConversationHook, AgentContextConfig


class MockAgentWithTools:
    """Mock agent with complex tool schemas."""

    def __init__(self, name: str) -> None:
        """Initialize mock agent."""
        self.name = name

    def get_context(self) -> dict:
        """Return context with complex tools."""
        return {
            "agent_name": self.name,
            "system_prompt": "Assistant system prompt. " * 10,
            "tools": [
                {
                    "name": "web_search",
                    "description": (
                        "Search the web for current information. "
                        "This tool helps find the latest news, "
                        "articles, and data. " * 3
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Search query. Can include multiple keywords. "
                                    "Be specific for better results. " * 2
                                ),
                            },
                            "max_results": {
                                "type": "integer",
                                "description": (
                                    "Maximum number of results to return. "
                                    "Default is 10. " * 2
                                ),
                            },
                            "language": {
                                "type": "string",
                                "description": (
                                    "Language code for search results "
                                    "(en, fr, de, etc.). " * 2
                                ),
                            },
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "code_execution",
                    "description": (
                        "Execute Python code safely. "
                        "Code runs in an isolated sandbox. "
                        "Can install packages. " * 3
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": (
                                    "Python code to execute. "
                                    "Must be valid Python syntax. " * 2
                                ),
                            },
                            "install_packages": {
                                "type": "array",
                                "description": (
                                    "Packages to install via pip. "
                                    "Format: package==version. " * 2
                                ),
                            },
                        },
                        "required": ["code"],
                    },
                },
            ],
        }


class TestToolCompression:
    """Tests for tool schema compression."""

    def test_tool_compression_applied(self) -> None:
        """Test that tools are compressed."""
        hook = TokenPakConversationHook()
        agent = MockAgentWithTools("tool_agent")

        config = AgentContextConfig(compress_tools=True)
        hook.compress_agent(agent, config)

        context = agent.get_context()
        report = hook.get_report("tool_agent")

        assert report is not None
        assert report.tools_compressed > 0

    def test_tool_compression_disabled(self) -> None:
        """Test disabling tool compression."""
        hook = TokenPakConversationHook()
        agent = MockAgentWithTools("tool_agent")

        config = AgentContextConfig(compress_tools=False)
        hook.compress_agent(agent, config)

        context = agent.get_context()
        report = hook.get_report("tool_agent")

        assert report is not None
        assert report.tools_compressed == 0

    def test_tool_descriptions_normalized(self) -> None:
        """Test that tool descriptions are normalized."""
        hook = TokenPakConversationHook()
        agent = MockAgentWithTools("tool_agent")

        hook.compress_agent(agent, AgentContextConfig(compress_tools=True))
        context = agent.get_context()

        for tool in context["tools"]:
            desc = tool.get("description", "")
            # Should not have excessive whitespace
            assert "  " not in desc
            assert "\n" not in desc

    def test_parameter_descriptions_normalized(self) -> None:
        """Test that parameter descriptions are normalized."""
        hook = TokenPakConversationHook()
        agent = MockAgentWithTools("tool_agent")

        hook.compress_agent(agent, AgentContextConfig(compress_tools=True))
        context = agent.get_context()

        for tool in context["tools"]:
            if "parameters" in tool and "properties" in tool["parameters"]:
                for prop in tool["parameters"]["properties"].values():
                    desc = prop.get("description", "")
                    # Should not have excessive whitespace
                    assert "  " not in desc
                    assert "\n" not in desc

    def test_empty_tools_list(self) -> None:
        """Test handling of empty tools list."""
        hook = TokenPakConversationHook()

        class AgentNoTools:
            name = "no_tools"

            def get_context(self) -> dict:
                return {
                    "system_prompt": "Agent without tools",
                    "tools": [],
                }

        agent = AgentNoTools()  # type: ignore
        hook.compress_agent(agent, AgentContextConfig(compress_tools=True))
        context = agent.get_context()

        assert context["tools"] == []

    def test_malformed_tools_handling(self) -> None:
        """Test handling of malformed tool definitions."""
        hook = TokenPakConversationHook()

        class AgentMalformedTools:
            name = "malformed"

            def get_context(self) -> dict:
                return {
                    "system_prompt": "Agent with malformed tools",
                    "tools": [
                        {"name": "tool1"},  # Missing description
                        "not_a_dict",  # Invalid tool
                        {"name": "tool2", "description": "Tool 2. " * 10},
                    ],
                }

        agent = AgentMalformedTools()  # type: ignore
        hook.compress_agent(agent, AgentContextConfig(compress_tools=True))
        context = agent.get_context()

        # Should handle malformed gracefully
        assert len(context["tools"]) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
