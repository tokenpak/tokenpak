"""Claude Code registry adapter for TokenPak.

Provides :class:`ClaudeCodeAdapter` for configuration, environment building,
health checking, and launching Claude Code through the TokenPak proxy.

Example::

    from tokenpak.registry.claude_code import ClaudeCodeAdapter
    adapter = ClaudeCodeAdapter()
    env = adapter.build_env()
"""
from tokenpak.registry.claude_code.adapter import ClaudeCodeAdapter
from tokenpak.registry.claude_code.config import ClaudeCodeConfig

__all__ = ["ClaudeCodeAdapter", "ClaudeCodeConfig"]
