# SPDX-License-Identifier: Apache-2.0
"""Shared output-style directive for both companion hosts.

The Claude companion appends this to its system prompt; the Codex companion
renders it into the managed ``AGENTS.md`` section.  Both read the same constant
so the two hosts cannot drift apart.

Disabled by default so unchanged launches preserve the host's native response
style.  ``TOKENPAK_COMPANION_STYLE=lean`` explicitly opts into the directive.
"""

from __future__ import annotations

import os

LEAN = "lean"
STANDARD = "standard"

DEFAULT = STANDARD

_ENV_VAR = "TOKENPAK_COMPANION_STYLE"

# Kept deliberately short: this text is appended to a cached system-prompt
# surface on every session, so its own token cost is paid repeatedly.
_DIRECTIVE = """
## Output style

Write dense technical markdown.  Maximize information per token.

- Lead with the answer, result, or finding.  No preamble, no restatement of the
  question, no "Background" section ahead of the first useful line.
- Prefer bold headers, imperative steps, tables, and fenced code over prose.
- Show the command, path, or real output instead of describing it.
- Cut hedging, filler, and closing recaps.  Keep the evidence: `file:line`,
  commands, versions, measured numbers.
- Brevity is about prose, not substance.  Never drop a required section, a
  frontmatter field, or a verifiable acceptance criterion to save words.

Use fuller prose where the task calls for it: persuasive or marketing copy,
user-facing documentation, release notes, commit messages, and explanations
written for a non-technical reader.

Task instructions and loaded skills override this default.
"""


def resolve(value: str | None = None) -> str:
    """Resolve the effective style, falling back to the environment.

    Unknown values resolve to :data:`DEFAULT` rather than raising — an
    unparseable style must never be able to block a session launch.
    """
    raw = value if value is not None else os.environ.get(_ENV_VAR)
    if raw is None:
        return DEFAULT
    normalized = raw.strip().lower()
    return normalized if normalized in (LEAN, STANDARD) else DEFAULT


def directive(style: str | None = None) -> str:
    """Return the output-style block, or ``""`` when the style is not lean."""
    return _DIRECTIVE if resolve(style) == LEAN else ""
