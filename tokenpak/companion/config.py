# SPDX-License-Identifier: Apache-2.0
"""Companion configuration — env vars, defaults, per-session overrides.

All companion config follows the tokenpak pattern: env vars override file
config, file config overrides defaults.  Companion-specific vars use the
``TOKENPAK_COMPANION_`` prefix.

Env vars
--------
TOKENPAK_COMPANION_ENABLED      Master switch (default: 1)
TOKENPAK_COMPANION_BUDGET       Daily budget in USD (default: 0 = unlimited)
TOKENPAK_COMPANION_PROFILE      Preset: lean | balanced | verbose (default: balanced)
TOKENPAK_COMPANION_JOURNAL_DIR  Journal/capsule storage (default: ~/.tokenpak/companion/)
TOKENPAK_COMPANION_HOOKS        Enable hook pipeline (default: 1)
TOKENPAK_COMPANION_MCP          Enable MCP server (default: 1)
TOKENPAK_COMPANION_SHOW_COST    Show cost estimates in TUI (default: 1)
TOKENPAK_COMPANION_PRUNE_THRESHOLD  Token count above which pruning is suggested (default: 50000)
TOKENPAK_COMPANION_MEMORY_DIRS  Extra memory/knowledge directories to ingest lessons
                                from (default: none).  OS-pathsep- or comma-separated
                                list of directories holding your own Markdown notes —
                                "bring your own knowledge base", no vault schema
                                required.  ``~`` is expanded; empty entries dropped.
TOKENPAK_COMPANION_BARE         Strip Claude Code native context (default: 0)
                                Disables CLAUDE.md, auto memory, prompt history,
                                system prompt injection, settings/hooks overlay,
                                and bypasses permissions. Keeps MCP + --resume/
                                --continue. Designed for OpenClaw adapter where
                                the gateway injects its own tools and history.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CompanionConfig:
    """Runtime configuration for the companion.

    Constructed once at launch, passed to all subsystems.  Subsystems never
    read env vars directly — they receive this config object.
    """

    enabled: bool = True
    budget_daily_usd: float = 0.0
    profile: str = "balanced"
    journal_dir: Path = field(default_factory=lambda: Path.home() / ".tokenpak" / "companion")
    hooks_enabled: bool = True
    mcp_enabled: bool = True
    show_cost: bool = True
    prune_threshold: int = 50_000
    bare: bool = False

    # Generic "bring your own knowledge base" memory sources.  These are
    # directories of the user's own Markdown notes the companion ingests
    # lessons from — distinct from the vault schema and from
    # ``additionalDirectories`` filesystem-access grants (EXTRA_DIRS).
    memory_dirs: list[Path] = field(default_factory=list)

    # Session-scoped (set at launch, immutable after)
    session_id: str = ""
    project_dir: str = ""

    # Derived at runtime (not user-configurable)
    proxy_url: str = ""
    mcp_server_pid: Optional[int] = None

    @property
    def run_dir(self) -> Path:
        """Fixed runtime directory for generated config files (AC5)."""
        return Path.home() / ".tokenpak" / "companion" / "run"

    @classmethod
    def from_env(cls) -> "CompanionConfig":
        """Build config from environment variables + defaults."""
        return cls(
            enabled=_bool("TOKENPAK_COMPANION_ENABLED", True),
            budget_daily_usd=_float("TOKENPAK_COMPANION_BUDGET", 0.0),
            profile=os.environ.get("TOKENPAK_COMPANION_PROFILE", "balanced"),
            journal_dir=Path(
                os.environ.get(
                    "TOKENPAK_COMPANION_JOURNAL_DIR",
                    str(Path.home() / ".tokenpak" / "companion"),
                )
            ),
            hooks_enabled=_bool("TOKENPAK_COMPANION_HOOKS", True),
            mcp_enabled=_bool("TOKENPAK_COMPANION_MCP", True),
            show_cost=_bool("TOKENPAK_COMPANION_SHOW_COST", True),
            prune_threshold=int(os.environ.get("TOKENPAK_COMPANION_PRUNE_THRESHOLD", "50000")),
            bare=_bool("TOKENPAK_COMPANION_BARE", False),
            memory_dirs=_path_list("TOKENPAK_COMPANION_MEMORY_DIRS"),
        )

    def profile_overrides(self) -> None:
        """Apply profile-specific overrides after construction."""
        if self.profile == "lean":
            self.prune_threshold = 20_000
            self.show_cost = True
        elif self.profile == "verbose":
            self.prune_threshold = 100_000


# ---------------------------------------------------------------------------
# Env var helpers
# ---------------------------------------------------------------------------


def _bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes")


def _float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _path_list(key: str) -> list[Path]:
    """Parse an env var holding a list of directories.

    Accepts both the OS path separator (``:`` on POSIX, ``;`` on Windows) and
    commas, so ``~/notes:~/work/journal`` and ``~/notes,~/work/journal`` both
    work.  ``~`` is expanded; surrounding whitespace and empty entries are
    dropped.  Returns ``[]`` when the var is unset or contains only empties —
    never raises, preserving the companion's fail-open posture.
    """
    val = os.environ.get(key)
    if not val:
        return []
    # Normalize the OS path separator to a comma, then split on commas.
    raw = val.replace(os.pathsep, ",")
    out: list[Path] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(Path(os.path.expanduser(part)))
    return out
