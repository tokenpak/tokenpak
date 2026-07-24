"""Configuration schema for the Claude Code registry adapter."""

from dataclasses import dataclass


@dataclass
class ClaudeCodeConfig:
    """Configuration for the Claude Code pass-through adapter.

    Attributes:
        proxy_host: Host where the TokenPak proxy is listening.
        proxy_port: Port where the TokenPak proxy is listening.
        inject_budget: Max characters to inject from vault context per request.
        min_query_tokens: Minimum token count below which vault injection is skipped.
        enable_tool_search: Whether to enable MCP tool search (ENABLE_TOOL_SEARCH).
    """

    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8766
    inject_budget: int = 4096
    min_query_tokens: int = 10
    enable_tool_search: bool = True

    @property
    def proxy_url(self) -> str:
        """Base URL of the TokenPak proxy."""
        return f"http://{self.proxy_host}:{self.proxy_port}"
