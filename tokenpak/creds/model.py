# SPDX-License-Identifier: Apache-2.0
"""Credential data model.

Credentials carry the minimum shape needed for (a) discovery UI, (b)
router selection, and (c) hazard detection. The actual secret value is
intentionally held off the object — providers are asked for it at
injection time so the secret doesn't sit in memory longer than needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Refresh ownership. The single-owner invariant is what prevents the
# "refresh token already used" OAuth failure mode — exactly one process
# must own refresh per credential.
REFRESH_EXTERNAL = "external"   # a CLI tool owns refresh; tokenpak only reads
REFRESH_TOKENPAK = "tokenpak"   # tokenpak owns refresh (not MVP)
REFRESH_NONE = "none"           # static API key; no refresh


# Credential kinds. Open set; providers may introduce new ones.
KIND_OAUTH = "oauth"
KIND_API_KEY = "api_key"
KIND_BEARER = "bearer"


@dataclass(frozen=True)
class Credential:
    """A discovered credential.

    Immutable snapshot of what a provider found at a given moment.
    Re-run discovery to pick up changes (token refresh, new BYOK key).
    """

    id: str                          # stable slug, e.g. "codex-9f05-personal"
    platform: str                    # "openai" | "anthropic" | "google" | ...
    kind: str                        # KIND_OAUTH | KIND_API_KEY | KIND_BEARER
    source: str                      # human-readable origin (path, env var, etc.)
    provider: str                    # provider module name that found this
    refresh_owner: str = REFRESH_NONE

    # Optional metadata — populated when the provider can cheaply extract it.
    expires_at: Optional[int] = None         # unix seconds; oauth only
    account_hint: Optional[str] = None       # email / account id for display
    scope_hosts: tuple[str, ...] = field(default_factory=tuple)

    # Where to go for the secret value. Providers interpret this
    # themselves — typically a file path or env var name.
    secret_ref: Optional[str] = None

    def is_stale(self, now: int, grace_seconds: int = 0) -> bool:
        """True if this is an OAuth cred past its expiry.

        ``grace_seconds`` lets the caller mark creds "about to expire"
        as stale too, which is what the doctor uses to warn early.
        """
        if self.kind != KIND_OAUTH or self.expires_at is None:
            return False
        return now >= (self.expires_at - grace_seconds)
