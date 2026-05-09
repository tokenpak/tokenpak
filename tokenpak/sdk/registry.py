"""tokenpak.sdk.registry — platform detection and adapter registry."""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tokenpak.sdk.base import TokenPakAdapter


def detect_platform() -> str:
    """Detect the current Claude Code consumption platform.

    Returns one of: 'openclaw', 'claude_cli', 'ide', 'cron', 'generic'.
    """
    if os.environ.get("OPENCLAW_GATEWAY_URL") or os.environ.get("OPENCLAW_HOST"):
        return "openclaw"
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "cli":
        return "claude_cli"
    if os.environ.get("TERM_PROGRAM") in ("vscode", "cursor", "windsurf"):
        return "ide"
    if not sys.stdin.isatty():
        return "cron"
    return "generic"


def get_adapter(platform: Optional[str] = None) -> "TokenPakAdapter":
    """Return the appropriate adapter for the detected platform."""
    p = platform or detect_platform()
    if p == "openclaw":
        from tokenpak.sdk.openclaw import OpenClawAdapter
        return OpenClawAdapter()
    if p == "claude_cli":
        from tokenpak.sdk.claude_cli import ClaudeCLIAdapter
        return ClaudeCLIAdapter()
    from tokenpak.sdk.generic import GenericAdapter
    return GenericAdapter()


def setup_platform(platform: Optional[str] = None, **kwargs) -> dict:
    """Run platform-specific setup/configuration.

    Detects the platform (or uses the one provided) and configures
    tokenpak integration. Returns a result dict describing what was done.

    Supported platforms:
      - ``openclaw``: adds/updates tokenpak-* providers in openclaw.json
      - ``claude_cli``: configures ANTHROPIC_BASE_URL in Claude Code settings

    Args:
        platform: Platform to configure (auto-detected if None).
        **kwargs: Platform-specific options (e.g. proxy_url).
    """
    p = platform or detect_platform()

    if p == "openclaw":
        from tokenpak.sdk.openclaw import detect_openclaw, setup_openclaw
        if not detect_openclaw():
            return {"error": "No OpenClaw install detected on this host"}
        return setup_openclaw(**kwargs)

    if p in ("claude_cli", "generic"):
        # Claude Code setup is in cli/commands/install.py
        from tokenpak.cli.commands.install import run_install_cmd
        # Create a minimal args namespace
        class _Args:
            mode = kwargs.get("mode")
            proxy_url = kwargs.get("proxy_url")
            systemd = kwargs.get("systemd", False)
        run_install_cmd(_Args())
        return {"platform": "claude_cli", "status": "configured"}

    return {"error": f"No setup available for platform: {p}"}


__all__ = ["detect_platform", "get_adapter", "setup_platform"]
