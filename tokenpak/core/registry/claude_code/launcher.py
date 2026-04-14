"""CLI launcher logic for the Claude Code adapter."""
import os
import sys
from typing import List, Optional

from tokenpak.core.registry.claude_code.config import ClaudeCodeConfig
from tokenpak.core.registry.claude_code.health import check_proxy_health


def build_launch_env(config: ClaudeCodeConfig) -> dict:
    """Merge proxy environment variables into a copy of the current environment.

    Args:
        config: Adapter configuration.

    Returns:
        Full environment dict ready to pass to exec / subprocess.
    """
    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = config.proxy_url
    if config.enable_tool_search:
        env["ENABLE_TOOL_SEARCH"] = "true"
    if config.inject_budget:
        env["TOKENPAK_CC_INJECT_MAX_CHARS"] = str(config.inject_budget)
    return env


def launch(
    config: Optional[ClaudeCodeConfig] = None,
    args: Optional[List[str]] = None,
) -> None:
    """Health-check the proxy then exec Claude Code with the correct environment.

    Prints a warning to stderr if the proxy is not reachable (does not abort
    launch — Claude Code will show its own auth error, which is the observable
    signal).  Uses ``os.execvpe`` so the Claude Code process replaces the
    current one; falls back to ``subprocess.run`` on platforms where exec
    is unavailable.

    Args:
        config: Adapter configuration.  Defaults to :class:`ClaudeCodeConfig`
                all-defaults.
        args: Extra arguments forwarded to the ``claude`` binary.
    """
    if config is None:
        config = ClaudeCodeConfig()

    healthy, status = check_proxy_health(config)
    if not healthy:
        print(
            f"tokenpak: proxy at {config.proxy_url} is not responding ({status}). "
            "Run 'tokenpak serve' to start it.",
            file=sys.stderr,
        )

    env = build_launch_env(config)
    cmd_args = ["claude"] + (args or [])

    try:
        os.execvpe("claude", cmd_args, env)
    except OSError:
        import subprocess  # noqa: PLC0415

        subprocess.run(cmd_args, env=env, check=False)
