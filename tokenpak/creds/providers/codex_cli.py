# SPDX-License-Identifier: Apache-2.0
"""Discover Codex CLI's OAuth credentials.

Codex owns refresh for its own auth.json — tokenpak reads only.
Respects ``CODEX_HOME`` per ``project_tokenpak_codex_three_paths.md``
(launcher hazard #1: never assume ~/.codex).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from ..model import Credential, REFRESH_EXTERNAL, KIND_OAUTH


PROVIDER_NAME = "codex-cli"


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _decode_jwt_exp(jwt: str) -> "tuple[int | None, str | None]":
    """Return (exp, account_id) from a Codex access_token JWT.

    Best-effort: returns (None, None) on any parse failure. We never
    validate the signature — we only peek at the public claims for
    display.
    """
    try:
        parts = jwt.split(".")
        if len(parts) < 2:
            return None, None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload.get("exp")
        account_id = (
            payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        )
        return (int(exp) if exp else None), account_id
    except Exception:
        return None, None


def resolve(cred: Credential) -> "str | None":
    """Return the current access_token from Codex's auth.json.

    We re-read the file every call so token rotation (owned by Codex
    CLI) is picked up without a cache. mtime-caching belongs in the
    caller if needed.
    """
    path = Path(cred.secret_ref) if cred.secret_ref else _codex_home() / "auth.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    access = (data.get("tokens") or {}).get("access_token")
    return access if isinstance(access, str) and access else None


def discover() -> list[Credential]:
    auth_path = _codex_home() / "auth.json"
    if not auth_path.exists():
        return []

    try:
        data = json.loads(auth_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        return []

    exp, account_id = _decode_jwt_exp(access)
    account_id = account_id or tokens.get("account_id")

    cred_id_suffix = (account_id or "default")[:8] if account_id else "default"
    cred_id = f"codex-{cred_id_suffix}"

    return [
        Credential(
            id=cred_id,
            platform="openai",
            kind=KIND_OAUTH,
            source=str(auth_path),
            provider=PROVIDER_NAME,
            refresh_owner=REFRESH_EXTERNAL,
            expires_at=exp,
            account_hint=account_id,
            scope_hosts=("chatgpt.com", "api.openai.com"),
            secret_ref=str(auth_path),
        )
    ]
