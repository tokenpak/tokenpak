# SPDX-License-Identifier: Apache-2.0
"""Discover Claude CLI's OAuth credentials.

The Claude CLI owns refresh for ``~/.claude/.credentials.json``.
tokenpak reads only (the proxy's ``_load_claude_cli_token`` already
does this — this provider exposes it to ``tokenpak creds list``).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import Credential, REFRESH_EXTERNAL, KIND_OAUTH


PROVIDER_NAME = "claude-cli"
CREDS_PATH = Path.home() / ".claude" / ".credentials.json"


def discover() -> list[Credential]:
    if not CREDS_PATH.exists():
        return []

    try:
        data = json.loads(CREDS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    oauth = data.get("claudeAiOauth") or {}
    access = oauth.get("accessToken")
    if not access:
        return []

    # expiresAt is ms since epoch in the Claude CLI's file format.
    expires_ms = oauth.get("expiresAt")
    expires_at = int(expires_ms // 1000) if isinstance(expires_ms, (int, float)) else None

    subscription = oauth.get("subscriptionType") or "personal"
    cred_id = f"claude-{subscription}"

    return [
        Credential(
            id=cred_id,
            platform="anthropic",
            kind=KIND_OAUTH,
            source=str(CREDS_PATH),
            provider=PROVIDER_NAME,
            refresh_owner=REFRESH_EXTERNAL,
            expires_at=expires_at,
            account_hint=subscription,
            scope_hosts=("api.anthropic.com",),
            secret_ref=str(CREDS_PATH),
        )
    ]
