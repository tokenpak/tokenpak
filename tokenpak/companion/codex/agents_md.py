# SPDX-License-Identifier: Apache-2.0
"""Generate and install AGENTS.md for durable TokenPak behavior in Codex.

AGENTS.md is Codex's mechanism for persistent behavioral guidance.  It's
loaded before each session and merged from global ($CODEX_HOME/AGENTS.md) to
project (<repo>/AGENTS.md) scope.

The companion installs a global AGENTS.md with rules for:
- When to load capsules
- How to use the journal
- Budget-aware behavior
- Context bloat avoidance
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import _style

_AGENTS_CONTENT = """\
# TokenPak Companion

TokenPak companion tools are available via MCP. Cost accounting is automatic
and out-of-band: a pre-send hook estimates every prompt and blocks over-budget
requests, and a stop hook records the session summary. Every MCP tool call
costs a full model round-trip that re-sends the conversation so far — never
spend one on routine accounting.

- Do NOT call `estimate_tokens`, `check_budget`, or `session_info` as
  bookkeeping during a task; the hooks already track cost. Reserve
  `estimate_tokens` for a genuine go/no-go decision on including very large
  content.
- `journal_write`: one concise entry for a major decision (no file contents);
  session summaries are captured automatically at stop.
- For prior work, retrieve before answering. Prefer available native memory;
  otherwise batch via `load_pak` (`load_capsule`); use `journal_read` only for
  targeted follow-up. Persist each fact once.
- `prune_context`: only for verbose output you must keep but do not need in
  full.
- Prefer targeted file reads over whole-file reads.
- If a prompt is blocked for budget, do not work around the block.
- Verify before claiming completion: run the tests or build and confirm the
  change actually works.
"""


def generate_agents_md(style: str | None = None) -> str:
    """Return the AGENTS.md content for TokenPak companion.

    ``style`` selects the response-style block appended to the section; it
    defaults to the ``TOKENPAK_COMPANION_STYLE`` environment value, then to
    ``standard`` (no block) when unset.  The block lands inside the managed
    section, below a ``##`` heading, so it is replaced and removed with the
    rest of the section rather than surviving as an orphan.
    """
    return _AGENTS_CONTENT + _style.directive(style)


def _selected_codex_home(codex_home: Path | None = None) -> Path:
    """Resolve the active Codex home at call time."""
    if codex_home is not None:
        return Path(codex_home).expanduser()
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def install_agents_md(target: str = "global") -> Path:
    """Write AGENTS.md using the active public Codex configuration."""
    return _install_agents_md(target)


def _install_agents_md(target: str = "global", *, codex_home: Path | None = None) -> Path:
    """Write AGENTS.md to the appropriate Codex config directory.

    Args:
        target: "global" for $CODEX_HOME/AGENTS.md, or a repo path for
                <repo>/AGENTS.md.
        codex_home: Internal explicit selected Codex home for a global install.

    Returns:
        Path to the written AGENTS.md file.

    If AGENTS.md already exists, the TokenPak section is replaced
    (identified by the ``# TokenPak Companion`` heading) while preserving
    any other content.
    """
    if target == "global":
        agents_path = _selected_codex_home(codex_home) / "AGENTS.md"
    else:
        agents_path = Path(target) / "AGENTS.md"

    agents_path.parent.mkdir(parents=True, exist_ok=True)

    new_content = generate_agents_md().rstrip() + "\n"

    if agents_path.exists():
        existing = agents_path.read_text()
        merged = _merge_agents(existing, new_content)
    else:
        merged = new_content

    agents_path.write_text(merged)
    return agents_path


def _merge_agents(existing: str, tokenpak_section: str) -> str:
    """Replace the TokenPak section in existing AGENTS.md, preserving the rest.

    The TokenPak section is identified by lines between
    ``# TokenPak Companion`` and the next top-level heading (``# ``).
    """
    marker = "# TokenPak Companion"
    if marker not in existing:
        # Append
        separator = "\n\n" if existing.rstrip() else ""
        return existing.rstrip() + separator + tokenpak_section

    # Find and replace the TokenPak section
    lines = existing.split("\n")
    before: list[str] = []
    after: list[str] = []
    in_section = False
    past_section = False

    for line in lines:
        if line.strip() == marker:
            in_section = True
            continue
        if in_section and not past_section:
            # Look for the next top-level heading
            if line.startswith("# ") and line.strip() != marker:
                past_section = True
                in_section = False
                after.append(line)
            # Skip lines in the old TokenPak section
            continue
        if past_section:
            after.append(line)
        else:
            before.append(line)

    before_text = "\n".join(before).rstrip()
    after_text = "\n".join(after).rstrip()

    parts = [before_text, tokenpak_section.rstrip()]
    if after_text:
        parts.append(after_text)

    return "\n\n".join(p for p in parts if p) + "\n"
