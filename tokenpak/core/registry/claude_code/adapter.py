"""ClaudeCodeAdapter — byte-level pass-through adapter for Claude Code."""

import platform
from typing import Dict, Optional

from tokenpak.core.registry.claude_code.config import ClaudeCodeConfig
from tokenpak.proxy.request import ROUTE_CLAUDE_CODE, HTTPProxy, ProxyRequest, ProxyResponse


class ClaudeCodeAdapter:
    """Registry adapter encapsulating Claude Code pass-through integration.

    Wraps the byte-level pass-through logic from proxy.py with configuration,
    environment-variable building, and platform identification for telemetry.

    The adapter registers itself under the name ``"claude-code"`` in the
    TokenPak extensions registry when :func:`register` is called.
    """

    ADAPTER_NAME = "claude-code"
    PLATFORM_TAG = "claude-code"

    def __init__(self, config: Optional[ClaudeCodeConfig] = None) -> None:
        """Initialise the adapter.

        Args:
            config: Optional configuration.  Defaults to :class:`ClaudeCodeConfig`
                    with all-default values.
        """
        self.config = config or ClaudeCodeConfig()
        self._proxy = HTTPProxy()
        self._platform_info: Dict[str, str] = {
            "os": platform.system(),
            "python": platform.python_version(),
            "adapter": self.ADAPTER_NAME,
        }

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------

    def handle_request(
        self,
        request: ProxyRequest,
        model: Optional[str] = None,
    ) -> ProxyResponse:
        """Forward *request* through the proxy using the Claude Code route.

        Applies the Claude Code header allowlist so only the seven
        permitted headers reach the upstream API.

        Args:
            request: Incoming proxy request.
            model: Model name forwarded to session-ID resolution.

        Returns:
            Upstream :class:`ProxyResponse`.
        """
        return self._proxy.handle_request(request, route=ROUTE_CLAUDE_CODE, model=model)

    # ------------------------------------------------------------------
    # Environment / launch helpers
    # ------------------------------------------------------------------

    def build_env(self) -> Dict[str, str]:
        """Build the environment variables required to point Claude Code at the proxy.

        Returns:
            Dict of environment variable name → value.  Always includes
            ``ANTHROPIC_BASE_URL``; conditionally includes
            ``ENABLE_TOOL_SEARCH`` and ``TOKENPAK_CC_INJECT_MAX_CHARS``.
        """
        env: Dict[str, str] = {
            "ANTHROPIC_BASE_URL": self.config.proxy_url,
        }
        if self.config.enable_tool_search:
            env["ENABLE_TOOL_SEARCH"] = "true"
        if self.config.inject_budget:
            env["TOKENPAK_CC_INJECT_MAX_CHARS"] = str(self.config.inject_budget)
        return env

    # ------------------------------------------------------------------
    # Telemetry / platform identification
    # ------------------------------------------------------------------

    @property
    def platform_info(self) -> Dict[str, str]:
        """Read-only platform identification dict for telemetry."""
        return dict(self._platform_info)
