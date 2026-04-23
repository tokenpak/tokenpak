"""Capsule builder: heuristic extraction from Claude Code transcripts.

A SessionCapsule compresses a long conversation (100k+ tokens) down to a
dense markdown summary (< 2k tokens) by extracting:

- **Artifacts**: file paths mentioned or modified during the session
- **Decisions**: language indicating a choice was made ("decided", "chose", etc.)
- **Action items**: forward-looking tasks ("TODO", "need to", "should", etc.)
- **Insights**: discoveries and findings ("found", "turns out", etc.)
- **Context summary**: first 2 and last 2 assistant turns (bookend the work)

This is v1 (heuristic). LLM-assisted extraction is a v2 feature.

Usage::

    from tokenpak.companion.capsules.builder import CapsuleBuilder

    builder = CapsuleBuilder()
    capsule = builder.build_from_messages(messages)
    print(capsule.to_markdown())
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Default capsule directory
# ---------------------------------------------------------------------------

_DEFAULT_CAPSULE_DIR = Path.home() / ".tokenpak" / "companion" / "capsules"


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# File paths: absolute (/foo/bar.py) or relative with extension (src/foo.py)
_RE_FILE_PATH = re.compile(
    r"""
    (?:
        /(?:[a-zA-Z0-9_\-\.]+/)*[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]{1,10}  # absolute
        |
        (?:[a-zA-Z0-9_\-\.]+/)+[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]{1,10}    # relative with dir
    )
    """,
    re.VERBOSE,
)

# Decision language — look for these at word boundaries
_RE_DECISION = re.compile(
    r"(?:^|[.!?\n]\s*)([^.!?\n]*\b(?:decided|chose|choosing|going\s+with|going\s+to\s+use|"
    r"will\s+use|opted\s+for|selected|picked|settled\s+on|let(?:'s|\s+us)\s+(?:use|go\s+with|"
    r"keep|stick))[^.!?\n]{0,200})",
    re.IGNORECASE | re.MULTILINE,
)

# Action items
_RE_ACTION = re.compile(
    r"(?:^|[.!?\n]\s*)([^.!?\n]*\b(?:TODO|FIXME|need\s+to|should|must|have\s+to|"
    r"next\s+step|follow[-\s]?up|will\s+need\s+to|don't\s+forget)[^.!?\n]{0,200})",
    re.IGNORECASE | re.MULTILINE,
)

# Insights / discoveries
_RE_INSIGHT = re.compile(
    r"(?:^|[.!?\n]\s*)([^.!?\n]*\b(?:found\s+that|discovered|turns\s+out|actually|"
    r"it\s+turns\s+out|realized|noticed|the\s+reason\s+(?:is|was)|root\s+cause|"
    r"the\s+issue\s+(?:is|was))[^.!?\n]{0,200})",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SessionCapsule:
    """Compressed representation of a Claude Code session."""

    session_id: str = ""
    artifacts: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    context_summary: List[Tuple[str, str]] = field(default_factory=list)
    """List of (role, snippet) pairs from the first/last turns."""

    message_count: int = 0
    tokens_est: int = 0

    def to_markdown(self) -> str:
        """Render the capsule as compact markdown (target: < 2 k tokens).

        Deduplicates all lists and truncates each item to keep the output
        within budget.
        """
        lines: List[str] = []

        header = "## Session Capsule"
        if self.session_id:
            header += f" — `{self.session_id[:16]}`"
        lines.append(header)
        lines.append(
            f"*{self.message_count} messages · ~{self.tokens_est:,} tokens compressed*"
        )
        lines.append("")

        if self.context_summary:
            lines.append("### Context")
            for role, snippet in self.context_summary:
                prefix = "U:" if role == "user" else "A:"
                lines.append(f"> **{prefix}** {_truncate(snippet, 120)}")
            lines.append("")

        if self.artifacts:
            lines.append("### Artifacts")
            for a in _dedup(self.artifacts)[:20]:
                lines.append(f"- `{_truncate(a, 80)}`")
            lines.append("")

        if self.decisions:
            lines.append("### Decisions")
            for d in _dedup(self.decisions)[:10]:
                lines.append(f"- {_truncate(d, 120)}")
            lines.append("")

        if self.action_items:
            lines.append("### Action Items")
            for ai in _dedup(self.action_items)[:10]:
                lines.append(f"- {_truncate(ai, 120)}")
            lines.append("")

        if self.insights:
            lines.append("### Insights")
            for ins in _dedup(self.insights)[:10]:
                lines.append(f"- {_truncate(ins, 120)}")
            lines.append("")

        return "\n".join(lines)

    def token_count(self) -> int:
        """Rough token estimate of `to_markdown()` output (chars / 4)."""
        return len(self.to_markdown()) // 4


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class CapsuleBuilder:
    """Heuristic capsule builder for Claude Code transcripts.

    Accepts raw messages (dicts as they appear in a JSONL transcript) or
    plain text snippets, and returns a :class:`SessionCapsule`.

    Args:
        context_turns: How many assistant turns to capture from the start
            and end of the session for the ``context_summary``.
    """

    def __init__(self, context_turns: int = 2) -> None:
        self.context_turns = context_turns

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_from_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        session_id: str = "",
    ) -> SessionCapsule:
        """Build a capsule from a list of transcript message dicts.

        Each dict should have at least a ``type`` field (``user``,
        ``assistant``, etc.) and a ``message`` or ``content`` field.
        """
        capsule = SessionCapsule(session_id=session_id)
        capsule.message_count = len(messages)

        assistant_texts: List[str] = []
        all_texts: List[str] = []

        for msg in messages:
            text = _extract_text(msg)
            if not text:
                continue
            all_texts.append(text)
            role = _get_role(msg)
            if role == "assistant":
                assistant_texts.append(text)

        # Token estimate (heuristic; use tiktoken if available)
        capsule.tokens_est = _count_tokens_total(all_texts)

        # Context summary: first N + last N assistant turns
        capsule.context_summary = _build_context_summary(
            messages, n=self.context_turns
        )

        # Heuristic extraction from all assistant text
        combined = "\n".join(assistant_texts)
        self._extract_heuristic(combined, capsule)

        return capsule

    def build_from_jsonl(
        self,
        path: Path,
        session_id: str = "",
    ) -> SessionCapsule:
        """Build a capsule directly from a JSONL transcript file."""
        messages: List[Dict[str, Any]] = []
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        messages.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
        if not session_id and messages:
            session_id = messages[0].get("sessionId", "")
        return self.build_from_messages(messages, session_id=session_id)

    # ------------------------------------------------------------------
    # Heuristic extraction (core)
    # ------------------------------------------------------------------

    def _extract_heuristic(
        self,
        text: str,
        capsule: SessionCapsule,
    ) -> None:
        """Extract artifacts, decisions, action items, and insights from *text*.

        Populates the corresponding lists on *capsule* in-place.
        Results are deduplicated and normalised (stripped, title-normalised
        for file paths).

        Args:
            text: Combined assistant message text for the session.
            capsule: The capsule to populate.
        """
        # ── Artifacts (file paths) ──────────────────────────────────────
        paths: List[str] = []
        for m in _RE_FILE_PATH.finditer(text):
            p = m.group(0).strip()
            # Filter noise: skip very short paths and obvious non-paths
            if len(p) >= 4 and not p.startswith(".."):
                paths.append(p)
        capsule.artifacts = _dedup(paths)

        # ── Decisions ──────────────────────────────────────────────────
        decisions: List[str] = []
        for m in _RE_DECISION.finditer(text):
            snippet = _clean(m.group(1))
            if len(snippet) > 8:
                decisions.append(snippet)
        capsule.decisions = _dedup(decisions)

        # ── Action items ───────────────────────────────────────────────
        actions: List[str] = []
        for m in _RE_ACTION.finditer(text):
            snippet = _clean(m.group(1))
            if len(snippet) > 8:
                actions.append(snippet)
        capsule.action_items = _dedup(actions)

        # ── Insights ───────────────────────────────────────────────────
        insights: List[str] = []
        for m in _RE_INSIGHT.finditer(text):
            snippet = _clean(m.group(1))
            if len(snippet) > 8:
                insights.append(snippet)
        capsule.insights = _dedup(insights)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _safe_filename(session_id: str) -> str:
    """Convert a session_id to a safe filename stem (no path separators)."""
    return re.sub(r"[^\w\-]", "_", session_id)[:64]


def save_capsule(
    capsule: "SessionCapsule",
    capsule_dir: Optional[Path] = None,
) -> Path:
    """Save *capsule* as a markdown file to *capsule_dir*.

    The filename is derived from ``capsule.session_id`` when set, otherwise a
    timestamp-based name is used (``capsule_<unix_ts>.md``).

    Args:
        capsule: The :class:`SessionCapsule` to persist.
        capsule_dir: Directory to write the file.  Defaults to
            ``~/.tokenpak/companion/capsules/``.  Created if absent.

    Returns:
        The :class:`Path` of the written file.
    """
    if capsule_dir is None:
        capsule_dir = _DEFAULT_CAPSULE_DIR
    capsule_dir = Path(capsule_dir)
    capsule_dir.mkdir(parents=True, exist_ok=True)

    if capsule.session_id:
        stem = _safe_filename(capsule.session_id)
    else:
        stem = f"capsule_{int(time.time())}"

    path = capsule_dir / f"{stem}.md"
    path.write_text(capsule.to_markdown(), encoding="utf-8")
    return path


def load_capsule(
    session_id: Optional[str] = None,
    capsule_dir: Optional[Path] = None,
) -> str:
    """List available capsules or load one by *session_id*.

    * **No session_id** — returns a newline-joined list of available session IDs
      (file stems).  Returns an empty string when the directory is missing or empty.
    * **With session_id** — returns the full markdown content of that capsule.
      Raises :class:`FileNotFoundError` when the capsule does not exist.

    Args:
        session_id: The session to load, or ``None``/``""`` to list all.
        capsule_dir: Directory to read from.  Defaults to
            ``~/.tokenpak/companion/capsules/``.

    Returns:
        Listing string or capsule markdown content.
    """
    if capsule_dir is None:
        capsule_dir = _DEFAULT_CAPSULE_DIR
    capsule_dir = Path(capsule_dir)

    if not session_id:
        if not capsule_dir.exists():
            return ""
        files = sorted(capsule_dir.glob("*.md"))
        return "\n".join(f.stem for f in files)

    stem = _safe_filename(session_id)
    path = capsule_dir / f"{stem}.md"
    if not path.exists():
        raise FileNotFoundError(f"Capsule not found for session_id={session_id!r} (looked at {path})")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(msg: Dict[str, Any]) -> str:
    """Pull a plain-text string out of a transcript message dict."""
    msg_type = msg.get("type", "")

    # assistant / user: message.content is a list of blocks or a string
    if msg_type in ("assistant", "user"):
        inner = msg.get("message", msg)
        content = inner.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        inp = block.get("input", {})
                        parts.append(json.dumps(inp) if isinstance(inp, dict) else str(inp))
                    elif block.get("type") == "tool_result":
                        rc = block.get("content", "")
                        parts.append(rc if isinstance(rc, str) else json.dumps(rc))
            return " ".join(parts)
        return ""

    # queue-operation has a top-level content field
    if msg_type == "queue-operation":
        return str(msg.get("content", ""))

    # Fallback: content / text / data
    for key in ("content", "text", "data"):
        val = msg.get(key)
        if val and isinstance(val, str):
            return val
    return ""


def _get_role(msg: Dict[str, Any]) -> str:
    """Return 'user', 'assistant', or the message type."""
    msg_type = msg.get("type", "")
    if msg_type in ("user", "assistant"):
        return msg_type
    inner = msg.get("message", {})
    if isinstance(inner, dict):
        role = inner.get("role", "")
        if role:
            return role
    return msg_type


def _build_context_summary(
    messages: Sequence[Dict[str, Any]],
    n: int = 2,
) -> List[Tuple[str, str]]:
    """Return the first *n* and last *n* non-empty turns as (role, snippet) pairs."""
    turns: List[Tuple[str, str]] = []
    for msg in messages:
        text = _extract_text(msg)
        if not text:
            continue
        role = _get_role(msg)
        if role in ("user", "assistant"):
            turns.append((role, text))

    if not turns:
        return []

    result: List[Tuple[str, str]] = []
    seen: set = set()

    # First n turns
    for t in turns[:n]:
        key = t[1][:40]
        if key not in seen:
            seen.add(key)
            result.append(t)

    # Last n turns (skip if already included)
    for t in turns[-n:]:
        key = t[1][:40]
        if key not in seen:
            seen.add(key)
            result.append(t)

    return result


def _count_tokens_total(texts: List[str]) -> int:
    """Estimate total token count for a list of texts.

    Uses tiktoken if available, falls back to chars/4.
    """
    combined = " ".join(texts)
    try:
        from tokenpak.telemetry.tokens import count_tokens  # type: ignore
        return count_tokens(combined)
    except Exception:
        return len(combined) // 4


def _dedup(items: List[str]) -> List[str]:
    """Deduplicate while preserving order."""
    seen: set = set()
    result: List[str] = []
    for item in items:
        key = item.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _clean(s: str) -> str:
    """Strip leading punctuation/whitespace from an extracted snippet."""
    return re.sub(r"^[\s.!?,;:]+", "", s).strip()


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string with an ellipsis if it exceeds *max_len*."""
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"
