# SPDX-License-Identifier: Apache-2.0
"""Credential discovery and routing for tokenpak.

The credential subsystem is a **router**, not a vault. It discovers
credentials owned by other tools (Codex CLI, Claude CLI, OpenClaw) and
only stores credentials the user explicitly pastes via BYOK.

Each credential has exactly one refresh owner so that rotating OAuth
refresh tokens can't be consumed twice. See ``project_tokenpak_codex_three_paths.md``
for the refresh-reuse failure pattern this subsystem was built to prevent.
"""

from .model import Credential, REFRESH_EXTERNAL, REFRESH_TOKENPAK, REFRESH_NONE

__all__ = [
    "Credential",
    "REFRESH_EXTERNAL",
    "REFRESH_TOKENPAK",
    "REFRESH_NONE",
]
