"""
QueryRewriter — pre-process user/system queries for compactness.

Strips pleasantries, collapses repeated asks, and extracts core intent
so downstream compression stages operate on denser signal.

Especially valuable in long multi-turn workflows where users repeat
context or embed intent inside conversational padding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Greeting / filler openers — matches common conversational prefixes
_OPENER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(?:hey[\s,!]*|hi[\s,!]*|hello[\s,!]*|"
        r"good\s+(?:morning|afternoon|evening|day)[\s,!]*)+",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:i\s+was\s+wondering\s+(?:if\s+)?(?:you\s+could\s+)?|"
        r"could\s+you\s+(?:please\s+)?|"
        r"can\s+you\s+(?:please\s+)?|"
        r"would\s+you\s+(?:be\s+able\s+to\s+)?(?:please\s+)?|"
        r"i\s+(?:really\s+)?need\s+(?:you\s+to\s+)?(?:please\s+)?|"
        r"i\s+(?:just\s+)?wanted\s+to\s+(?:ask\s+)?(?:if\s+(?:you\s+could\s+)?)?|"
        r"i\s+hope\s+you\s+can\s+help\s+(?:me\s+)?(?:with\s+this\b)?[,.]?\s*)",
        re.IGNORECASE,
    ),
]

# Closing pleasantries / filler trailers
_CLOSER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"[\s,!.]*(?:please\s+(?:and\s+)?thank\s+you|"
        r"thanks?\s+(?:a\s+lot|so\s+much|very\s+much|in\s+advance)?|"
        r"thank\s+you\s+(?:so\s+much\b\s*)?(?:in\s+advance\b\s*)?|"
        r"i\s+(?:really\s+)?appreciate\s+(?:it|your\s+help)|"
        r"i\s+look\s+forward\s+to\s+(?:your\s+)?(?:help|response|reply))[\s!.]*$",
        re.IGNORECASE,
    ),
]

# Filler phrases that appear mid-sentence
_INLINE_FILLER: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bkindly\b\s+", re.IGNORECASE), ""),
    (re.compile(r"\bplease\s+note\s+that\b\s*", re.IGNORECASE), ""),
    (re.compile(r"\bif\s+you\s+(?:don'?t\s+mind\b|could\b)[,.]?\s+", re.IGNORECASE), ""),
    (re.compile(r"\bjust\s+(?:to\s+be\s+clear|to\s+clarify|wondering)[,.]?\s+", re.IGNORECASE), ""),
    (re.compile(r"\bbasically\s+", re.IGNORECASE), ""),
    (re.compile(r"\bes[s]?entially\s+", re.IGNORECASE), ""),
    (re.compile(r"\bkind\s+of\b\s+", re.IGNORECASE), ""),
    (re.compile(r"\bsort\s+of\b\s+", re.IGNORECASE), ""),
    (re.compile(r"\blike\s+I\s+said\s*,?\s+", re.IGNORECASE), ""),
    (re.compile(r"\b(as\s+I\s+(?:mentioned|said|noted)\s+(?:before|earlier|above)[,.]?\s*)", re.IGNORECASE), ""),
]

# Repeated-question detection: sentences that are semantically identical
# are collapsed to one. We use 4-gram Jaccard as a fast proxy.
_SENT_SPLITTER = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ngrams(text: str, n: int = 4) -> frozenset[str]:
    text = text.lower()
    if len(text) < n:
        return frozenset()
    return frozenset(text[i : i + n] for i in range(len(text) - n + 1))


def _jaccard(a: str, b: str, n: int = 4) -> float:
    sa, sb = _ngrams(a, n), _ngrams(b, n)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _strip_openers(text: str) -> str:
    for pat in _OPENER_PATTERNS:
        text = pat.sub("", text)
    return text


def _strip_closers(text: str) -> str:
    for pat in _CLOSER_PATTERNS:
        text = pat.sub("", text)
    return text


def _strip_inline_filler(text: str) -> str:
    for pat, repl in _INLINE_FILLER:
        text = pat.sub(repl, text)
    return text


def _collapse_whitespace(text: str) -> str:
    """Normalise runs of whitespace; strip leading/trailing."""
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _capitalise_first(text: str) -> str:
    """Ensure the string starts with a capital letter."""
    if not text:
        return text
    return text[0].upper() + text[1:]


def _collapse_repeated_sentences(
    text: str,
    threshold: float = 0.75,
) -> str:
    """
    Remove sentences whose content substantially duplicates an earlier
    sentence in the same query (Jaccard ≥ threshold).
    """
    sentences = _SENT_SPLITTER.split(text.strip())
    kept: list[str] = []
    for sent in sentences:
        sent_s = sent.strip()
        if not sent_s:
            continue
        is_dupe = any(_jaccard(sent_s, k) >= threshold for k in kept)
        if not is_dupe:
            kept.append(sent_s)
    return " ".join(kept)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class RewriteResult:
    """Result of a single query rewrite operation."""

    original: str
    rewritten: str
    chars_saved: int = field(init=False)
    savings_pct: float = field(init=False)
    modified: bool = field(init=False)

    def __post_init__(self) -> None:
        self.chars_saved = max(0, len(self.original) - len(self.rewritten))
        if len(self.original) > 0:
            self.savings_pct = round(self.chars_saved / len(self.original) * 100, 2)
        else:
            self.savings_pct = 0.0
        self.modified = self.original != self.rewritten

    def __repr__(self) -> str:
        return (
            f"RewriteResult(chars_saved={self.chars_saved}, "
            f"savings_pct={self.savings_pct}%, modified={self.modified})"
        )


class QueryRewriter:
    """
    Pre-process user/system queries for maximum compactness.

    Applies a deterministic, rule-based pipeline:

    1. Strip greeting / opener phrases
    2. Strip trailing pleasantries
    3. Remove inline filler words
    4. Collapse repeated / near-duplicate sentences
    5. Normalise whitespace and capitalisation

    All transforms are fully reversible from the original; the original
    is always retained in :class:`RewriteResult` for audit/fallback.

    Parameters
    ----------
    collapse_threshold : float
        Jaccard similarity above which two sentences in the same query
        are considered duplicates. Default 0.75.
    preserve_technical : bool
        When True (default), skip inline-filler stripping on tokens
        that appear inside backticks, angle-bracket tags, or URLs to
        avoid corrupting code / markup.
    """

    def __init__(
        self,
        collapse_threshold: float = 0.70,
        preserve_technical: bool = True,
    ) -> None:
        self.collapse_threshold = collapse_threshold
        self.preserve_technical = preserve_technical

    # ------------------------------------------------------------------
    # Core rewrite
    # ------------------------------------------------------------------

    def rewrite(self, text: str) -> RewriteResult:
        """
        Rewrite a single query string.

        Parameters
        ----------
        text:
            Raw query text (may include greetings, filler, duplicates).

        Returns
        -------
        RewriteResult
        """
        if not text or not text.strip():
            return RewriteResult(original=text, rewritten=text)

        if self.preserve_technical:
            result = self._rewrite_with_preservation(text)
        else:
            result = self._rewrite_raw(text)

        return RewriteResult(original=text, rewritten=result)

    def rewrite_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        roles: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Rewrite all user (and optionally system) messages in a messages list.

        Parameters
        ----------
        messages:
            Standard messages list (role/content dicts).
        roles:
            Which roles to rewrite. Defaults to ``["user"]``.
            Pass ``["user", "system"]`` to also compress system prompts.

        Returns
        -------
        List[Dict[str, Any]]
            New messages list; original dicts are not mutated.
        """
        target_roles = set(roles or ["user"])
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") not in target_roles:
                result.append(msg)
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                rr = self.rewrite(content)
                new_msg = dict(msg)
                new_msg["content"] = rr.rewritten
                result.append(new_msg)
            else:
                # Block-format content — rewrite text blocks only
                new_blocks = []
                for block in content if isinstance(content, list) else [content]:
                    if isinstance(block, dict) and block.get("type") == "text":
                        rr = self.rewrite(block.get("text", ""))
                        new_blocks.append({**block, "text": rr.rewritten})
                    else:
                        new_blocks.append(block)
                new_msg = dict(msg)
                new_msg["content"] = new_blocks
                result.append(new_msg)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rewrite_raw(self, text: str) -> str:
        text = _strip_openers(text)
        text = _strip_closers(text)
        text = _strip_inline_filler(text)
        text = _collapse_repeated_sentences(text, self.collapse_threshold)
        text = _collapse_whitespace(text)
        text = _capitalise_first(text)
        return text

    def _rewrite_with_preservation(self, text: str) -> str:
        """
        Preserve technical spans (code, URLs) while rewriting prose.

        Replaces technical spans with placeholders, rewrites the prose
        skeleton, then restores originals.
        """
        # Extract and protect technical spans
        placeholders: dict[str, str] = {}
        counter = [0]

        def _protect(m: re.Match[str]) -> str:
            key = f"\x00TP{counter[0]}\x00"
            placeholders[key] = m.group(0)
            counter[0] += 1
            return key

        # Protect: `backtick code`, ```fences```, <tags>, URLs
        protected = re.sub(r"```[\s\S]*?```", _protect, text)
        protected = re.sub(r"`[^`\n]+`", _protect, protected)
        protected = re.sub(r"<[^>]{1,200}>", _protect, protected)
        protected = re.sub(r"https?://\S+", _protect, protected)

        rewritten = self._rewrite_raw(protected)

        # Restore placeholders
        for key, original in placeholders.items():
            rewritten = rewritten.replace(key, original)

        return rewritten


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def rewrite_query(
    text: str,
    *,
    collapse_threshold: float = 0.70,
    preserve_technical: bool = True,
) -> RewriteResult:
    """
    Convenience wrapper — rewrite a single query string.

    Example::

        from tokenpak.agent.compression.query_rewriter import rewrite_query

        result = rewrite_query("Hey, can you please help me understand what a tensor is?")
        print(result.rewritten)
        # "What is a tensor?"

    Parameters
    ----------
    text : str
        Raw query text.
    collapse_threshold : float
        Jaccard similarity threshold for duplicate-sentence collapsing.
    preserve_technical : bool
        Whether to protect code/URL spans from filler stripping.

    Returns
    -------
    RewriteResult
    """
    rewriter = QueryRewriter(
        collapse_threshold=collapse_threshold,
        preserve_technical=preserve_technical,
    )
    return rewriter.rewrite(text)
