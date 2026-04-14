# SPDX-License-Identifier: Apache-2.0
"""Parse Claude Code transcript JSONL into structured conversation data.

Transcript format (observed via probe 2026-04-13 + real file sampling 2026-04-14):
    Each line is a JSON object with a ``type`` field:
    - ``queue-operation``  — enqueue/dequeue markers with prompt text in ``content``
    - ``user``             — user messages; content in ``message.content`` (str or blocks)
    - ``assistant``        — Claude responses; content in ``message.content`` (list of blocks)
    - ``attachment``       — injected context (CLAUDE.md, deferred tools, system prompts)
    - ``ai-title``         — auto-generated session title
    - ``last-prompt``      — most recent prompt snapshot in ``lastPrompt``
    - ``progress``         — sub-agent progress events (parsed but minimal content)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from tokenpak.telemetry.tokens import count_tokens
except ImportError:
    # Fallback if tokens module unavailable — heuristic only
    def count_tokens(text: str) -> int:  # type: ignore[misc]
        return max(1, len(text) // 4) if text else 0


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
    total_tokens = 0

    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
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
        tool_calls = _extract_tool_calls(obj)
        tokens = count_tokens(content) if content else 0
        total_tokens += tokens

        msg = TranscriptMessage(
            type=msg_type,
            role=_extract_role(obj),
            content=content,
            tokens_est=tokens,
            timestamp=obj.get("timestamp", ""),
            tool_calls=tool_calls,
            raw=obj,
        )
        messages.append(msg)
        summary.total_chars += len(content)

    summary.messages = messages
    summary.message_count = len(messages)
    summary.tokens_est = total_tokens
    summary.role_counts = role_counts
    return summary


def _extract_role(obj: dict[str, Any]) -> str:
    """Determine the role for a transcript line."""
    msg_type = obj.get("type", "")
    if msg_type == "user":
        msg = obj.get("message", {})
        return msg.get("role", "user") if isinstance(msg, dict) else "user"
    if msg_type == "assistant":
        msg = obj.get("message", {})
        return msg.get("role", "assistant") if isinstance(msg, dict) else "assistant"
    return msg_type


def _extract_content(obj: dict[str, Any]) -> str:
    """Extract human-readable content text from any transcript message type."""
    msg_type = obj.get("type", "")

    # queue-operation: prompt text is in top-level ``content``
    if msg_type == "queue-operation":
        return str(obj.get("content", ""))

    # user / assistant: content lives inside the nested ``message`` dict
    if msg_type in ("user", "assistant"):
        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            return ""
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return _flatten_content_blocks(content)
        return ""

    # attachment: injected context — CLAUDE.md, deferred tools, etc.
    if msg_type == "attachment":
        att = obj.get("attachment", {})
        if not isinstance(att, dict):
            return str(att)
        # Some attachments have a direct text payload
        for key in ("content", "text"):
            val = att.get(key)
            if isinstance(val, str) and val:
                return val
        # deferred_tools_delta and similar: list of names/lines
        added = att.get("addedLines") or att.get("addedNames") or []
        if added:
            return "\n".join(str(a) for a in added)
        # Fallback: JSON-encode the attachment dict (still useful for token est)
        return json.dumps(att, ensure_ascii=False)

    # last-prompt: snapshot of the most recent user prompt
    if msg_type == "last-prompt":
        return str(obj.get("lastPrompt", ""))

    # ai-title: session title generated by Claude
    if msg_type == "ai-title":
        return str(obj.get("title", obj.get("content", "")))

    # progress and any unknown types: try common text fields
    for key in ("content", "text", "data"):
        val = obj.get(key)
        if isinstance(val, str) and val:
            return val

    return ""


def _flatten_content_blocks(blocks: list[Any]) -> str:
    """Flatten a list of content blocks (text, tool_use, thinking, tool_result, …) into text."""
    parts: list[str] = []
    for item in blocks:
        if not isinstance(item, dict):
            if isinstance(item, str):
                parts.append(item)
            continue
        block_type = item.get("type", "")
        if block_type == "text":
            text = item.get("text", "")
            if text:
                parts.append(text)
        elif block_type == "thinking":
            thinking = item.get("thinking", "")
            if thinking:
                parts.append(thinking)
        elif block_type == "tool_use":
            name = item.get("name", "")
            inp = item.get("input", {})
            # Serialise input so token count reflects the actual bytes sent to the API
            parts.append(f"[tool_use:{name}] {json.dumps(inp, ensure_ascii=False)}")
        elif block_type == "tool_result":
            result_content = item.get("content", "")
            if isinstance(result_content, list):
                result_content = "\n".join(
                    r.get("text", "") for r in result_content if isinstance(r, dict)
                )
            if result_content:
                parts.append(str(result_content))
        elif block_type == "image":
            # Images contribute tokens but we can only estimate; record placeholder
            parts.append("[image]")
        else:
            # Unknown block type: try text field, else skip
            fallback = item.get("text", item.get("content", ""))
            if isinstance(fallback, str) and fallback:
                parts.append(fallback)
    return "\n".join(p for p in parts if p)


def _extract_tool_calls(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Return list of tool_use blocks from an assistant message, else []."""
    if obj.get("type") != "assistant":
        return []
    msg = obj.get("message", {})
    if not isinstance(msg, dict):
        return []
    content = msg.get("content", [])
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def find_live_transcript(
    project_dir: str = "",
    session_id: str = "",
) -> Optional[str]:
    """Find the transcript JSONL for the current or most recent session.

    Args:
        project_dir: Claude Code project directory (e.g. working directory).
                     When provided, only projects matching that path are searched.
        session_id: If known, find the exact session file by UUID.

    Returns:
        Absolute path to the transcript ``.jsonl`` file, or ``None`` if not found.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None

    # If session_id is known, search for it directly across all project dirs
    if session_id:
        for project_path in claude_dir.iterdir():
            if not project_path.is_dir():
                continue
            candidate = project_path / f"{session_id}.jsonl"
            if candidate.exists():
                return str(candidate)

    # Narrow to the project dir matching the given working directory
    search_dirs: list[Path] = []
    if project_dir:
        # Claude Code encodes the project path as the directory name
        # (replaces '/' with '-'), so convert to find the right dir
        encoded = project_dir.lstrip("/").replace("/", "-")
        for d in claude_dir.iterdir():
            if d.is_dir() and encoded in d.name:
                search_dirs.append(d)

    if not search_dirs:
        search_dirs = [d for d in claude_dir.iterdir() if d.is_dir()]

    # Find the most recently modified top-level .jsonl (skip subagent dirs)
    candidates: list[tuple[float, Path]] = []
    for project_path in search_dirs:
        for jsonl in project_path.glob("*.jsonl"):
            candidates.append((jsonl.stat().st_mtime, jsonl))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return str(candidates[0][1])
