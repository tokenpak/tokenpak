# SPDX-License-Identifier: Apache-2.0
"""Launcher for ``tokenpak codex`` — thin bootstrap for Codex with companion.

Does setup (rate snapshot, MCP registration, hooks install, AGENTS.md,
skills) and either exec-replaces into ``codex`` (default) or exits
after install (``--install-only``).

Companion features work without the launcher if the user manually
configures MCP, hooks, and AGENTS.md — the launcher is convenience.
"""

from __future__ import annotations

import os
import sys

from ..config import CompanionConfig


def main(args: list[str] | None = None) -> int:
    """Entry point for ``tokenpak codex``."""
    args = list(args if args is not None else sys.argv[1:])

    install_only = False
    if "--install-only" in args:
        install_only = True
        args = [a for a in args if a != "--install-only"]

    config = CompanionConfig.from_env()
    config.profile_overrides()

    config.journal_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 0: Refresh model-rate snapshot for shell hooks ──
    from .rates_snapshot import refresh as refresh_rates

    rates_path = refresh_rates()
    print(f"tokenpak: rates snapshot refreshed ({rates_path})", file=sys.stderr)

    # ── Step 1: Register MCP server ──────────────────────────
    from .mcp_config import register, get_env_vars

    env_vars = get_env_vars(config)
    if register(env_vars=env_vars):
        print("tokenpak: MCP server registered", file=sys.stderr)
    else:
        print("tokenpak: MCP registration failed (continuing)", file=sys.stderr)

    # ── Step 2: Install hooks ────────────────────────────────
    if config.hooks_enabled:
        from .hooks import install_hooks, ensure_hooks_feature_enabled

        if ensure_hooks_feature_enabled():
            hooks_path = install_hooks(target="global")
            print(f"tokenpak: hooks installed ({hooks_path})", file=sys.stderr)
        else:
            print(
                "tokenpak: codex_hooks feature could not be enabled",
                file=sys.stderr,
            )

    # ── Step 3: Install AGENTS.md ────────────────────────────
    from .agents_md import install_agents_md

    agents_path = install_agents_md(target="global")
    print(f"tokenpak: AGENTS.md installed ({agents_path})", file=sys.stderr)

    # ── Step 4: Install skills ───────────────────────────────
    from .skills_installer import install_skills

    installed = install_skills()
    if installed:
        print(f"tokenpak: {len(installed)} skills installed", file=sys.stderr)

    # ── Step 5: Banner ───────────────────────────────────────
    budget_phrase = (
        f"budget ${config.budget_daily_usd:.2f}/day"
        if config.budget_daily_usd > 0
        else "no budget cap"
    )
    print(
        f"tokenpak: companion ready for codex ({config.profile}, {budget_phrase})",
        file=sys.stderr,
    )

    if install_only:
        print(
            "tokenpak: setup complete — run `tokenpak codex doctor` to verify",
            file=sys.stderr,
        )
        return 0

    # ── Step 6: Exec into codex ──────────────────────────────
    if config.budget_daily_usd > 0:
        os.environ["TOKENPAK_COMPANION_BUDGET"] = str(config.budget_daily_usd)

    env = os.environ.copy()
    if config.profile != "balanced":
        env["TOKENPAK_COMPANION_PROFILE"] = config.profile
    default_journal_dir = str(config.journal_dir.__class__.home() / ".tokenpak" / "companion")
    if str(config.journal_dir) != default_journal_dir:
        env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(config.journal_dir)

    codex_args = ["codex", *args]
    os.execvpe("codex", codex_args, env)

    print("tokenpak: failed to launch codex", file=sys.stderr)
    return 1
