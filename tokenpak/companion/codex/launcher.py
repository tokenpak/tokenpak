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

_TEAL = "\033[38;2;0;180;170m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CLEAR_LINE = "\033[2K"
_TOKENPAK_CHATGPT_BASE_URL = "http://127.0.0.1:8766/v1"

_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"
_BYPASS_ENV_VAR = "TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX"
_TRUTHY = {"1", "true", "yes"}


def _bypass_env_enabled(env: dict[str, str] | None = None) -> bool:
    """Return True if the bypass env var is set to a truthy value (case-insensitive)."""
    src = env if env is not None else os.environ
    raw = src.get(_BYPASS_ENV_VAR, "")
    return raw.strip().lower() in _TRUTHY


def _fleet_state_enabled() -> bool:
    """True when TokenPak launcher fleet mode is enabled. Never raises.

    Fleet mode is the runtime unattended-bypass knob stored in
    TokenPak-owned state (~/.config/tokenpak/permissions.toml, set via
    `tokenpak permissions set fleet`). It is launcher-scoped only and
    never persists into ~/.codex/config.toml — the persistent trust level
    (tier) lives there and is managed by `tokenpak permissions`.
    """
    try:
        from tokenpak.cli.commands.permissions import fleet_mode_enabled

        return fleet_mode_enabled()
    except Exception:
        return False


def _maybe_inject_bypass_flag(
    args: list[str], env: dict[str, str] | None = None, fleet: bool = False
) -> list[str]:
    """Return a new arg list with the Codex bypass flag injected when opted in.

    Two opt-in surfaces, both launcher-scoped:

    - ``fleet=True`` — TokenPak launcher fleet mode (canonical path; the
      caller reads it from TokenPak-owned state via
      :func:`_fleet_state_enabled`).
    - the env var ``TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX``
      (accepts ``1`` / ``true`` / ``yes``) — the Codex-side back-compat
      alias of fleet mode, kept for automation scripts that predate the
      permission-tier system. Same effect, same banner.

    The flag is a no-op if the user already passed it on the command line
    (no duplication). Never mutates the input list.
    """
    if not (fleet or _bypass_env_enabled(env)):
        return list(args)
    if _BYPASS_FLAG in args:
        return list(args)
    return [_BYPASS_FLAG, *args]


def _fleet_banner(env: dict[str, str] | None = None, fleet: bool = False) -> str | None:
    """Mandatory stderr banner text for fleet-mode launches (None when off).

    Canonical user-visible guardrail — do not remove or soften it.
    """
    if fleet or _bypass_env_enabled(env):
        return f"tokenpak: fleet mode — bypass flags injected ({_BYPASS_FLAG})"
    return None


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
    from .mcp_config import get_env_vars, register

    env_vars = get_env_vars(config)
    if register(env_vars=env_vars):
        print("tokenpak: MCP server registered", file=sys.stderr)
    else:
        print("tokenpak: MCP registration failed (continuing)", file=sys.stderr)

    # ── Step 2: Install hooks ────────────────────────────────
    if config.hooks_enabled:
        from .hooks import ensure_hooks_feature_enabled, install_hooks

        if ensure_hooks_feature_enabled():
            hooks_path = install_hooks(target="global")
            print(f"tokenpak: hooks installed ({hooks_path})", file=sys.stderr)
        else:
            print(
                "tokenpak: hooks feature could not be enabled",
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

    fleet = _fleet_state_enabled()
    forwarded = _maybe_inject_bypass_flag(args, env, fleet=fleet)
    banner = _fleet_banner(env, fleet=fleet)
    if banner:
        print(banner, file=sys.stderr)
    codex_args = ["codex", *forwarded]
    os.execvpe("codex", codex_args, env)

    print("tokenpak: failed to launch codex", file=sys.stderr)
    return 1
