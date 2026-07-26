# SPDX-License-Identifier: Apache-2.0
"""The explicit beta command allowlist.

TokenPak's parser exposes markedly more verbs than its command registry
documents. Every one of them is reachable, so a beta tester could discover
and run a command that has never been exercised end-to-end — and if it
printed a number, they would have no way to know it was not measured.

This module names the supported set explicitly and requires a reason for
every exclusion. It is an allowlist, not a blocklist: a verb added to the
parser without a classification fails the test that pairs this file against
the live parser, so growth in the surface cannot happen silently.

Excluded commands are **not removed**. They still parse, still run, and
still appear under ``tokenpak help --all`` with their status shown. What
they lose is a place in *default* discovery, so what TokenPak advertises is
what TokenPak has verified.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_MANIFEST_PATH = Path(__file__).with_name("beta_surface.json")

#: Classification results.
SUPPORTED = "supported"
EXCLUDED = "excluded"
UNCLASSIFIED = "unclassified"


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    """Load the manifest. A missing or malformed file is a hard error.

    Failing open here would silently restore the behaviour this module
    exists to remove — an unbounded advertised surface.
    """
    with open(_MANIFEST_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "supported" not in data or "excluded" not in data:
        raise ValueError(f"{_MANIFEST_PATH} is not a valid beta surface manifest")
    return data


def supported_commands() -> frozenset[str]:
    """Commands TokenPak supports during beta."""
    return frozenset(_manifest()["supported"])


def excluded_commands() -> dict[str, str]:
    """Excluded commands mapped to the reason they are excluded."""
    return dict(_manifest()["excluded"])


def policy() -> str:
    """The written policy the allowlist encodes."""
    return str(_manifest().get("policy", ""))


def classify(command: str) -> str:
    """Return :data:`SUPPORTED`, :data:`EXCLUDED` or :data:`UNCLASSIFIED`."""
    if command in supported_commands():
        return SUPPORTED
    if command in excluded_commands():
        return EXCLUDED
    return UNCLASSIFIED


def is_supported(command: str) -> bool:
    """Whether *command* is on the beta allowlist."""
    return command in supported_commands()


def exclusion_reason(command: str) -> Optional[str]:
    """Why *command* is excluded, or ``None`` if it is not excluded."""
    return excluded_commands().get(command)


def filter_supported(commands: list[str]) -> list[str]:
    """Keep only allowlisted commands, preserving order."""
    allowed = supported_commands()
    return [c for c in commands if c in allowed]


__all__ = [
    "EXCLUDED",
    "SUPPORTED",
    "UNCLASSIFIED",
    "classify",
    "exclusion_reason",
    "excluded_commands",
    "filter_supported",
    "is_supported",
    "policy",
    "supported_commands",
]
