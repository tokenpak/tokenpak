# SPDX-License-Identifier: Apache-2.0
"""Discover credentials the user pasted via ``tokenpak creds add``.

File format (TOML)::

    [creds.openai-work]
    platform = "openai"
    kind = "api_key"
    key = "sk-..."
    scope_hosts = ["api.openai.com"]

    [creds.anthropic-byok]
    platform = "anthropic"
    kind = "api_key"
    key = "sk-ant-..."

``tokenpak creds add`` / ``tokenpak creds remove`` write this file;
this module only reads (shared by discovery + doctor).

Perms are expected to be 0600; ``creds doctor`` flags looser perms.
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from ..model import REFRESH_NONE, REFRESH_TOKENPAK, Credential

PROVIDER_NAME = "user-config"
CONFIG_PATH = Path.home() / ".tokenpak" / "credentials.toml"


def discover() -> list[Credential]:
    if not CONFIG_PATH.exists():
        return []

    try:
        raw = CONFIG_PATH.read_bytes()
        data = tomllib.loads(raw.decode())
    except (OSError, UnicodeDecodeError, Exception):
        return []

    entries = data.get("creds") or {}
    if not isinstance(entries, dict):
        return []

    creds: list[Credential] = []
    for cred_id, body in entries.items():
        if not isinstance(body, dict):
            continue

        platform = str(body.get("platform", "unknown")).lower()
        kind = str(body.get("kind", "api_key")).lower()
        refresh_owner = REFRESH_TOKENPAK if kind == "oauth" else REFRESH_NONE

        scope = body.get("scope_hosts") or ()
        if isinstance(scope, list):
            scope = tuple(str(h) for h in scope)
        else:
            scope = ()

        creds.append(
            Credential(
                id=str(cred_id),
                platform=platform,
                kind=kind,
                source=f"{CONFIG_PATH}#creds.{cred_id}",
                provider=PROVIDER_NAME,
                refresh_owner=refresh_owner,
                expires_at=body.get("expires_at"),
                account_hint=body.get("account_hint"),
                scope_hosts=scope,
                # secret_ref points back into the config — value is pulled at injection time.
                secret_ref=f"user-config:{cred_id}",
            )
        )
    return creds


def resolve(cred: Credential) -> "str | None":
    """Re-read credentials.toml and return the secret for ``cred.id``.

    We never cache the secret in memory — discovery returns a
    :class:`Credential` without the value, and resolution happens on
    demand. That way a ``creds remove`` is effective immediately.
    """
    if not CONFIG_PATH.exists():
        return None
    try:
        data = tomllib.loads(CONFIG_PATH.read_text())
    except Exception:
        return None
    entries = data.get("creds") or {}
    if not isinstance(entries, dict):
        return None
    body = entries.get(cred.id) or {}
    # Prefer kind-appropriate field, fall back to whichever is present.
    if cred.kind == "oauth" or cred.kind == "bearer":
        value = body.get("token") or body.get("key")
    else:
        value = body.get("key") or body.get("token")
    return value if isinstance(value, str) and value else None


def config_perms_ok() -> bool:
    """Return True iff credentials.toml has owner-only perms (or is absent).

    Used by :mod:`creds.doctor` to surface over-permissive file modes
    without prescribing a fix here.
    """
    if not CONFIG_PATH.exists():
        return True
    try:
        mode = CONFIG_PATH.stat().st_mode & 0o777
    except OSError:
        return True
    return mode == 0o600
