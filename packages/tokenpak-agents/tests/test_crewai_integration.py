"""Tests for CrewAI TokenPak integration."""

import pytest
from tokenpak_agents.crewai import TokenPakContext, TokenPakHandoff, TokenPakCrew


class TestTokenPakContext:
    
    def test_context_creation(self):
        """Test context manager creation."""
        ctx = TokenPakContext(budget=4000, compaction_mode="balanced")
        assert ctx.budget == 4000
        assert ctx.compaction_mode == "balanced"
    
    def test_process_agent_context(self):
        """Test agent context processing."""
        ctx = TokenPakContext(budget=4000)
        agent_data = {
            "id": "researcher",
            "role": "Research Agent",
            "goal": "Find information",
            "context": "Initial context",
        }
        result = ctx.process_agent_context(agent_data)
        assert result["agent_id"] == "researcher"
        assert result["role"] == "Research Agent"
        assert result["compressed"] == True
    
    def test_cache_and_retrieve(self):
        """Test caching task results."""
        ctx = TokenPakContext()
        ctx.cache_result("task_1", {"output": "Research data"})
        
        cached = ctx.get_context_for_task("task_1")
        assert cached["output"] == "Research data"


class TestTokenPakHandoff:
    
    def test_prepare_output(self):
        """Test task output preparation."""
        handoff = TokenPakHandoff()
        result = handoff.prepare_output("Research findings")
        
        assert result["type"] == "task_output"
        assert result["content"] == "Research findings"
        assert result["format"] == "tokenpak"
    
    def test_prepare_input(self):
        """Test handoff input preparation."""
        handoff = TokenPakHandoff()
        handoff_data = {"content": "Previous task output"}
        
        input_str = handoff.prepare_input(handoff_data)
        assert "Previous task output" in input_str
        assert "Context from previous task" in input_str


class TestTokenPakCrew:
    
    def test_crew_creation(self):
        """Test crew initialization."""
        crew = TokenPakCrew(
            agents=[],
            tasks=[],
            context_budget=8000,
        )
        assert crew.context_budget == 8000
        assert len(crew.agents) == 0
    
    def test_kickoff(self):
        """Test crew execution."""
        crew = TokenPakCrew(
            agents=[{"name": "agent1"}],
            tasks=[{"id": "task1"}],
            context_budget=8000,
        )
        result = crew.kickoff()
        
        assert result["status"] == "success"
        assert "outputs" in result
        assert "context_used" in result
