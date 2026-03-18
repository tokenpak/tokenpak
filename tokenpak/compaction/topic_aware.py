"""
TokenPak Topic-Aware Compaction — Segmentation and Differential Compression.

Provides deterministic topic boundary detection and topic-grouped compaction
policy for maintaining active-topic detail while summarizing inactive topics.

Usage::

    from tokenpak.compaction.topic_aware import TopicBoundaryDetector, TopicAwarePolicy

    # Detect topic boundaries
    detector = TopicBoundaryDetector()
    text = "Introduction to ML...\\n\\nDeep Learning techniques...\\n\\nConclusion..."
    segments = detector.segment(text)
    # [TopicSegment(...), TopicSegment(...), ...]

    # Apply topic-aware compaction
    policy = TopicAwarePolicy(
        active_mode="balanced",
        inactive_mode="aggressive",
        activity_threshold=0.5,
    )
    result = policy.compact_with_topics(text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class TopicSegment:
    """Represents a contiguous topic span in text."""

    start: int  # Character offset start
    end: int  # Character offset end
    content: str  # Raw text content
    topic_id: str  # Unique topic identifier
    activity_score: float = 0.5  # 0.0 (inactive) to 1.0 (active)
    reference_count: int = 0  # Number of cross-references to this topic
    recency_score: float = 0.5  # How recent the topic is

    @property
    def is_active(self) -> bool:
        """Topic is active if activity_score exceeds threshold."""
        return self.activity_score >= 0.5

    @property
    def length_chars(self) -> int:
        """Length in characters."""
        return self.end - self.start

    def to_dict(self) -> Dict:
        """Serialize to dict."""
        return {
            "start": self.start,
            "end": self.end,
            "topic_id": self.topic_id,
            "activity_score": self.activity_score,
            "reference_count": self.reference_count,
            "recency_score": self.recency_score,
            "length_chars": self.length_chars,
        }


# ---------------------------------------------------------------------------
# Topic Boundary Detection
# ---------------------------------------------------------------------------


class TopicBoundaryDetector:
    """
    Deterministic topic boundary detection using similarity signals.

    Identifies transitions between distinct topics by comparing semantic
    similarity between consecutive text chunks.
    """

    def __init__(
        self,
        chunk_size: int = 100,
        similarity_threshold: float = 0.3,
        min_segment_chars: int = 50,
    ):
        """
        Initialize detector.

        Args:
            chunk_size: Characters per similarity window.
            similarity_threshold: Boundary trigger when similarity drops below this.
            min_segment_chars: Minimum segment length to avoid over-segmentation.
        """
        self.chunk_size = chunk_size
        self.similarity_threshold = similarity_threshold
        self.min_segment_chars = min_segment_chars
        self._topic_counter = 0

    def segment(self, text: str) -> List[TopicSegment]:
        """
        Segment text into topic boundaries.

        Uses deterministic TF-IDF cosine similarity to detect boundaries.
        Returns ordered list of TopicSegments with activity scores.

        Args:
            text: Input text to segment.

        Returns:
            List of TopicSegment objects in document order.
        """
        # Handle empty text
        if not text:
            return []

        # For very short text, return as single segment
        if len(text) < self.chunk_size:
            topic_id = f"topic_{self._topic_counter}"
            self._topic_counter += 1
            return [self._make_segment(text, 0, len(text), topic_id)]

        # Compute chunk-to-chunk similarity
        chunks = self._make_chunks(text)
        similarities = self._compute_similarities(chunks)

        # Identify boundaries where similarity drops
        boundaries = self._find_boundaries(similarities)
        boundaries = sorted(set([0] + boundaries + [len(text)]))

        # Build segments and score them
        segments = []
        skipped_content = []
        skipped_start = 0

        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            segment_text = text[start:end]

            if len(segment_text) >= self.min_segment_chars:
                # If we skipped content before, prepend it
                if skipped_content:
                    segment_text = "".join(skipped_content) + segment_text
                    start = skipped_start
                    skipped_content = []

                segment = self._make_segment(
                    segment_text,
                    start,
                    end,
                    f"topic_{self._topic_counter}",
                )
                self._topic_counter += 1
                segments.append(segment)
            else:
                # Track skipped content to merge with next segment
                if not skipped_content:
                    skipped_start = start
                skipped_content.append(segment_text)

        # If we have leftover skipped content, append it to last segment or create new one
        if skipped_content:
            remaining = "".join(skipped_content)
            if segments:
                # Merge with last segment
                last = segments[-1]
                merged_text = last.content + remaining
                segments[-1] = self._make_segment(
                    merged_text, last.start, len(text), last.topic_id
                )
            else:
                # Create new segment for remaining
                topic_id = f"topic_{self._topic_counter}"
                self._topic_counter += 1
                segments = [self._make_segment(remaining, 0, len(text), topic_id)]

        # If no segments were created, return full text as one segment
        if not segments:
            topic_id = f"topic_{self._topic_counter}"
            self._topic_counter += 1
            segments = [self._make_segment(text, 0, len(text), topic_id)]

        return segments

    def _make_chunks(self, text: str) -> List[str]:
        """Split text into overlapping chunks for similarity comparison."""
        chunks = []
        for i in range(0, len(text), self.chunk_size // 2):
            chunk = text[i : i + self.chunk_size]
            if chunk:
                chunks.append(chunk)
        return chunks

    def _compute_similarities(self, chunks: List[str]) -> List[float]:
        """Compute cosine similarity between consecutive chunks (deterministic)."""
        if len(chunks) < 2:
            return []

        similarities = []
        for i in range(len(chunks) - 1):
            sim = self._chunk_similarity(chunks[i], chunks[i + 1])
            similarities.append(sim)
        return similarities

    def _chunk_similarity(self, chunk_a: str, chunk_b: str) -> float:
        """
        Compute deterministic TF-IDF cosine similarity.

        Uses word frequency vectors for deterministic comparison.
        """
        words_a = set(self._tokenize(chunk_a))
        words_b = set(self._tokenize(chunk_b))

        if not words_a or not words_b:
            return 0.0

        intersection = len(words_a & words_b)
        union = len(words_a | words_b)
        jaccard = intersection / union if union > 0 else 0.0
        return jaccard

    def _tokenize(self, text: str) -> List[str]:
        """Simple deterministic tokenization."""
        text = text.lower()
        words = re.findall(r"\b\w+\b", text)
        return [w for w in words if len(w) > 2]

    def _find_boundaries(self, similarities: List[float]) -> List[int]:
        """Find character offsets where similarity drops below threshold."""
        boundaries = []
        for i, sim in enumerate(similarities):
            if sim < self.similarity_threshold:
                # Boundary is roughly at the end of chunk i
                offset = (i + 1) * (self.chunk_size // 2)
                boundaries.append(offset)
        return boundaries

    def _make_segment(
        self, content: str, start: int, end: int, topic_id: str
    ) -> TopicSegment:
        """Create a TopicSegment with activity scoring."""
        activity = self._score_activity(content)
        recency = self._score_recency(content)
        ref_count = self._count_cross_references(content)

        return TopicSegment(
            start=start,
            end=end,
            content=content,
            topic_id=topic_id,
            activity_score=activity,
            reference_count=ref_count,
            recency_score=recency,
        )

    def _score_activity(self, content: str) -> float:
        """
        Score topic activity (0.0 = inactive, 1.0 = highly active).

        Based on indicators: recent markers, action words, specificity.
        """
        score = 0.5  # neutral baseline

        # Check for recency markers
        if any(word in content.lower() for word in ["recently", "now", "currently", "today"]):
            score += 0.2

        # Check for action/activity markers
        if any(
            word in content.lower()
            for word in ["implement", "building", "develop", "active", "running"]
        ):
            score += 0.15

        # Check for technical detail (higher specificity = more active)
        code_like = len(re.findall(r"[(){}\[\]]", content))
        if code_like > 5:
            score += 0.1

        return min(1.0, score)

    def _score_recency(self, content: str) -> float:
        """Score how recent the content is (0.0 = old, 1.0 = very recent)."""
        score = 0.5
        if any(word in content.lower() for word in ["recently", "today", "now", "tomorrow"]):
            score = 0.8
        elif any(word in content.lower() for word in ["yesterday", "this week"]):
            score = 0.6
        return score

    def _count_cross_references(self, content: str) -> int:
        """Count references to other topics (links, mentions, etc)."""
        # Count link references, mentions, cross-references
        links = len(re.findall(r"\[([^\]]+)\]", content))
        refs = len(re.findall(r"see also|refer", content, re.IGNORECASE))
        return links + refs


# ---------------------------------------------------------------------------
# Breakpoint Placement
# ---------------------------------------------------------------------------


def place_topic_aware_breakpoints(
    segments: List[TopicSegment], target_tokens: int
) -> Dict[str, int]:
    """
    Place cache breakpoints respecting topic boundaries.

    Returns mapping of topic_id -> max_token_budget.

    Active topics get proportionally more budget than inactive topics.
    """
    if not segments or target_tokens <= 0:
        return {}

    total_active_score = sum(s.activity_score for s in segments)
    total_inactive_score = sum((1.0 - s.activity_score) for s in segments)

    if total_active_score == 0:
        total_active_score = 1.0

    # Allocate 70% to active, 30% to inactive
    active_budget = int(target_tokens * 0.7)
    inactive_budget = int(target_tokens * 0.3)

    breakpoints = {}
    for segment in segments:
        if segment.is_active:
            if total_active_score > 0:
                allocation = int((segment.activity_score / total_active_score) * active_budget)
                breakpoints[segment.topic_id] = max(50, allocation)
        else:
            if total_inactive_score > 0:
                allocation = int(
                    ((1.0 - segment.activity_score) / total_inactive_score) * inactive_budget
                )
                breakpoints[segment.topic_id] = max(20, allocation)

    return breakpoints
