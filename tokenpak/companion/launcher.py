# SPDX-License-Identifier: Apache-2.0
"""Launcher for ``tokenpak claude`` — one command to start Claude Code with
the companion active.

What it does:
    1. Loads companion config from env vars
    2. Ensures the tokenpak proxy is running (if configured)
    3. Generates temp files: MCP config, settings overlay, system prompt
    4. Execs into ``claude`` with the right flags

What the user sees:
    $ tokenpak claude
    tokenpak: companion ready (balanced, budget $5.00/day)
    [Claude Code TUI starts normally]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .config import CompanionConfig


# System prompt fragment injected via --append-system-prompt-file
_SYSTEM_PROMPT = """\
## tokenpak companion

A tokenpak companion is active in this session. You have these MCP tools:

- **estimate_tokens** — Estimate token count for text or a file. Call before including large content.
- **check_budget** — Query remaining cost budget for this session and today.
- **load_capsule** — Load a memory capsule from a prior session (omit session_id to list available).
- **prune_context** — Compress verbose tool output to reduce token count.
- **journal_read** — Read session journal entries (omit session_id to list sessions).
- **journal_write** — Save an important decision, milestone, or note for future sessions.
- **session_info** — Get companion status and configuration.

The companion automatically estimates cost and journals each prompt via hooks.
You only need to call tools explicitly when optimizing context or managing budget.
"""


def main(args: list[str] | None = None) -> int:
    """Entry point for ``tokenpak claude``."""
    args = args if args is not None else sys.argv[1:]

    config = CompanionConfig.from_env()
    config.profile_overrides()

    # Ensure journal dir exists
    config.journal_dir.mkdir(parents=True, exist_ok=True)

    # Generate temp files that Claude Code will consume
    tmpdir = tempfile.mkdtemp(prefix="tokenpak-companion-")

    mcp_config_path = _write_mcp_config(tmpdir)
    settings_path = _write_settings(tmpdir, config)
    prompt_path = _write_system_prompt(tmpdir)

    # Print startup banner
    banner_parts = ["tokenpak: companion ready"]
    banner_parts.append(f"({config.profile}")
    if config.budget_daily_usd > 0:
        banner_parts.append(f"budget ${config.budget_daily_usd:.2f}/day)")
    else:
        banner_parts.append("no budget cap)")
    print("  ".join(banner_parts), file=sys.stderr)

    # Build claude command
    claude_args = ["claude"]

    if config.mcp_enabled:
        claude_args.extend(["--mcp-config", mcp_config_path])

    claude_args.extend(["--append-system-prompt-file", prompt_path])
    claude_args.extend(["--settings", settings_path])

    # Pass through any user-provided args
    claude_args.extend(args)

    # Set env vars for the proxy
    env = os.environ.copy()
    if config.proxy_url:
        env["ANTHROPIC_BASE_URL"] = config.proxy_url

    # Exec into claude — replaces this process
    os.execvpe("claude", claude_args, env)

    # Only reached if exec fails
    print("tokenpak: failed to launch claude", file=sys.stderr)
    return 1


def _write_mcp_config(tmpdir: str) -> str:
    """Write the MCP server configuration."""
    config = {
        "mcpServers": {
            "tokenpak-companion": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "tokenpak.companion.mcp.server"],
            }
        }
    }
    path = os.path.join(tmpdir, "mcp.json")
    with open(path, "w") as f:
        json.dump(config, f)
    return path


def _write_settings(tmpdir: str, config: CompanionConfig) -> str:
    """Write the settings overlay with hook configuration and permissions."""
    hook_cmd = f"{sys.executable} -m tokenpak.companion.hooks.pre_send"

    settings: dict = {
        "permissions": {
            "allow": [
                "mcp__tokenpak-companion__*",
            ]
        },
    }

    if config.hooks_enabled:
        settings["hooks"] = {
            "UserPromptSubmit": [
                {
                    "type": "command",
                    "command": hook_cmd,
                }
            ],
        }

    path = os.path.join(tmpdir, "settings.json")
    with open(path, "w") as f:
        json.dump(settings, f)
    return path


def _write_system_prompt(tmpdir: str) -> str:
    """Write the companion system prompt fragment."""
    path = os.path.join(tmpdir, "companion-prompt.md")
    with open(path, "w") as f:
        f.write(_SYSTEM_PROMPT)
    return path
