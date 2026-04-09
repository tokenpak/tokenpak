"""
tokenpak.integrations.claude_code.agent_sdk_helpers
====================================================

Helper module that exposes the tokenpak plugin's MCP server, hooks, and
subagents as programmatic objects for use with the Claude Agent SDK.

Usage::

    from claude_agent_sdk import query, ClaudeAgentOptions
    from tokenpak.integrations.claude_code.agent_sdk_helpers import (
        tokenpak_mcp_servers,
        tokenpak_hooks,
        tokenpak_agents,
    )

    async for msg in query(
        prompt="Review this PR",
        options=ClaudeAgentOptions(
            mcp_servers=tokenpak_mcp_servers(),
            hooks=tokenpak_hooks(),
            agents=tokenpak_agents(license_key=os.getenv("TOKENPAK_LICENSE_KEY")),
        ),
    ):
        ...

Lazy-import discipline
----------------------
All imports from ``claude_agent_sdk`` are deferred into function bodies.
Importing this module does NOT require the SDK to be installed.  Each helper
raises ``ImportError`` with a clear message if the SDK is missing and the
caller tries to use it.

See CCP-22 mode matrix for the architectural context behind this module.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).parent / "plugin" / "agents"
_PLUGIN_DATA_DEFAULT = Path.home() / ".claude" / "plugin-data" / "tokenpak-claude-code"

# Module-level license cache: license_key → (cached_at, is_pro)
_license_cache: dict[str, tuple[float, bool]] = {}
_LICENSE_CACHE_TTL = 300  # 5 minutes


# ─────────────────────────────────────────────────────────────────────────────
# MCP servers helper
# ─────────────────────────────────────────────────────────────────────────────


def tokenpak_mcp_servers(
    vault_root: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> dict[str, Any]:
    """Return the Agent SDK ``mcp_servers`` dict for the tokenpak MCP server.

    The server is spawned via stdio using the
    ``tokenpak.integrations.claude_code.mcp_server`` entrypoint (CCP-06).

    Args:
        vault_root: Override for the vault root path.  Falls back to
            ``TOKENPAK_VAULT_ROOT`` environment variable.
        proxy_url: Override for the proxy URL.  Falls back to
            ``TOKENPAK_PROXY_URL`` environment variable.

    Returns:
        Dict suitable for ``ClaudeAgentOptions(mcp_servers=...)``.
    """
    env: dict[str, str] = {}
    effective_vault_root = vault_root or os.environ.get("TOKENPAK_VAULT_ROOT", "")
    effective_proxy_url = proxy_url or os.environ.get("TOKENPAK_PROXY_URL", "")
    if effective_vault_root:
        env["TOKENPAK_VAULT_ROOT"] = str(effective_vault_root)
    if effective_proxy_url:
        env["TOKENPAK_PROXY_URL"] = effective_proxy_url

    config: dict[str, Any] = {
        "command": "python",
        "args": ["-m", "tokenpak.integrations.claude_code.mcp_server"],
    }
    if env:
        config["env"] = env

    return {"tokenpak": config}


# ─────────────────────────────────────────────────────────────────────────────
# Hooks helper
# ─────────────────────────────────────────────────────────────────────────────


def tokenpak_hooks() -> dict[str, list[Any]]:
    """Return the Agent SDK ``hooks`` dict for the tokenpak plugin hooks.

    Registers the following callbacks:

    - **PostToolUse / all tools**: telemetry stamp (equivalent to
      ``telemetry-stamp.sh``, CCP-16).
    - **PreToolUse / Edit|Write|Bash**: protect-paths guard (blocks writes to
      paths listed in ``TOKENPAK_PROTECTED_PATHS``).
    - **PreToolUse / Bash** (Pro): review-prep enforcement (equivalent to
      ``review-prep.sh``, CCP-17).  Conditionally registered when
      ``TOKENPAK_LICENSE_KEY`` is set and resolves to a Pro (or higher) tier.

    All ``claude_agent_sdk`` imports are lazy.

    Returns:
        Dict suitable for ``ClaudeAgentOptions(hooks=...)``.

    Raises:
        ImportError: If ``claude_agent_sdk`` is not installed.
    """
    try:
        from claude_agent_sdk import HookMatcher  # noqa: F401 — verify importable
    except ImportError:
        raise ImportError("install claude_agent_sdk to use tokenpak SDK helpers")

    # ── PostToolUse / all tools: telemetry stamp ──────────────────────────────

    async def telemetry_stamp_callback(
        input_data: Any,
        tool_use_id: Optional[str],
        context: Any,
    ) -> dict:
        """Write one JSONL telemetry line per tool call (mirrors telemetry-stamp.sh)."""
        try:
            tool_name = ""
            file_path = ""
            exit_code = 0
            duration_ms = 0

            if isinstance(input_data, dict):
                tool_name = input_data.get("tool_name", "")
                file_path = (
                    input_data.get("file_path")
                    or (input_data.get("tool_input") or {}).get("file_path", "")
                    or ""
                )
                exit_code = int(input_data.get("exit_code", 0) or 0)
                duration_ms = int(input_data.get("duration_ms", 0) or 0)

            session_id = os.environ.get("CLAUDE_SESSION_ID", "")
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            record = {
                "session_id": session_id,
                "ts": ts,
                "tool_name": str(tool_name),
                "file_path": str(file_path),
                "exit_code": exit_code,
                "duration_ms": duration_ms,
            }

            plugin_data_dir = Path(
                os.environ.get("CLAUDE_PLUGIN_DATA", str(_PLUGIN_DATA_DEFAULT))
            )
            telemetry_dir = plugin_data_dir / "telemetry"
            jsonl_file = telemetry_dir / f"{date_str}.jsonl"

            try:
                telemetry_dir.mkdir(parents=True, exist_ok=True)
                line = json.dumps(record, separators=(",", ":"))
                # Enforce PIPE_BUF cap (4096 bytes) for append atomicity
                if len(line.encode()) >= 4096:
                    record["file_path"] = "[truncated]"
                    line = json.dumps(record, separators=(",", ":"))
                with jsonl_file.open("a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass  # loss-tolerant: disk-full, permission-denied → continue
        except Exception:  # pragma: no cover — never let telemetry crash the agent
            pass
        return {}

    # ── PreToolUse / Edit|Write|Bash: protect-paths guard ────────────────────

    async def protect_paths_callback(
        input_data: Any,
        tool_use_id: Optional[str],
        context: Any,
    ) -> dict:
        """Block writes to paths listed in TOKENPAK_PROTECTED_PATHS."""
        protected_raw = os.environ.get("TOKENPAK_PROTECTED_PATHS", "")
        if not protected_raw:
            return {}

        protected = [p.strip() for p in protected_raw.split(":") if p.strip()]
        if not protected:
            return {}

        file_path = ""
        if isinstance(input_data, dict):
            file_path = (
                input_data.get("file_path")
                or (input_data.get("tool_input") or {}).get("file_path", "")
                or ""
            )

        if not file_path:
            return {}

        resolved = str(Path(file_path).expanduser().resolve())
        for guard in protected:
            guard_resolved = str(Path(guard).expanduser().resolve())
            if resolved == guard_resolved or resolved.startswith(guard_resolved + "/"):
                return {
                    "decision": "block",
                    "systemMessage": (
                        f"tokenpak protect-paths: write to '{file_path}' is blocked "
                        f"(matches protected path '{guard}')."
                    ),
                }
        return {}

    # ── PreToolUse / Bash (Pro): review-prep enforcement ─────────────────────

    async def review_prep_callback(
        input_data: Any,
        tool_use_id: Optional[str],
        context: Any,
    ) -> dict:
        """Block git push / gh pr create when no fresh review packet exists (Pro)."""
        command = ""
        if isinstance(input_data, dict):
            command = (
                input_data.get("command")
                or (input_data.get("tool_input") or {}).get("command", "")
                or ""
            )

        import re

        if not re.match(r"^git push|^gh pr create", command):
            return {}

        try:
            import subprocess

            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            branch = branch_result.stdout.strip()
            if not branch or branch == "HEAD":
                return {}

            safe_branch = branch.replace("/", "-")
            max_age_minutes = float(os.environ.get("TOKENPAK_REVIEW_MAX_AGE_MINUTES", "30"))
            plugin_data_dir = Path(
                os.environ.get("CLAUDE_PLUGIN_DATA", str(_PLUGIN_DATA_DEFAULT))
            )
            cache_dir = plugin_data_dir / "review-cache"

            cache_file: Optional[Path] = None
            for candidate in [
                cache_dir / safe_branch,
                cache_dir / f"{safe_branch}.json",
                cache_dir / branch,
                cache_dir / f"{branch}.json",
            ]:
                if candidate.exists():
                    cache_file = candidate
                    break

            if cache_file is None:
                return {
                    "decision": "block",
                    "systemMessage": (
                        f"tokenpak review-prep: No review packet found for branch '{branch}'.\n"
                        "Run `/review-pack` to generate one, then retry."
                    ),
                }

            age_minutes = (time.time() - cache_file.stat().st_mtime) / 60
            if age_minutes >= max_age_minutes:
                return {
                    "decision": "block",
                    "systemMessage": (
                        f"tokenpak review-prep: Review packet for branch '{branch}' is older "
                        f"than {int(max_age_minutes)} minutes. Re-run `/review-pack`."
                    ),
                }
        except Exception:
            return {}  # fail open

        return {}

    # ── Assemble hooks dict ───────────────────────────────────────────────────

    from claude_agent_sdk import HookMatcher

    hooks: dict[str, list[Any]] = {
        "PostToolUse": [
            HookMatcher(matcher="*", hooks=[telemetry_stamp_callback]),
        ],
        "PreToolUse": [
            HookMatcher(matcher="Edit|Write|Bash", hooks=[protect_paths_callback]),
        ],
    }

    # Pro: review-prep enforcement (conditionally registered)
    license_key = os.environ.get("TOKENPAK_LICENSE_KEY", "")
    if license_key and _is_pro_license(license_key):
        hooks["PreToolUse"].append(
            HookMatcher(matcher="Bash", hooks=[review_prep_callback])
        )

    return hooks


# ─────────────────────────────────────────────────────────────────────────────
# Agents helper
# ─────────────────────────────────────────────────────────────────────────────


def tokenpak_agents(license_key: Optional[str] = None) -> dict[str, Any]:
    """Return the Agent SDK ``agents`` dict for the tokenpak subagents.

    OSS tier: includes ``research-analyst`` only.
    Pro tier (valid ``license_key``): also includes ``security-reviewer`` and
    ``migration-planner``.

    Agent definitions are loaded at runtime from the canonical
    ``plugin/agents/*.md`` files — they are not duplicated in Python.

    Args:
        license_key: tokenpak Pro license token.  Falls back to the
            ``TOKENPAK_LICENSE_KEY`` environment variable if not provided.
            When ``None`` and the env var is unset, only OSS agents are
            registered.

    Returns:
        Dict suitable for ``ClaudeAgentOptions(agents=...)``.

    Raises:
        ImportError: If ``claude_agent_sdk`` is not installed.
    """
    try:
        from claude_agent_sdk import AgentDefinition
    except ImportError:
        raise ImportError("install claude_agent_sdk to use tokenpak SDK helpers")

    effective_key = license_key or os.environ.get("TOKENPAK_LICENSE_KEY", "")
    is_pro = bool(effective_key and _is_pro_license(effective_key))

    agents: dict[str, Any] = {}

    # OSS: research-analyst (always included)
    ra_def = _load_agent_md("research-analyst.md")
    agents["research-analyst"] = AgentDefinition(
        description=ra_def["description"],
        prompt=ra_def["prompt"],
        tools=ra_def.get("tools"),
    )

    if is_pro:
        # Pro: security-reviewer
        sr_def = _load_agent_md("security-reviewer.md")
        agents["security-reviewer"] = AgentDefinition(
            description=sr_def["description"],
            prompt=sr_def["prompt"],
            tools=sr_def.get("tools"),
        )
        # Pro: migration-planner
        mp_def = _load_agent_md("migration-planner.md")
        agents["migration-planner"] = AgentDefinition(
            description=mp_def["description"],
            prompt=mp_def["prompt"],
            tools=mp_def.get("tools"),
        )

    return agents


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _load_agent_md(filename: str) -> dict[str, Any]:
    """Parse frontmatter + body from a plugin/agents/*.md file.

    Returns a dict with at minimum ``name``, ``description``, ``prompt``, and
    optionally ``tools`` (list[str]) and ``disallowedTools`` (list[str]).
    """
    import yaml  # pyyaml is a hard tokenpak dependency

    path = _AGENTS_DIR / filename
    content = path.read_text(encoding="utf-8")

    # Split on frontmatter delimiters
    if content.startswith("---"):
        parts = content.split("---", 2)
        # parts[0] == '' (before first ---), parts[1] == frontmatter, parts[2] == body
        if len(parts) >= 3:
            frontmatter_raw = parts[1]
            body = parts[2].strip()
        else:
            frontmatter_raw = ""
            body = content
    else:
        frontmatter_raw = ""
        body = content

    fm: dict[str, Any] = yaml.safe_load(frontmatter_raw) or {}

    # tools field: may be a comma-separated string or a YAML list
    raw_tools = fm.get("tools", None)
    if isinstance(raw_tools, str):
        tools_list: Optional[list[str]] = [t.strip() for t in raw_tools.split(",") if t.strip()]
    elif isinstance(raw_tools, list):
        tools_list = [str(t).strip() for t in raw_tools]
    else:
        tools_list = None

    return {
        "name": fm.get("name", filename.removesuffix(".md")),
        "description": fm.get("description", ""),
        "prompt": body,
        "tools": tools_list,
        "disallowedTools": fm.get("disallowedTools", None),
    }


def _is_pro_license(license_key: str) -> bool:
    """Check whether ``license_key`` resolves to Pro tier or higher.

    Uses the existing ``LicenseValidator`` (same path as install-time gating).
    Result is cached for ``_LICENSE_CACHE_TTL`` seconds per key to avoid
    re-validating on every call.
    """
    now = time.monotonic()
    cached = _license_cache.get(license_key)
    if cached is not None:
        cached_at, result = cached
        if now - cached_at < _LICENSE_CACHE_TTL:
            return result

    try:
        from tokenpak.infrastructure.license_validation import (
            LicenseTier,
            LicenseValidator,
        )

        validator = LicenseValidator()
        validation = validator.validate(license_key)
        is_pro = validation.is_usable and validation.tier not in (
            LicenseTier.OSS,
        )
    except Exception:
        is_pro = False

    _license_cache[license_key] = (now, is_pro)
    return is_pro
