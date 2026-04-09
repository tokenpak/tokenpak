"""TokenPak integration for Microsoft AutoGen.

This package provides automatic context compression for AutoGen conversations,
reducing token usage in multi-agent systems while preserving conversation quality.

Example:
    >>> from tokenpak.adapters.autogen import TokenPakConversationHook
    >>> hook = TokenPakConversationHook()
    >>> # Patch an AutoGen agent (requires pyautogen installed):
    >>> # hook.compress_agent(assistant)
"""

from .context import (
    TokenPakConversationHook,
    TokenPakCompressionReport,
    AgentContextConfig,
)
from .message import TokenPakMessage, compress_messages
from .assistant import TokenPakAssistant
from .groupchat import TokenPakGroupChat

__version__ = "0.1.0"
__all__ = [
    "TokenPakConversationHook",
    "TokenPakCompressionReport",
    "AgentContextConfig",
    "TokenPakMessage",
    "compress_messages",
    "TokenPakAssistant",
    "TokenPakGroupChat",
]
