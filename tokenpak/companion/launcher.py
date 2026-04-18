# SPDX-License-Identifier: Apache-2.0
"""Launcher for ``tokenpak claude`` — one command to start Claude Code with
the companion active.

What it does:
    1. Loads companion config from env vars
    2. Ensures the tokenpak proxy is running (if configured)
    3. Generates config files: MCP config, settings overlay, system prompt
    4. Execs into ``claude`` with the right flags

Config files are written to the fixed location ~/.tokenpak/companion/run/
(not tempfile) so they persist across relaunches and are inspectable.

What the user sees:
    $ tokenpak claude

      📦 TokenPak Companion
         Ready • Mode: Balanced • Budget: Unlimited
         Proxy active → http://localhost:8766

         Your API bill called. It's crying.

    [Claude Code TUI starts normally]
"""

from __future__ import annotations

import json
import os
import random
import sys
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

    # Ensure journal dir and fixed run dir exist
    config.journal_dir.mkdir(parents=True, exist_ok=True)
    config.run_dir.mkdir(parents=True, exist_ok=True)

    # Generate config files at fixed location (AC5: ~/.tokenpak/companion/run/)
    mcp_config_path = _write_mcp_config(config)
    settings_path = _write_settings(config)
    prompt_path = _write_system_prompt(config)

    _TEAL = "\033[38;2;0;180;170m"
    _DIM = "\033[2m"
    _RESET = "\033[0m"

    mode = config.profile.capitalize()
    budget = f"${config.budget_daily_usd:.2f}/day" if config.budget_daily_usd > 0 else "Unlimited"

    # Route through tokenpak proxy for compression/caching/dedup.
    # Auto-detect if proxy is running when no explicit proxy_url is set.
    env = os.environ.copy()

    # Bare mode: strip Claude Code native context layers so an external
    # gateway (e.g. OpenClaw) can inject its own tools/history/memory.
    if config.bare:
        env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] = "1"

    proxy_url = config.proxy_url
    if not proxy_url:
        default_proxy = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
        try:
            import httpx
            resp = httpx.get(f"{default_proxy}/health", timeout=1.0)
            if resp.status_code == 200:
                proxy_url = default_proxy
        except Exception:
            pass
    if proxy_url:
        env["ANTHROPIC_BASE_URL"] = proxy_url

    # Print styled startup banner
    from tokenpak.cli.commands.status import MEME_LINES
    meme = random.choice(MEME_LINES)

    print(file=sys.stderr)
    bare_tag = " \u2022 Bare: ON" if config.bare else ""
    print(f"  \U0001f4e6 Token{_TEAL}Pak{_RESET} Companion", file=sys.stderr)
    print(f"     {_DIM}Ready \u2022 Mode: {mode} \u2022 Budget: {budget}{bare_tag}{_RESET}", file=sys.stderr)
    if proxy_url:
        print(f"     {_DIM}Proxy active \u2192 {proxy_url}{_RESET}", file=sys.stderr)
    print(file=sys.stderr)
    print(f"     {_DIM}{meme}{_RESET}", file=sys.stderr)
    print(file=sys.stderr)

    # Prefix session name with 📦 so tokenpak sessions are visually distinct
    # in terminal tabs. If the user provided --name/-n, prefix their value;
    # otherwise inject a default name.
    args = _prefix_session_name(args)

    # Build claude command
    claude_args = ["claude"]

    if config.mcp_enabled:
        claude_args.extend(["--mcp-config", mcp_config_path])

    if config.bare:
        # Bare mode: skip system prompt, settings/hooks overlay, and bypass
        # permissions — the external gateway (OpenClaw) owns those layers.
        claude_args.append("--dangerously-skip-permissions")
    else:
        claude_args.extend(["--append-system-prompt-file", prompt_path])
        claude_args.extend(["--settings", settings_path])

    # Pass through any user-provided args
    claude_args.extend(args)

    # Set terminal tab title immediately so the 📦 is visible even before
    # Claude Code finishes initialising and sets its own title.
    sys.stderr.write("\033]0;📦 tokenpak claude\007")
    sys.stderr.flush()

    # Exec into claude — replaces this process
    os.execvpe("claude", claude_args, env)

    # Only reached if exec fails
    print("tokenpak: failed to launch claude", file=sys.stderr)
    return 1


_SESSION_PREFIX = "\U0001f4e6"  # 📦


def _prefix_session_name(args: list[str]) -> list[str]:
    """Prefix the Claude Code session name with 📦.

    Handles ``--name VALUE``, ``-n VALUE``, and ``--name=VALUE`` forms.
    If no name flag is present, injects ``--name "📦 tokenpak"``.
    Returns a new list (never mutates the input).
    """
    args = list(args)  # shallow copy
    for i, arg in enumerate(args):
        if arg in ("--name", "-n") and i + 1 < len(args):
            args[i + 1] = f"{_SESSION_PREFIX} {args[i + 1]}"
            return args
        if arg.startswith("--name="):
            _, val = arg.split("=", 1)
            args[i] = f"--name={_SESSION_PREFIX} {val}"
            return args
    # No name flag found — inject a default
    args.extend(["--name", f"{_SESSION_PREFIX} tokenpak claude"])
    return args


def _write_mcp_config(config: CompanionConfig) -> str:
    """Write the MCP server configuration to fixed run_dir."""
    mcp_data = {
        "mcpServers": {
            "tokenpak-companion": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "tokenpak.companion.mcp.server"],
            }
        }
    }
    path = config.run_dir / "mcp.json"
    path.write_text(json.dumps(mcp_data, indent=2))
    return str(path)


def _write_settings(config: CompanionConfig) -> str:
    """Write the settings overlay with hook configuration and permissions.

    Claude Code's ``--settings <file>`` argument replaces the user-level
    settings at ``~/.claude/settings.json`` wholesale — it does NOT merge.
    So a minimal overlay file strips everything the user carefully
    configured: allowed directories, custom permissions, attribution
    defaults, effort level, etc. In particular, workspace agents (Suki,
    Cali, Trix) rely on ``permissions.additionalDirectories`` to reach
    ``~/vault`` and ``~/.openclaw`` from their workspace CWD — without it
    the Claude Code path sandbox blocks every read even with
    ``--permission-mode bypassPermissions`` (bypass skips prompts, not
    path checks).

    Load the user's ``~/.claude/settings.json`` as the base and layer the
    companion's MCP permission + pre-send hook on top. Falls back to a
    minimal dict when the user has no global settings.
    """
    # Use the bash hook for speed (~30ms vs ~400ms for Python hook).
    # The bash hook does file-size token estimation, budget gating, and
    # stderr output without spawning a Python interpreter on every prompt.
    hook_script = Path(__file__).parent / "hooks" / "pre_send.sh"
    hook_cmd = f"bash {hook_script}"

    settings: dict = {}
    user_settings_path = Path.home() / ".claude" / "settings.json"
    if user_settings_path.is_file():
        try:
            settings = json.loads(user_settings_path.read_text())
        except Exception:
            settings = {}

    # Ensure permissions.allow includes the companion's MCP glob
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    companion_glob = "mcp__tokenpak-companion__*"
    if companion_glob not in allow:
        allow.append(companion_glob)

    # Auto-add common workspace dirs to additionalDirectories when the
    # user hasn't configured them. Applies to fleet hosts whose user-
    # level ``~/.claude/settings.json`` is bare (e.g. ``{env: {...}}``
    # only) — without this, workspace agents can't reach their vault
    # checkout or OpenClaw state dir and every cycle trips the sandbox.
    # Only adds dirs that actually exist on this host — no phantom paths.
    add_dirs = permissions.setdefault("additionalDirectories", [])
    for candidate in (
        Path.home() / "vault",
        Path.home() / ".openclaw",
        Path.home() / ".openclaw-governor",
    ):
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in add_dirs:
                add_dirs.append(candidate_str)

    # Install pre-send hook — companion-owned for this launch context.
    # Replaces any existing UserPromptSubmit entry (companion hooks are
    # authoritative here; user-level hooks would conflict with budget
    # gating + journal write-through).
    if config.hooks_enabled:
        hooks = settings.setdefault("hooks", {})
        hooks["UserPromptSubmit"] = [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd,
                    }
                ],
            }
        ]

    path = config.run_dir / "settings.json"
    path.write_text(json.dumps(settings, indent=2))
    return str(path)


def _write_system_prompt(config: CompanionConfig) -> str:
    """Write the companion system prompt fragment."""
    path = config.run_dir / "companion-prompt.md"
    path.write_text(_SYSTEM_PROMPT)
    return str(path)
