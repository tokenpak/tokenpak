"""Configuration schema for the tokenpak companion, read from TOKENPAK_COMPANION_* env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any


def _default_run_dir() -> Path:
    return Path.home() / ".tokenpak" / "companion" / "run"


# Profile presets — each dict contains field overrides applied on top of defaults.
PROFILES: Dict[str, Dict[str, Any]] = {
    "lean": {
        "prune_threshold": 20000,
        "show_cost": True,
    },
    "balanced": {
        "prune_threshold": 50000,
        "show_cost": False,
    },
    "verbose": {
        "prune_threshold": 100000,
        "show_cost": False,
    },
}

_VALID_PROFILES = frozenset(PROFILES)


@dataclass
class CompanionConfig:
    """Configuration for the tokenpak companion.

    All fields are populated from ``TOKENPAK_COMPANION_*`` environment variables
    via :meth:`from_env`.  Defaults are used when a variable is absent.

    Env vars (10 total):
        TOKENPAK_COMPANION_MCP_MODULE      Python module path for the MCP server.
        TOKENPAK_COMPANION_HOOK_MODULE     Python module path for the UserPromptSubmit hook.
        TOKENPAK_COMPANION_RUN_DIR        Directory for generated config files.
        TOKENPAK_COMPANION_BUDGET_TOKENS  Max tokens companion may use per request.
        TOKENPAK_COMPANION_MODEL          Model for companion inference calls.
        TOKENPAK_COMPANION_LOG_LEVEL      Log level (debug/info/warn/error).
        TOKENPAK_COMPANION_SYSTEM_PROMPT  Inline text appended as system prompt.
        TOKENPAK_COMPANION_ENABLED        Set to "false"/"0"/"no" to disable companion.
        TOKENPAK_COMPANION_PROFILE        Pruning profile: lean / balanced / verbose.
        TOKENPAK_COMPANION_PRUNE_THRESHOLD  Token threshold triggering pruning (overrides profile).
    """

    mcp_module: str = "tokenpak.companion.mcp_server"
    hook_module: str = "tokenpak.companion.hooks.pre_send"
    run_dir: Path = field(default_factory=_default_run_dir)
    budget_tokens: int = 2000
    model: str = "claude-haiku-4-5-20251001"
    log_level: str = "info"
    system_prompt: str = ""
    enabled: bool = True
    profile: str = "balanced"
    prune_threshold: int = 50000
    show_cost: bool = False

    def validate(self) -> None:
        """Raise :class:`ValueError` if the config contains invalid values.

        Checks:
        - ``budget_tokens`` must be >= 0.
        - ``profile`` must be one of the known profile names.
        """
        if self.budget_tokens < 0:
            raise ValueError(
                f"budget_tokens must be >= 0, got {self.budget_tokens}"
            )
        if self.profile not in _VALID_PROFILES:
            valid = ", ".join(sorted(_VALID_PROFILES))
            raise ValueError(
                f"Unknown profile {self.profile!r}. Valid options: {valid}"
            )

    @classmethod
    def from_env(cls) -> "CompanionConfig":
        """Build a :class:`CompanionConfig` from TOKENPAK_COMPANION_* env vars."""
        run_dir_str = os.environ.get("TOKENPAK_COMPANION_RUN_DIR", "")
        run_dir = Path(run_dir_str).expanduser() if run_dir_str else _default_run_dir()

        enabled_str = os.environ.get("TOKENPAK_COMPANION_ENABLED", "true").lower()
        enabled = enabled_str not in ("false", "0", "no", "off")

        budget_str = os.environ.get("TOKENPAK_COMPANION_BUDGET_TOKENS", "2000")
        try:
            budget_tokens = int(budget_str)
        except ValueError:
            budget_tokens = 2000

        profile = os.environ.get("TOKENPAK_COMPANION_PROFILE", "balanced")

        # Start with profile preset values, then allow explicit env var overrides.
        preset = PROFILES.get(profile, PROFILES["balanced"])
        prune_threshold = preset["prune_threshold"]
        show_cost = preset["show_cost"]

        prune_str = os.environ.get("TOKENPAK_COMPANION_PRUNE_THRESHOLD", "")
        if prune_str:
            try:
                prune_threshold = int(prune_str)
            except ValueError:
                pass

        return cls(
            mcp_module=os.environ.get(
                "TOKENPAK_COMPANION_MCP_MODULE", "tokenpak.companion.mcp_server"
            ),
            hook_module=os.environ.get(
                "TOKENPAK_COMPANION_HOOK_MODULE", "tokenpak.companion.hooks.pre_send"
            ),
            run_dir=run_dir,
            budget_tokens=budget_tokens,
            model=os.environ.get(
                "TOKENPAK_COMPANION_MODEL", "claude-haiku-4-5-20251001"
            ),
            log_level=os.environ.get("TOKENPAK_COMPANION_LOG_LEVEL", "info"),
            system_prompt=os.environ.get("TOKENPAK_COMPANION_SYSTEM_PROMPT", ""),
            enabled=enabled,
            profile=profile,
            prune_threshold=prune_threshold,
            show_cost=show_cost,
        )
