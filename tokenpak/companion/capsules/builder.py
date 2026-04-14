# SPDX-License-Identifier: Apache-2.0
"""Build memory capsules from session transcripts and journal entries.

A capsule is a compressed, reusable summary of a session that can be loaded
into a future conversation.  Unlike the full transcript (which may be 100k+
tokens), a capsule is typically 500-2000 tokens — small enough to inject
without significant cost impact.

Capsule sections (weighted by importance):
    - decisions_made (3.0)  — what was decided and why
    - artifacts_created (2.5) — files written/modified
    - action_items (2.0) — what's left to do
    - insights (1.5) — surprising findings, gotchas
    - context_summary (1.0) — what the session was about

This builds on the existing ``tokenpak.capsule`` module but is specialized
for Claude Code session transcripts rather than arbitrary text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..transcript.parser import TranscriptSummary, parse_transcript


@dataclass
class Capsule:
    """A compressed session memory capsule."""

    session_id: str
    created_at: float = 0.0
    project_dir: str = ""
    model: str = ""

    context_summary: str = ""
    decisions_made: list[str] = field(default_factory=list)
    artifacts_created: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)

    total_turns: int = 0
    total_tokens_est: int = 0
    capsule_tokens_est: int = 0

    def to_markdown(self) -> str:
        """Render the capsule as a markdown block for injection."""
        lines = [f"## Session Capsule: {self.session_id[:8]}"]
        if self.context_summary:
            lines.append(f"\n**Context:** {self.context_summary}")
        if self.decisions_made:
            lines.append("\n**Decisions:**")
            for d in self.decisions_made:
                lines.append(f"- {d}")
        if self.artifacts_created:
            lines.append("\n**Artifacts:**")
            for a in self.artifacts_created:
                lines.append(f"- {a}")
        if self.action_items:
            lines.append("\n**Action items:**")
            for a in self.action_items:
                lines.append(f"- {a}")
        if self.insights:
            lines.append("\n**Insights:**")
            for i in self.insights:
                lines.append(f"- {i}")
        lines.append(f"\n_({self.total_turns} turns, ~{self.total_tokens_est:,} tokens compressed to ~{self.capsule_tokens_est:,})_")
        return "\n".join(lines)


def build_capsule_from_transcript(
    transcript_path: str,
    session_id: str = "",
    project_dir: str = "",
) -> Capsule:
    """Build a capsule from a raw transcript file.

    This is the extraction-only path — it parses the transcript and builds
    the capsule structure.  For LLM-assisted summarization (higher quality
    but costs tokens), see ``build_capsule_with_llm()``.

    Args:
        transcript_path: Path to the session's ``.jsonl`` file.
        session_id: Session identifier.
        project_dir: Working directory of the session.

    Returns:
        A Capsule with best-effort extracted sections.
    """
    import time

    summary = parse_transcript(transcript_path)

    capsule = Capsule(
        session_id=session_id or Path(transcript_path).stem,
        created_at=time.time(),
        project_dir=project_dir,
        total_turns=summary.message_count,
        total_tokens_est=summary.tokens_est,
    )

    # Extract artifacts: look for file paths in assistant messages
    # Extract decisions: look for "decided", "chose", "going with" patterns
    # This is a heuristic pass — LLM-assisted path does better
    for msg in summary.messages:
        if msg.type == "assistant":
            _extract_heuristic(msg.content, capsule)

    capsule.capsule_tokens_est = len(capsule.to_markdown()) // 4
    return capsule


def _extract_heuristic(content: str, capsule: Capsule) -> None:
    """Best-effort extraction of decisions, artifacts, and insights."""
    # Placeholder — production implementation will use regex patterns
    # for file paths, decision language, TODO markers, etc.
    # The LLM-assisted path in build_capsule_with_llm() is preferred.
    pass


def save_capsule(capsule: Capsule, capsule_dir: Path) -> str:
    """Save a capsule to disk as markdown.

    Returns:
        Path to the saved capsule file.
    """
    capsule_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{capsule.session_id[:12]}.md"
    path = capsule_dir / filename
    path.write_text(capsule.to_markdown())
    return str(path)


def load_capsule(capsule_path: str) -> Optional[str]:
    """Load a capsule's markdown content from disk.

    Returns:
        The capsule markdown, or None if not found.
    """
    p = Path(capsule_path)
    if p.exists():
        return p.read_text()
    return None
