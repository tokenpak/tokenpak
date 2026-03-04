"""
Claude CLI platform adapter.

Detection:
  - HTTP ``User-Agent`` header starts with ``claude-cli/``, OR
  - Environment variable ``CLAUDE_CLI`` equals ``1``.

Strategy: balanced compression (0.5 ratio target), no vault integration,
preserve code blocks verbatim.
"""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseAdapter


class ClaudeCLIAdapter(BaseAdapter):
    """Adapter for requests originating from the Claude CLI tool."""

    @property
    def platform_name(self) -> str:
        return "claude_cli"

    @classmethod
    def detect(
        cls,
        request_headers: Dict[str, str],
        env: Dict[str, str],
    ) -> bool:
        headers_lower = {k.lower(): v for k, v in request_headers.items()}
        user_agent = headers_lower.get("user-agent", "")
        if user_agent.lower().startswith("claude-cli/"):
            return True
        if env.get("CLAUDE_CLI", "") == "1":
            return True
        return False

    def get_config(self) -> Dict[str, Any]:
        return {
            "compression_ratio_target": 0.5,
            "vault_aware": False,
            "preserve_code_blocks": True,
            "prefer_fast_models": False,
            "routing_hints": {
                "context_recipe": "default",
                "platform": self.platform_name,
            },
        }
