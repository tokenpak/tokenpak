"""ClaudeCodeAdapter — configuration and launch wrapper for Claude Code pass-through."""
import platform
from typing import Dict, Optional

from tokenpak.registry.claude_code.config import ClaudeCodeConfig


class ClaudeCodeAdapter:
    """Registry adapter encapsulating Claude Code pass-through integration.

    The actual byte-level pass-through logic lives in the proxy (proxy.py).
    This adapter provides configuration, environment building, health checking,
    and platform identification for telemetry.
    """

    ADAPTER_NAME = "claude-code"
    PLATFORM_TAG = "claude-code"

    def __init__(self, config: Optional[ClaudeCodeConfig] = None) -> None:
        self.config = config or ClaudeCodeConfig()
        self._platform_info: Dict[str, str] = {
            "os": platform.system(),
            "python": platform.python_version(),
            "adapter": self.ADAPTER_NAME,
        }

    def build_env(self) -> Dict[str, str]:
        """Build the environment variables required to point Claude Code at the proxy."""
        env: Dict[str, str] = {
            "ANTHROPIC_BASE_URL": self.config.proxy_url,
        }
        if self.config.enable_tool_search:
            env["ENABLE_TOOL_SEARCH"] = "true"
        if self.config.inject_budget:
            env["TOKENPAK_CC_INJECT_MAX_CHARS"] = str(self.config.inject_budget)
        return env

    @property
    def platform_info(self) -> Dict[str, str]:
        """Read-only platform identification dict for telemetry."""
        return dict(self._platform_info)
