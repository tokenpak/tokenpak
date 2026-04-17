# SPDX-License-Identifier: Apache-2.0
"""Discover OpenClaw agent auth-profiles.

OpenClaw stores one or more named profiles per agent under
``~/.openclaw/agents/<agent>/agent/auth-profiles.json``. Each profile
is a credential the agent may route through tokenpak.

The profile format is documented in ``project_tokenpak_openclaw.md``;
here we just surface what's present so ``creds list`` shows the full
multi-agent credential surface on a host.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import Credential, REFRESH_EXTERNAL, REFRESH_NONE, KIND_OAUTH


PROVIDER_NAME = "openclaw"
AGENTS_ROOT = Path.home() / ".openclaw" / "agents"


def _provider_to_platform(provider: str) -> str:
    """Collapse OpenClaw provider names down to our platform slugs.

    ``tokenpak-anthropic`` / ``anthropic`` both → "anthropic" etc., so
    routing doesn't need to memorise the tokenpak-prefixed variants.
    """
    p = provider.lower().removeprefix("tokenpak-")
    if p.startswith("openai"):
        return "openai"
    if p.startswith("anthropic"):
        return "anthropic"
    if p.startswith("google"):
        return "google"
    if p.startswith("xai") or p.startswith("grok"):
        return "xai"
    return p or "unknown"


def _kind_from_profile(profile_type: str) -> str:
    if profile_type == "oauth":
        return KIND_OAUTH
    return "api_key" if profile_type == "api_key" else "bearer"


def discover() -> list[Credential]:
    if not AGENTS_ROOT.exists():
        return []

    creds: list[Credential] = []
    for agent_dir in sorted(AGENTS_ROOT.iterdir()):
        if not agent_dir.is_dir():
            continue
        profiles_file = agent_dir / "agent" / "auth-profiles.json"
        if not profiles_file.exists():
            continue

        try:
            data = json.loads(profiles_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        profiles = data.get("profiles") or {}
        for profile_id, body in profiles.items():
            if not isinstance(body, dict):
                continue

            provider_name = body.get("provider") or "unknown"
            platform = _provider_to_platform(str(provider_name))
            kind = _kind_from_profile(str(body.get("type", "")))
            expires = body.get("expires")
            expires_at = int(expires // 1000) if isinstance(expires, (int, float)) else None

            # OpenClaw owns refresh for its oauth entries; static keys have none.
            refresh_owner = REFRESH_EXTERNAL if kind == KIND_OAUTH else REFRESH_NONE

            creds.append(
                Credential(
                    id=f"openclaw-{agent_dir.name}-{profile_id}",
                    platform=platform,
                    kind=kind,
                    source=f"{profiles_file}#profiles.{profile_id}",
                    provider=PROVIDER_NAME,
                    refresh_owner=refresh_owner,
                    expires_at=expires_at,
                    account_hint=body.get("accountId"),
                    scope_hosts=_scope_for(platform),
                    secret_ref=f"openclaw:{agent_dir.name}:{profile_id}",
                )
            )
    return creds


def _scope_for(platform: str) -> tuple[str, ...]:
    return {
        "anthropic": ("api.anthropic.com",),
        "openai": ("api.openai.com", "chatgpt.com"),
        "google": ("generativelanguage.googleapis.com",),
        "xai": ("api.x.ai",),
    }.get(platform, ())
