# SPDX-License-Identifier: Apache-2.0
"""Credential hazard detector.

Each check returns ``list[Issue]`` — empty when healthy. The CLI wraps
them into a single report. Adding a check is "define function, append
to CHECKS".

The one class of bug this subsystem exists to surface is the refresh-
token-reuse failure: two consumers of the same OAuth file, each
trying to refresh, invalidating each other. The cohabitation check
surfaces that pattern before it bites.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from .model import Credential, REFRESH_EXTERNAL, REFRESH_NONE, KIND_OAUTH
from .providers import discover_all
from .providers.user_config import config_perms_ok, CONFIG_PATH as USER_CONFIG_PATH


# 24h grace means "warn one day before expiry" — early enough to act,
# not so early it's spam.
DEFAULT_EXPIRY_GRACE = 24 * 60 * 60


@dataclass(frozen=True)
class Issue:
    severity: str   # "warn" | "error"
    subject: str    # "codex-9f05" | "credentials.toml" | ...
    detail: str     # one-line human-readable explanation


# ── individual checks ────────────────────────────────────────────────


def check_stale_oauth(creds: list[Credential], now: int) -> list[Issue]:
    """Flag OAuth creds that are expired or about to expire."""
    issues: list[Issue] = []
    for c in creds:
        if c.kind != KIND_OAUTH or c.expires_at is None:
            continue
        if now >= c.expires_at:
            issues.append(
                Issue(
                    "error",
                    c.id,
                    f"OAuth expired — re-auth via the owning tool ({c.refresh_owner})",
                )
            )
        elif now >= c.expires_at - DEFAULT_EXPIRY_GRACE:
            mins_left = (c.expires_at - now) // 60
            issues.append(
                Issue(
                    "warn",
                    c.id,
                    f"OAuth expires in ~{mins_left} min",
                )
            )
    return issues


def check_duplicate_ids(creds: list[Credential]) -> list[Issue]:
    """Two credentials with the same id → router can't disambiguate."""
    by_id: dict[str, list[Credential]] = defaultdict(list)
    for c in creds:
        by_id[c.id].append(c)
    issues: list[Issue] = []
    for cid, matches in by_id.items():
        if len(matches) > 1:
            sources = ", ".join(m.source for m in matches)
            issues.append(
                Issue("error", cid, f"{len(matches)} credentials share this id ({sources})")
            )
    return issues


def check_cohabitation(creds: list[Credential]) -> list[Issue]:
    """Two providers pointing at the same file = potential refresh-reuse bug.

    If (say) both ``codex-cli`` and ``openclaw`` name ``~/.codex/auth.json``
    as their source, they are two consumers of the same single-use
    refresh token — exactly the pattern that produced today's
    ``refresh_token_reused`` error.
    """
    by_source_file: dict[str, list[Credential]] = defaultdict(list)
    for c in creds:
        if c.kind != KIND_OAUTH:
            continue
        # Strip `#section` suffix so two profiles inside the same JSON
        # file don't falsely trigger the check.
        file_part = c.source.split("#", 1)[0]
        by_source_file[file_part].append(c)

    issues: list[Issue] = []
    for source_file, matches in by_source_file.items():
        providers = {m.provider for m in matches}
        if len(providers) <= 1:
            continue
        refreshers = {m.provider for m in matches if m.refresh_owner == REFRESH_EXTERNAL}
        if len(refreshers) > 1:
            issues.append(
                Issue(
                    "error",
                    source_file,
                    f"multiple refresh owners ({sorted(refreshers)}) — rotating tokens will clash",
                )
            )
        else:
            issues.append(
                Issue(
                    "warn",
                    source_file,
                    f"{len(matches)} consumers ({sorted(providers)}) — single refresh owner, but watch the sync",
                )
            )
    return issues


def check_orphan_oauth(creds: list[Credential]) -> list[Issue]:
    """OAuth with refresh_owner=none is a bug — nobody will refresh it."""
    return [
        Issue("error", c.id, "OAuth credential has no refresh owner")
        for c in creds
        if c.kind == KIND_OAUTH and c.refresh_owner == REFRESH_NONE
    ]


def check_user_config_perms() -> list[Issue]:
    if not USER_CONFIG_PATH.exists():
        return []
    if config_perms_ok():
        return []
    return [
        Issue(
            "error",
            str(USER_CONFIG_PATH),
            "perms are not 0600 — run `chmod 600 ~/.tokenpak/credentials.toml`",
        )
    ]


# ── runner ──────────────────────────────────────────────────────────


Check = Callable[[list[Credential], int], list[Issue]]

# Note: ``check_user_config_perms`` takes no args; handled separately in ``run``.
CREDS_CHECKS: list[tuple[str, Check]] = [
    ("stale-oauth", check_stale_oauth),
    ("duplicate-ids", lambda creds, _now: check_duplicate_ids(creds)),
    ("cohabitation", lambda creds, _now: check_cohabitation(creds)),
    ("orphan-oauth", lambda creds, _now: check_orphan_oauth(creds)),
]


def run(creds: list[Credential] | None = None) -> list[Issue]:
    """Run every check, return the flat list of issues."""
    if creds is None:
        creds = discover_all()
    now = int(time.time())

    issues: list[Issue] = []
    for _name, check in CREDS_CHECKS:
        try:
            issues.extend(check(creds, now))
        except Exception as exc:  # keep one bad check from killing doctor
            issues.append(Issue("warn", "doctor", f"check {_name} errored: {exc}"))
    issues.extend(check_user_config_perms())
    return issues
