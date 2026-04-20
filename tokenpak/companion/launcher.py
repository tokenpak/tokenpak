"""Companion launcher — writes temp config files and execs Claude Code."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from tokenpak.companion.config import CompanionConfig


def _write_mcp_json(config: CompanionConfig, run_dir: Path) -> Path:
    """Generate mcp.json pointing to the companion MCP server module."""
    mcp_path = run_dir / "mcp.json"
    payload = {
        "mcpServers": {
            "tokenpak-companion": {
                "command": sys.executable,
                "args": ["-m", config.mcp_module],
                "env": {
                    "TOKENPAK_COMPANION_LOG_LEVEL": config.log_level,
                    "TOKENPAK_COMPANION_BUDGET_TOKENS": str(config.budget_tokens),
                    "TOKENPAK_COMPANION_MODEL": config.model,
                },
            }
        }
    }
    mcp_path.write_text(json.dumps(payload, indent=2))
    return mcp_path


def _write_settings_json(config: CompanionConfig, run_dir: Path) -> Path:
    """Generate settings.json with UserPromptSubmit hook pointing to companion hook module."""
    settings_path = run_dir / "settings.json"
    hook_cmd = f"{sys.executable} -m {config.hook_module}"
    payload = {
        "hooks": {
            "UserPromptSubmit": [
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
        }
    }
    settings_path.write_text(json.dumps(payload, indent=2))
    return settings_path


def _write_system_prompt(config: CompanionConfig, run_dir: Path) -> Optional[Path]:
    """Write system prompt file if TOKENPAK_COMPANION_SYSTEM_PROMPT is set."""
    if not config.system_prompt:
        return None
    prompt_path = run_dir / "system_prompt.md"
    prompt_path.write_text(config.system_prompt)
    return prompt_path


def launch(
    config: Optional[CompanionConfig] = None,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Build companion config files and exec Claude Code with companion active.

    Uses a fixed run directory (``~/.tokenpak/companion/run/`` by default) so
    files persist across sessions — no cleanup is needed and the location is
    predictable for debugging.

    When companion is disabled (``TOKENPAK_COMPANION_ENABLED=false``), passes
    through directly to the ``claude`` binary with no extra flags.

    Args:
        config: Companion configuration.  Defaults to :meth:`CompanionConfig.from_env`.
        extra_args: Additional arguments forwarded verbatim to the ``claude`` binary.
    """
    if config is None:
        config = CompanionConfig.from_env()

    if not config.enabled:
        cmd_args = ["claude"] + (extra_args or [])
        try:
            os.execvp("claude", cmd_args)
        except OSError:
            import subprocess  # noqa: PLC0415

            subprocess.run(cmd_args, check=False)
        return

    run_dir = config.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)

    mcp_path = _write_mcp_json(config, run_dir)
    settings_path = _write_settings_json(config, run_dir)
    prompt_path = _write_system_prompt(config, run_dir)

    cmd_args = [
        "claude",
        "--mcp-config",
        str(mcp_path),
        "--settings",
        str(settings_path),
    ]
    if prompt_path is not None:
        cmd_args += ["--append-system-prompt-file", str(prompt_path)]
    cmd_args += extra_args or []

    try:
        os.execvp("claude", cmd_args)
    except OSError:
        import subprocess  # noqa: PLC0415

        subprocess.run(cmd_args, check=False)
