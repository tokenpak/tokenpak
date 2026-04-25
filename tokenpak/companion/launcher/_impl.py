"""Companion launcher — writes temp config files and execs Claude Code."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from tokenpak.companion.config import CompanionConfig


def _write_mcp_json(config: CompanionConfig, run_dir: Path) -> Path:
    """Generate mcp.json pointing to the companion MCP server module.

    Uses ``-P`` (Python 3.11+) to suppress prepending cwd to sys.path.
    Same mitigation as the UserPromptSubmit hook: Claude Code launches
    MCP servers with its own cwd (typically the user's project dir),
    and if that dir happens to contain a sibling ``tokenpak/`` directory
    (as the tokenpak repo root does), Python would resolve ``import
    tokenpak`` to the namespace directory instead of the editable-
    install finder, causing
    ``ImportError: cannot import name '__version__' from 'tokenpak'``.

    ``-P`` blocks that path collision deterministically — the editable
    finder wins regardless of cwd.
    """
    mcp_path = run_dir / "mcp.json"
    payload = {
        "mcpServers": {
            "tokenpak-companion": {
                "command": sys.executable,
                "args": ["-P", "-m", config.mcp_module],
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
    # `-P` suppresses adding the cwd to sys.path. Without it, running
    # from a directory that has a sibling `tokenpak/` dir (like the repo
    # root) makes Python resolve `import tokenpak` to that namespace
    # directory, which has no `__version__` and bypasses the editable
    # install finder. Claude Code's hook runs with cwd = the project
    # dir, which triggers exactly that collision for repo-local work.
    hook_cmd = f"{sys.executable} -P -m {config.hook_module}"
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


def _wire_proxy_env(env: dict) -> dict:
    """Route Claude Code's outbound API traffic through the local TokenPak proxy.

    Sets ANTHROPIC_BASE_URL and OPENAI_BASE_URL so requests hit
    ``http://127.0.0.1:<TOKENPAK_PORT>`` (default 8766), where the proxy
    can compress + meter + log them into monitor.db for ``tokenpak status``.

    Respects any pre-existing values — users who have intentionally
    overridden these (e.g. to a remote proxy) are not clobbered.
    Suppressed entirely when ``TOKENPAK_PROXY_BYPASS=1`` is set.
    """
    if env.get("TOKENPAK_PROXY_BYPASS") == "1":
        return env
    port = env.get("TOKENPAK_PORT", "8766")
    proxy_url = f"http://127.0.0.1:{port}"
    env.setdefault("ANTHROPIC_BASE_URL", proxy_url)
    env.setdefault("OPENAI_BASE_URL", f"{proxy_url}/v1")
    return env


def _wire_codex_env(env: dict) -> dict:
    """Route codex CLI traffic through the local TokenPak proxy.

    The codex CLI reads ``OPENAI_BASE_URL`` for ChatGPT-OAuth-mode calls
    and falls back to the ChatGPT backend URL otherwise. Setting it to
    the proxy lets the proxy apply compression / caching / telemetry +
    credential precedence (caller's own ``OPENAI_API_KEY`` wins; else
    managed creds get injected).

    Same ``TOKENPAK_PROXY_BYPASS=1`` opt-out as the Claude path.
    """
    if env.get("TOKENPAK_PROXY_BYPASS") == "1":
        return env
    port = env.get("TOKENPAK_PORT", "8766")
    proxy_url = f"http://127.0.0.1:{port}"
    # Codex's pi-ai connector posts to ``<baseUrl>/codex/responses`` and
    # ``<baseUrl>/v1/responses`` depending on shape. Pointing at the
    # proxy root lets both work; the proxy's path matcher routes either.
    env.setdefault("OPENAI_BASE_URL", proxy_url)
    return env


def launch_codex(extra_args: Optional[List[str]] = None) -> None:
    """Build env-routed launch for the codex CLI.

    Mirrors :func:`launch` for Claude Code: sets ``OPENAI_BASE_URL`` to
    the local TokenPak proxy and execs ``codex`` with any forwarded
    args. Codex's own AGENTS.md / hooks / MCP discovery from the cwd
    keeps working — we only redirect outbound HTTP through the proxy.

    The companion MCP / hook layer is *not* wired in yet for codex
    (Kevin 2026-04-15 architecture parks that as a follow-up: native
    MCP + hooks + AGENTS.md + skills, NOT a wrapper). For now this
    launcher is purely env wiring.
    """
    env = _wire_codex_env(dict(os.environ))
    cmd_args = ["codex"] + (extra_args or [])
    try:
        os.execvpe("codex", cmd_args, env)
    except OSError:
        import subprocess  # noqa: PLC0415

        subprocess.run(cmd_args, env=env, check=False)


def launch(
    config: Optional[CompanionConfig] = None,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Build companion config files and exec Claude Code with companion active.

    Uses a fixed run directory (``~/.tokenpak/companion/run/`` by default) so
    files persist across sessions — no cleanup is needed and the location is
    predictable for debugging.

    Routes Claude Code's API traffic through the local TokenPak proxy by
    setting ANTHROPIC_BASE_URL + OPENAI_BASE_URL in the child env. Set
    ``TOKENPAK_PROXY_BYPASS=1`` to disable.

    When companion is disabled (``TOKENPAK_COMPANION_ENABLED=false``), passes
    through directly to the ``claude`` binary with no extra flags (but still
    routes traffic through the proxy unless bypass is set).

    Args:
        config: Companion configuration.  Defaults to :meth:`CompanionConfig.from_env`.
        extra_args: Additional arguments forwarded verbatim to the ``claude`` binary.
    """
    if config is None:
        config = CompanionConfig.from_env()

    env = _wire_proxy_env(dict(os.environ))

    if not config.enabled:
        cmd_args = ["claude"] + (extra_args or [])
        try:
            os.execvpe("claude", cmd_args, env)
        except OSError:
            import subprocess  # noqa: PLC0415

            subprocess.run(cmd_args, env=env, check=False)
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
        os.execvpe("claude", cmd_args, env)
    except OSError:
        import subprocess  # noqa: PLC0415

        subprocess.run(cmd_args, env=env, check=False)
