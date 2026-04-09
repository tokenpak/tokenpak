"""
TokenPak Fingerprint Generator — structural analysis without content leakage.

Generates a structural fingerprint of a prompt: segment types, token counts,
language detection — but never raw text content.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────


@dataclass
class Segment:
    """A structural unit within a prompt."""

    type: str  # e.g. "system", "user", "code", "list", "prose"
    token_estimate: int
    depth: int = 0  # nesting depth (e.g. code inside a message)
    content_hash: Optional[str] = None  # SHA-256 of content (optional, for dedup)


@dataclass
class Fingerprint:
    """Structural fingerprint of a prompt — no raw content."""

    fingerprint_id: str
    schema_version: int = 1
    total_tokens: int = 0
    segment_count: int = 0
    segments: list[Segment] = field(default_factory=list)
    language: Optional[str] = None
    model_hint: Optional[str] = None  # e.g. "gpt-4", "claude-3"

    def to_dict(self) -> dict:
        return {
            "fingerprint_id": self.fingerprint_id,
            "schema_version": self.schema_version,
            "total_tokens": self.total_tokens,
            "segment_count": self.segment_count,
            "segments": [
                {
                    "type": s.type,
                    "token_estimate": s.token_estimate,
                    "depth": s.depth,
                    **({"content_hash": s.content_hash} if s.content_hash else {}),
                }
                for s in self.segments
            ],
            **({"language": self.language} if self.language else {}),
            **({"model_hint": self.model_hint} if self.model_hint else {}),
        }


# ─────────────────────────────────────────────
# Token estimation
# ─────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token (conservative)."""
    return max(1, len(text) // 4)


# ─────────────────────────────────────────────
# Segment classification
# ─────────────────────────────────────────────

_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```|`[^`]+`", re.MULTILINE)
_LIST_RE = re.compile(r"^[\s]*[-*•]\s+.+", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+.+", re.MULTILINE)
_URL_RE = re.compile(r"https?://\S+")


def _classify_text(text: str) -> str:
    """Classify a chunk of text into a structural type."""
    stripped = text.strip()
    if _CODE_FENCE_RE.match(stripped):
        return "code"
    if _HEADING_RE.match(stripped):
        return "heading"
    if len(_LIST_RE.findall(stripped)) >= 2:
        return "list"
    if len(stripped) < 60 and not stripped.endswith("."):
        return "short_instruction"
    return "prose"


def _detect_language(text: str) -> Optional[str]:
    """Very lightweight language detection — just ascii vs unicode."""
    try:
        text.encode("ascii")
        return "en"
    except UnicodeEncodeError:
        return "non-ascii"


# ─────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────


class FingerprintGenerator:
    """
    Generates a structural Fingerprint from prompt text or message lists.

    Usage:
        gen = FingerprintGenerator()
        fp = gen.generate("You are a helpful assistant.\n\nWhat is 2+2?")
        fp = gen.generate_from_messages([{"role": "system", "content": "..."}])
    """

    def __init__(self, include_hashes: bool = False, model_hint: Optional[str] = None):
        self.include_hashes = include_hashes
        self.model_hint = model_hint

    def generate(self, text: str) -> Fingerprint:
        """Generate a fingerprint from a single prompt string."""
        fp_id = str(uuid.uuid4())
        segments: list[Segment] = []

        # Split on double-newlines (paragraph-like boundaries)
        blocks = re.split(r"\n{2,}", text.strip())
        for block in blocks:
            if not block.strip():
                continue
            seg_type = _classify_text(block)
            tok = _estimate_tokens(block)
            content_hash = (
                hashlib.sha256(block.encode()).hexdigest()[:16] if self.include_hashes else None
            )
            segments.append(
                Segment(
                    type=seg_type,
                    token_estimate=tok,
                    content_hash=content_hash,
                )
            )

        total = sum(s.token_estimate for s in segments)
        lang = _detect_language(text)

        return Fingerprint(
            fingerprint_id=fp_id,
            total_tokens=total,
            segment_count=len(segments),
            segments=segments,
            language=lang,
            model_hint=self.model_hint,
        )

    def generate_from_messages(self, messages: list[dict]) -> Fingerprint:
        """
        Generate a fingerprint from an OpenAI-style messages list.
        Each message becomes typed segments (system/user/assistant).
        """
        fp_id = str(uuid.uuid4())
        segments: list[Segment] = []
        full_text = ""

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not isinstance(content, str):
                # Handle content arrays (vision, tool use, etc.)
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))

            full_text += content + "\n"
            tok = _estimate_tokens(content)

            # Code inside message gets its own sub-segment
            code_blocks = _CODE_FENCE_RE.findall(content)
            if code_blocks:
                code_toks = sum(_estimate_tokens(c) for c in code_blocks)
                prose_toks = max(0, tok - code_toks)
                if prose_toks > 0:
                    segments.append(Segment(type=role, token_estimate=prose_toks))
                for cb in code_blocks:
                    segments.append(
                        Segment(
                            type="code",
                            token_estimate=_estimate_tokens(cb),
                            depth=1,
                        )
                    )
            else:
                segments.append(Segment(type=role, token_estimate=tok))

        total = sum(s.token_estimate for s in segments)
        lang = _detect_language(full_text)

        return Fingerprint(
            fingerprint_id=fp_id,
            total_tokens=total,
            segment_count=len(segments),
            segments=segments,
            language=lang,
            model_hint=self.model_hint,
        )
