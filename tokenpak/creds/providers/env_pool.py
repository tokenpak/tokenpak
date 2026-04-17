# SPDX-License-Identifier: Apache-2.0
"""Discover API keys and key pools from environment variables.

Dynamic — no hardcoded platform list. Matches any env var whose name
ends in ``_API_KEY`` or ``_KEY_POOL`` and derives the platform from the
prefix. ``_KEY_POOL`` values are comma-separated lists; each key gets
its own :class:`Credential`.
"""

from __future__ import annotations

import os
import re

from ..model import Credential, REFRESH_NONE, KIND_API_KEY


PROVIDER_NAME = "env-pool"

# Lowercase the prefix and strip any leading underscore so e.g.
# ``_ANTHROPIC_KEY_POOL`` → platform "anthropic".
_SUFFIX_RE = re.compile(r"^_?(?P<prefix>[A-Z][A-Z0-9_]*?)(?P<suffix>_API_KEY|_KEY_POOL)$")


def _platform_from_prefix(prefix: str) -> str:
    # Strip trailing underscores, lowercase, split common compound names.
    p = prefix.strip("_").lower()
    # Normalise common variants so "OPENAI" and "openai_admin" both sort
    # cleanly under "openai" in the display.
    for known in ("anthropic", "openai", "google", "gemini", "xai", "grok", "mistral", "cohere"):
        if p.startswith(known):
            return "google" if known == "gemini" else "xai" if known == "grok" else known
    return p or "unknown"


def discover() -> list[Credential]:
    creds: list[Credential] = []
    for var, value in os.environ.items():
        if not value or not value.strip():
            continue
        match = _SUFFIX_RE.match(var)
        if not match:
            continue

        platform = _platform_from_prefix(match.group("prefix"))
        suffix = match.group("suffix")

        if suffix == "_KEY_POOL":
            keys = [k.strip() for k in value.split(",") if k.strip()]
            for i, _key in enumerate(keys):
                creds.append(
                    Credential(
                        id=f"{platform}-env-pool-{i}",
                        platform=platform,
                        kind=KIND_API_KEY,
                        source=f"env:{var}[{i}]",
                        provider=PROVIDER_NAME,
                        refresh_owner=REFRESH_NONE,
                        secret_ref=f"{var}#{i}",
                    )
                )
        else:  # _API_KEY
            creds.append(
                Credential(
                    id=f"{platform}-env",
                    platform=platform,
                    kind=KIND_API_KEY,
                    source=f"env:{var}",
                    provider=PROVIDER_NAME,
                    refresh_owner=REFRESH_NONE,
                    secret_ref=var,
                )
            )
    return creds
