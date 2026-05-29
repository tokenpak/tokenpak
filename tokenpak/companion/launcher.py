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
# ANSI colors for the branded session label. A black background fill is
# painted across the whole label so it reads as a solid TokenPak chip
# regardless of the user's terminal background; the trailing reset clears
# it. Foreground: white "📦 Token", teal "Pak", gray "Claude Companion".
_LBL_BG_BLACK = "\033[48;2;0;0;0m"   # solid black background fill
_LBL_TEAL = "\033[38;2;0;180;170m"   # "Pak" — TokenPak teal
_LBL_WHITE = "\033[38;2;255;255;255m"  # "📦 Token"        — white
_LBL_GRAY = "\033[38;2;90;94;105m"   # "Claude Companion" — muted gray
_LBL_RESET = "\033[0m"
# Default session label shown in the top-HR chat-header. Kept in sync
# with ``hooks/session_start_name.sh`` so the post-/clear restore
# matches the launcher's startup label exactly. Real ESC bytes here —
# they pass through ``os.execvpe`` to ``--name`` as raw argv bytes.
_DEFAULT_SESSION_LABEL = (
    f"{_LBL_BG_BLACK}"
    f"{_LBL_WHITE} {_SESSION_PREFIX} Token"
    f"{_LBL_TEAL}Pak"
    f"{_LBL_GRAY} Claude Companion "
    f"{_LBL_RESET}"
)


def _prefix_session_name(args: list[str]) -> list[str]:
    """Prefix the Claude Code session name with 📦.

    Handles ``--name VALUE``, ``-n VALUE``, and ``--name=VALUE`` forms.
    If no name flag is present, injects the default branded label
    (``📦 TokenPak Claude Companion``).
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
    # No name flag found — inject the default branded label
    args.extend(["--name", _DEFAULT_SESSION_LABEL])
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
    defaults, effort level, etc. In particular, workspace-scoped users
    rely on ``permissions.additionalDirectories`` to reach configured
    workspace directories from their CWD — without it the Claude Code
    path sandbox blocks every read even with
    ``--permission-mode bypassPermissions`` (bypass skips prompts, not
    path checks).

    Load the user's ``~/.claude/settings.json`` as the base and layer the
    companion's MCP permission + pre-send hook on top. Falls back to a
    minimal dict when the user has no global settings.

    Persistent top-HR session label via ``SessionStart`` hook
    ---------------------------------------------------------
    The launcher passes ``--name "<ANSI-styled label>"`` at startup,
    painting ``[ 📦 TokenPak Claude Companion ]`` (teal brackets +
    ``Pak``, white ``📦 Token``, gray ``Claude Companion``) in the
    top-HR chat-header — foreground-only, no background fill, so the
    user's terminal background shows through. But ``--name`` is
    per-session: ``/clear`` creates a *new* session (new ``session_id``)
    and the new session inherits no name — the top-HR reverts to
    default white/gray chrome with no branding.

    Claude Code's ``SessionStart`` hook fires on session-creation
    events (``startup``, ``clear``, ``resume``, ``compact``). When a
    hook emits ``hookSpecificOutput.sessionTitle``, the TUI uses that
    string for the new session's display label. We register a tiny
    bash hook (``hooks/session_start_name.sh``) with matcher
    ``"clear"`` so the label — including its ANSI styling — is
    reasserted after every ``/clear``. The hook emits ANSI escapes as
    JSON ``\\u001b`` literals (real ESC bytes are invalid in JSON
    strings; ``\\u001b`` is the standards-compliant form, decoded back
    to ESC by the consumer's JSON parser).

    The terminal-tab title (OSC 0 sequence in :func:`main` before
    ``os.execvpe``) is unrelated — it's a one-shot pre-exec write that
    Claude Code itself rewrites on its own cadence.

    User overrides win: only injects when the user has not configured
    a ``SessionStart`` entry in their global settings.
    """
    # Prefer the bash hook (~30ms) when available; fall back to the
    # Python hook (~400ms) when only the .py is installed. When neither
    # exists, hook_cmd stays None and the UserPromptSubmit entry is
    # skipped below — avoids the 2026-04-18 regression where Claude Code
    # logged "bash: ...: No such file or directory" on every prompt
    # after the .sh file was stripped from a host.
    hooks_dir = Path(__file__).parent / "hooks"
    hook_sh = hooks_dir / "pre_send.sh"
    hook_py = hooks_dir / "pre_send.py"
    if hook_sh.is_file():
        hook_cmd = f"bash {hook_sh}"
    elif hook_py.is_file():
        hook_cmd = f"python3 {hook_py}"
    else:
        hook_cmd = None

    # SessionStart hook that re-emits the top-HR session label after
    # /clear. Skipped when the bundled script is missing on this host
    # (same defensive pattern as pre_send.sh above).
    session_name_hook = hooks_dir / "session_start_name.sh"
    session_name_cmd = (
        f"bash {session_name_hook}" if session_name_hook.is_file() else None
    )

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
    # user hasn't configured them. Applies to multi-host setups whose
    # user-level ``~/.claude/settings.json`` is bare (e.g. ``{env: {...}}``
    # only) — without this, workspace-scoped sessions can't reach their
    # vault checkout or any operator-state directory, and every session
    # trips the sandbox. Only adds dirs that actually exist on this host
    # — no phantom paths. Operators who need additional candidate dirs
    # beyond ``~/vault`` can list them (colon-separated; absolute paths
    # or names relative to ``$HOME``) in the
    # ``TOKENPAK_COMPANION_EXTRA_DIRS`` environment variable.
    add_dirs = permissions.setdefault("additionalDirectories", [])
    candidates: list[Path] = [Path.home() / "vault"]
    extra = os.environ.get("TOKENPAK_COMPANION_EXTRA_DIRS", "")
    for entry in (s.strip() for s in extra.split(":") if s.strip()):
        candidates.append(Path(entry) if entry.startswith("/") else Path.home() / entry)
    for candidate in candidates:
        if candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in add_dirs:
                add_dirs.append(candidate_str)

    # Install pre-send hook — companion-owned for this launch context.
    # Replaces any existing UserPromptSubmit entry (companion hooks are
    # authoritative here; user-level hooks would conflict with budget
    # gating + journal write-through).
    if config.hooks_enabled and hook_cmd is not None:
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

    # Install SessionStart hook — restores the branded top-HR label
    # after /clear. Only injected when the user has not configured
    # their own SessionStart hook (their override wins). Unlike the
    # UserPromptSubmit hook above, this is purely cosmetic and never
    # competes with user logic on the same matcher.
    if config.hooks_enabled and session_name_cmd is not None:
        hooks = settings.setdefault("hooks", {})
        if "SessionStart" not in hooks:
            hooks["SessionStart"] = [
                {
                    "matcher": "clear",
                    "hooks": [
                        {
                            "type": "command",
                            "command": session_name_cmd,
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
