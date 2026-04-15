# SPDX-License-Identifier: Apache-2.0
"""Launcher for ``tokenpak codex`` — thin bootstrap for Codex with companion.

Unlike the Claude Code launcher, this does NOT wrap or exec-replace Codex.
It performs setup (MCP registration, hooks install, AGENTS.md install) and
then launches Codex with any user-provided args.

The launcher is optional convenience — all companion features work without
it if the user manually configures MCP, hooks, and AGENTS.md.

What the user sees::

    $ tokenpak codex
    tokenpak: companion ready for codex (balanced, no budget cap)
    tokenpak: MCP server registered
    tokenpak: hooks installed
    [Codex starts normally]

    $ tokenpak codex "Fix the login bug"
    tokenpak: companion ready for codex (balanced, budget $5.00/day)
    [Codex starts with prompt]
"""

from __future__ import annotations

import os
import sys

from ..config import CompanionConfig


def main(args: list[str] | None = None) -> int:
    """Entry point for ``tokenpak codex``."""
    args = args if args is not None else sys.argv[1:]

    config = CompanionConfig.from_env()
    config.profile_overrides()

    # Ensure storage dirs exist
    config.journal_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Register MCP server ──────────────────────────
    from .mcp_config import register, get_env_vars

    env_vars = get_env_vars(config)
    mcp_ok = register(env_vars=env_vars)
    if mcp_ok:
        print("tokenpak: MCP server registered", file=sys.stderr)
    else:
        print("tokenpak: MCP registration failed (continuing without)", file=sys.stderr)

    # ── Step 2: Install hooks ────────────────────────────────
    if config.hooks_enabled:
        from .hooks import install_hooks, ensure_hooks_feature_enabled

        feature_ok = ensure_hooks_feature_enabled()
        if feature_ok:
            hooks_path = install_hooks(target="global")
            print(f"tokenpak: hooks installed ({hooks_path})", file=sys.stderr)
        else:
            print("tokenpak: hooks feature not enabled in config.toml", file=sys.stderr)

    # ── Step 3: Install AGENTS.md ────────────────────────────
    from .agents_md import install_agents_md

    agents_path = install_agents_md(target="global")
    print(f"tokenpak: AGENTS.md installed ({agents_path})", file=sys.stderr)

    # ── Step 4: Install skills ───────────────────────────────
    from .skills_installer import install_skills

    installed = install_skills()
    if installed:
        print(f"tokenpak: {len(installed)} skills installed", file=sys.stderr)

    # ── Step 5: Print banner ─────────────────────────────────
    banner_parts = ["tokenpak: companion ready for codex"]
    banner_parts.append(f"({config.profile}")
    if config.budget_daily_usd > 0:
        banner_parts.append(f"budget ${config.budget_daily_usd:.2f}/day)")
    else:
        banner_parts.append("no budget cap)")
    print("  ".join(banner_parts), file=sys.stderr)

    # ── Step 6: Launch Codex ─────────────────────────────────
    codex_args = ["codex"]

    # Forward budget as model config if set
    if config.budget_daily_usd > 0:
        os.environ["TOKENPAK_COMPANION_BUDGET"] = str(config.budget_daily_usd)

    # Pass through user-provided args
    codex_args.extend(args)

    # Set env vars for companion hooks/MCP
    env = os.environ.copy()
    if config.profile != "balanced":
        env["TOKENPAK_COMPANION_PROFILE"] = config.profile
    if str(config.journal_dir) != str(config.journal_dir.__class__.home() / ".tokenpak" / "companion"):
        env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(config.journal_dir)

    # Exec into codex — replaces this process
    os.execvpe("codex", codex_args, env)

    # Only reached if exec fails
    print("tokenpak: failed to launch codex", file=sys.stderr)
    return 1
