# SPDX-License-Identifier: Apache-2.0
"""Register / unregister the tokenpak companion MCP server with Codex.

Uses ``codex mcp add`` / ``codex mcp remove`` so the config lives in
Codex's own config store (``$CODEX_HOME/config.toml``) and is visible to
``codex mcp list``.

The MCP server binary is the same stdio JSON-RPC server used by Claude Code
(``python3 -m tokenpak.companion.mcp.server``).  Only the discovery
mechanism differs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tokenpak.companion.config import CompanionConfig

SERVER_NAME = "tokenpak-companion"


def _codex_env(codex_home: Path | None = None) -> dict[str, str]:
    """Return an environment scoped to the selected Codex home."""
    env = os.environ.copy()
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    return env


def is_registered() -> bool:
    """Check registration using the active public Codex configuration."""
    return _is_registered()


def _is_registered(codex_home: Path | None = None) -> bool:
    """Check whether the companion MCP server is already registered."""
    try:
        result = subprocess.run(
            ["codex", "mcp", "get", SERVER_NAME],
            capture_output=True,
            env=_codex_env(codex_home),
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def register(
    env_vars: Optional[dict[str, str]] = None,
) -> bool:
    """Register using the active public Codex configuration."""
    return _register(env_vars)


def _register(
    env_vars: Optional[dict[str, str]] = None,
    *,
    codex_home: Path | None = None,
) -> bool:
    """Register the companion MCP server via ``codex mcp add``.

    Returns True if registration succeeded (or was already registered).
    """
    if _is_registered(codex_home):
        return True

    cmd = [
        "codex",
        "mcp",
        "add",
        SERVER_NAME,
        "--",
        sys.executable,
        "-m",
        "tokenpak.companion.mcp.server",
    ]

    # Pass companion env vars to the MCP server process.
    # Codex documents `--env KEY=VALUE` (space-separated); insert before "--".
    if env_vars:
        insert_at = cmd.index("--")
        for k, v in env_vars.items():
            cmd[insert_at:insert_at] = ["--env", f"{k}={v}"]
            insert_at += 2

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            env=_codex_env(codex_home),
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            print(
                f"tokenpak: failed to register MCP server: {result.stderr.strip()}",
                file=sys.stderr,
            )
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"tokenpak: codex not available: {e}", file=sys.stderr)
        return False


def unregister() -> bool:
    """Remove registration using the active public Codex configuration."""
    return _unregister()


def _unregister(codex_home: Path | None = None) -> bool:
    """Remove the companion MCP server registration."""
    if not _is_registered(codex_home):
        return True

    try:
        result = subprocess.run(
            ["codex", "mcp", "remove", SERVER_NAME],
            capture_output=True,
            env=_codex_env(codex_home),
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_env_vars(config: "CompanionConfig") -> dict[str, str]:
    """Build env vars to forward to the MCP server subprocess."""

    env: dict[str, str] = {}
    if config.budget_daily_usd > 0:
        env["TOKENPAK_COMPANION_BUDGET"] = str(config.budget_daily_usd)
    if config.profile != "balanced":
        env["TOKENPAK_COMPANION_PROFILE"] = config.profile
    if str(config.journal_dir) != str(
        config.journal_dir.__class__.home() / ".tokenpak" / "companion"
    ):
        env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(config.journal_dir)
    return env
