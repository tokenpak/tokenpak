"""Public API for the CrewAI TokenPak adapter."""

from .context import AgentContextConfig, CompressionResult, TokenPakContext
from .crew import CompletionHook, TokenPakCompressionReport, TokenPakCrew, TokenPakCrewAIHook
from .handoff import TokenPakHandoff

__version__ = "0.1.0"
__all__ = [
    "AgentContextConfig",
    "CompletionHook",
    "CompressionResult",
    "TokenPakContext",
    "TokenPakCompressionReport",
    "TokenPakCrewAIHook",
    "TokenPakHandoff",
    "TokenPakCrew",
]
