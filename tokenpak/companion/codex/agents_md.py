# SPDX-License-Identifier: Apache-2.0
"""Generate and install AGENTS.md for durable TokenPak behavior in Codex.

AGENTS.md is Codex's mechanism for persistent behavioral guidance.  It's
loaded before each session and merged from global (~/.codex/AGENTS.md) to
project (<repo>/AGENTS.md) scope.

The companion installs a global AGENTS.md with rules for:
- When to load capsules
- How to use the journal
- Budget-aware behavior
- Context bloat avoidance
"""

from __future__ import annotations

from pathlib import Path


_AGENTS_CONTENT = """\
# TokenPak Companion

You have access to TokenPak companion tools via MCP. These tools help manage
cost, context, and session continuity.

## Available MCP tools

- **estimate_tokens** — Check token count before including large content.
- **check_budget** — Query remaining cost budget for this session and today.
- **load_capsule** — Load compressed context from a prior session.
- **prune_context** — Compress verbose tool output to reduce token usage.
- **journal_read** — Read notes from past sessions.
- **journal_write** — Save important decisions or milestones.
- **session_info** — Get companion status and configuration.

## When to use tools

- **Before reading large files**: call `estimate_tokens` with the file path
  to decide if the cost is worth it.
- **Before multi-step tasks**: call `check_budget` to see if there is
  headroom for the full task.
- **When resuming prior work**: call `load_capsule` to recall context from
  the previous session rather than re-reading everything.
- **After verbose tool output**: consider calling `prune_context` if the
  output exceeds ~2000 tokens and you only need the summary.
- **When making architectural decisions**: call `journal_write` to record
  the decision and rationale for future sessions.

## When NOT to load capsules

Do not load capsules automatically on every session start.  Only load when:
- The user references prior work or a previous session.
- You need context that would otherwise require re-reading many files.
- The user explicitly asks to resume.

## Budget awareness

- If `check_budget` shows less than 20% remaining, warn the user before
  starting expensive operations (large file reads, multi-step refactors).
- If budget is exceeded, the UserPromptSubmit hook will block the request.
  Do not attempt to work around budget blocks.

## Context hygiene

- Prefer targeted file reads over whole-file reads.
- Do not include full file contents in journal entries — summarize.
- When tool output is large, prune it before reasoning over it.
- Keep journal entries concise: one decision per entry, include rationale.

## Verification

- Verify before claiming completion. Run tests, check builds, confirm the
  change actually works.
- Do not claim a task is done based solely on writing code — confirm it
  compiles, passes tests, or visibly works.
"""


def generate_agents_md() -> str:
    """Return the AGENTS.md content for TokenPak companion."""
    return _AGENTS_CONTENT


def install_agents_md(target: str = "global") -> Path:
    """Write AGENTS.md to the appropriate Codex config directory.

    Args:
        target: "global" for ~/.codex/AGENTS.md, or a repo path for
                <repo>/AGENTS.md.

    Returns:
        Path to the written AGENTS.md file.

    If AGENTS.md already exists, the TokenPak section is replaced
    (identified by the ``# TokenPak Companion`` heading) while preserving
    any other content.
    """
    if target == "global":
        agents_path = Path.home() / ".codex" / "AGENTS.md"
    else:
        agents_path = Path(target) / "AGENTS.md"

    agents_path.parent.mkdir(parents=True, exist_ok=True)

    new_content = _AGENTS_CONTENT.rstrip() + "\n"

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
