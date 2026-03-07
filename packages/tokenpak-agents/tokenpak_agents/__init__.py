"""
tokenpak-agents

TokenPak integrations for multi-agent frameworks.

Quick Start:
    # CrewAI
    from tokenpak_agents.crewai import TokenPakCrew
    crew = TokenPakCrew(agents=[...], tasks=[...], context_budget=8000)
    
    # AutoGen
    from tokenpak_agents.autogen import TokenPakAssistant
    assistant = TokenPakAssistant(name="assistant", context_budget=4000)
    
    # Semantic Kernel
    from tokenpak_agents.semantic_kernel import TokenPakMemory
    memory = TokenPakMemory(budget=4000)
"""

from . import crewai
from . import autogen
from . import semantic_kernel

__version__ = "0.1.0"
__all__ = ["crewai", "autogen", "semantic_kernel"]
