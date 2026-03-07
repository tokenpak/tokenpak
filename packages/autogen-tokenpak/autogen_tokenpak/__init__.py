"""
autogen-tokenpak

TokenPak integration for AutoGen — automatic context compression for multi-agent conversations.

Quick Start:
    from autogen_tokenpak import TokenPakAssistant, TokenPakGroupChat

    assistant = TokenPakAssistant(name="agent", budget=4000)
    group = TokenPakGroupChat(agents=[assistant], budget=8000)
"""

from .assistant import TokenPakAssistant
from .groupchat import TokenPakGroupChat
from .message import TokenPakMessage

__version__ = "0.1.0"
__all__ = [
    "TokenPakAssistant",
    "TokenPakGroupChat",
    "TokenPakMessage",
]
