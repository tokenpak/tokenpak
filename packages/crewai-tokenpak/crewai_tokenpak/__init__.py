"""
crewai-tokenpak

TokenPak integration for CrewAI — automatic context compression for multi-agent systems.

Quick Start:
    from crewai_tokenpak import TokenPakCrew

    crew = TokenPakCrew(
        agents=[agent1, agent2],
        tasks=[task1, task2],
        budget=8000,
    )
    result = crew.kickoff()
"""

from .context import TokenPakContext
from .handoff import TokenPakHandoff
from .crew import TokenPakCrew

__version__ = "0.1.0"
__all__ = [
    "TokenPakContext",
    "TokenPakHandoff",
    "TokenPakCrew",
]
