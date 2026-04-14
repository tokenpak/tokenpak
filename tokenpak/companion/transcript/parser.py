# SPDX-License-Identifier: Apache-2.0
"""Parse Claude Code transcript JSONL into structured conversation data.

Transcript format (observed via probe 2026-04-13):
    Each line is a JSON object with a ``type`` field:
    - ``queue-operation``  — enqueue/dequeue markers
    - ``user``             — user messages
    - ``assistant``        — Claude responses (may contain tool_use blocks)
    - ``attachment``       — system prompts, injected context (CLAUDE.md, etc.)
    - ``ai-title``         — auto-generated session title
    - ``last-prompt``      — most recent prompt snapshot
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class TranscriptMessage:
    """Single message from the transcript."""

    type: str
    role: str = ""
    content: str = ""
    tokens_est: int = 0
    timestamp: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptSummary:
    """Aggregated summary of a transcript file."""

    path: str
    message_count: int = 0
    total_chars: int = 0
    tokens_est: int = 0
    role_counts: dict[str, int] = field(default_factory=dict)
    messages: list[TranscriptMessage] = field(default_factory=list)
    file_size_bytes: int = 0
    parse_errors: int = 0


def parse_transcript(path: str | Path) -> TranscriptSummary:
    """Parse a transcript JSONL file into a structured summary.

    Args:
        path: Path to the ``.jsonl`` transcript file.

    Returns:
        TranscriptSummary with messages, token estimates, and role breakdown.
    """
    p = Path(path)
    summary = TranscriptSummary(path=str(p))

    if not p.exists():
        return summary

    summary.file_size_bytes = p.stat().st_size
    messages: list[TranscriptMessage] = []
    role_counts: dict[str, int] = {}

    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            summary.parse_errors += 1
            continue

        msg_type = obj.get("type", "unknown")
        role_counts[msg_type] = role_counts.get(msg_type, 0) + 1

        content = _extract_content(obj)
        char_count = len(content)

        msg = TranscriptMessage(
            type=msg_type,
            role=obj.get("role", msg_type),
            content=content,
            tokens_est=char_count // 4,
            timestamp=obj.get("timestamp", ""),
            raw=obj,
        )
        messages.append(msg)
        summary.total_chars += char_count

    summary.messages = messages
    summary.message_count = len(messages)
    summary.tokens_est = summary.total_chars // 4
    summary.role_counts = role_counts
    return summary


def _extract_content(obj: dict[str, Any]) -> str:
    """Best-effort content extraction from a transcript JSON line."""
    # Try common content fields
    for key in ("content", "message", "text"):
        val = obj.get(key)
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "\n".join(parts)
    # Fallback: serialize the whole object
    return json.dumps(obj)


def find_live_transcript(
    project_dir: str = "",
    session_id: str = "",
) -> Optional[str]:
    """Find the transcript JSONL for the current or most recent session.

    Args:
        project_dir: Claude Code project directory (e.g. working directory).
        session_id: If known, find the exact session file.

    Returns:
        Path to the transcript file, or None.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None

    # If session_id is known, look for it directly
    if session_id:
        for project_path in claude_dir.iterdir():
            if not project_path.is_dir():
                continue
            candidate = project_path / f"{session_id}.jsonl"
            if candidate.exists():
                return str(candidate)

    # Otherwise find the most recently modified .jsonl
    candidates: list[tuple[float, Path]] = []
    for project_path in claude_dir.iterdir():
        if not project_path.is_dir():
            continue
        for jsonl in project_path.glob("*.jsonl"):
            candidates.append((jsonl.stat().st_mtime, jsonl))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return str(candidates[0][1])
