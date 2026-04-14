# SPDX-License-Identifier: Apache-2.0
"""SpanExtractor — extractive span selection from text chunks.

Extracts the most relevant 20-80 token sentences from a chunk given a query.
Uses a lightweight TF-IDF-lite heuristic by default; upgrades to a
cross-encoder reranker if sentence-transformers is available.

Usage:
    extractor = SpanExtractor()
    result = extractor.extract_span(chunk_text, query, max_tokens=50)
    # {text: str, span: str, score: float}
"""

import re
from typing import List

try:
    import tiktoken

    _enc = tiktoken.encoding_for_model("gpt-4")

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))

    def _truncate_tokens(text: str, max_tokens: int) -> str:
        tokens = _enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return _enc.decode(tokens[:max_tokens]) + "..."

except ImportError:
    _enc = None  # type: ignore[assignment]

    def _count_tokens(text: str) -> int:
        # Rough approximation: 1 token ≈ 4 chars
        return max(1, len(text) // 4)

    def _truncate_tokens(text: str, max_tokens: int) -> str:
        approx_chars = max_tokens * 4
        return text[:approx_chars] + ("..." if len(text) > approx_chars else "")


# Optional: cross-encoder reranker
try:
    from sentence_transformers.cross_encoder import CrossEncoder as _CrossEncoder

    _CROSS_ENCODER_AVAILABLE = True
except ImportError:
    _CROSS_ENCODER_AVAILABLE = False


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences using simple regex."""
    # Split on ., !, ? followed by whitespace/newline, or on newlines
    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text.strip())
    # Filter empty, strip whitespace
    return [s.strip() for s in sentences if s.strip()]


def _heuristic_score(query: str, sentence: str) -> float:
    """
    TF-IDF-lite relevance score between query and sentence.

    Score = (# matching query terms in sentence) / len(query_terms)
    Boosted by:
    - Exact phrase match (+0.2)
    - Higher term density (terms / sentence_words)
    """
    query_terms = set(re.findall(r"\b\w+\b", query.lower()))
    sent_words = re.findall(r"\b\w+\b", sentence.lower())
    sent_set = set(sent_words)

    if not query_terms or not sent_words:
        return 0.0

    # Base: term overlap ratio
    matches = query_terms & sent_set
    base_score = len(matches) / len(query_terms)

    # Boost: term density (how much of the sentence is relevant)
    density = len(matches) / len(sent_words) if sent_words else 0.0

    # Boost: exact phrase match
    phrase_bonus = 0.2 if query.lower() in sentence.lower() else 0.0

    return min(1.0, base_score * 0.6 + density * 0.2 + phrase_bonus)


class SpanExtractor:
    """
    Extracts the most relevant sentence spans from a text chunk.

    Strategy:
    1. Split chunk into sentences
    2. Score each sentence against the query
    3. Select top-scoring sentences that fit within max_tokens
    4. Return extracted span with byte-offset reference

    Optional: uses cross-encoder reranker if sentence-transformers is
    installed and use_reranker=True.
    """

    def __init__(
        self,
        reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        use_reranker: bool = False,
    ):
        self.use_reranker = use_reranker and _CROSS_ENCODER_AVAILABLE
        self._reranker = None

        if self.use_reranker:
            try:
                self._reranker = _CrossEncoder(reranker_model)
            except Exception:
                self.use_reranker = False

    def _score_sentences(self, sentences: List[str], query: str) -> List[float]:
        """Score sentences against query. Returns parallel list of scores."""
        if self.use_reranker and self._reranker:
            pairs = [(query, s) for s in sentences]
            raw = self._reranker.predict(pairs)
            # Normalize to [0, 1]
            min_s, max_s = min(raw), max(raw)
            if max_s > min_s:
                return [(s - min_s) / (max_s - min_s) for s in raw]
            return [0.5] * len(raw)
        else:
            return [_heuristic_score(query, s) for s in sentences]

    def extract_span(
        self,
        chunk_text: str,
        query: str,
        max_tokens: int = 50,
    ) -> dict:
        """
        Extract the most relevant span from a chunk.

        Args:
            chunk_text:  Full text of the retrieved chunk
            query:       User query or search query string
            max_tokens:  Maximum tokens for the extracted span

        Returns:
            {
              "text":  str,   # extracted span text
              "span":  str,   # "byte:start-end" or "L1-LN"
              "score": float  # max relevance score across selected sentences
            }
        """
        sentences = _split_sentences(chunk_text)
        if not sentences:
            return {"text": chunk_text[:200], "span": "byte:0-0", "score": 0.0}

        scores = self._score_sentences(sentences, query)
        # Rank by score, but track original index for reconstruction
        indexed = list(enumerate(zip(sentences, scores)))  # [(orig_idx, (sent, score)), ...]
        ranked = sorted(indexed, key=lambda x: x[1][1], reverse=True)

        # Select top sentences that fit within max_tokens
        selected_indices: List[int] = []  # original sentence indices
        total_tokens = 0
        top_score = 0.0

        for orig_idx, (sent, score) in ranked:
            sent_tokens = _count_tokens(sent)
            if total_tokens + sent_tokens <= max_tokens:
                selected_indices.append(orig_idx)
                total_tokens += sent_tokens
                top_score = max(top_score, score)
            # Stop early once we're close to budget
            if total_tokens >= max_tokens - 2:
                break

        if not selected_indices:
            # Fallback: truncate the highest-scoring sentence
            best_sent = ranked[0][1][0] if ranked else sentences[0]
            top_score = ranked[0][1][1] if ranked else 0.0
            truncated = _truncate_tokens(best_sent, max_tokens)
            return {"text": truncated, "span": "byte:0-0", "score": top_score}

        # Reconstruct in original order (preserve reading flow)
        selected_idx_set = set(selected_indices)
        ordered = [sentences[i] for i in sorted(selected_idx_set)]
        span_text = " ".join(ordered)

        # Calculate byte span in original chunk
        try:
            first_sent = ordered[0]
            last_sent = ordered[-1]
            start_idx = chunk_text.find(first_sent)
            end_idx = chunk_text.find(last_sent)
            if end_idx != -1:
                end_idx += len(last_sent)
            span_ref = f"byte:{start_idx}-{end_idx}"
        except Exception:
            span_ref = "byte:0-0"

        return {"text": span_text, "span": span_ref, "score": top_score}

    def extract_spans_batch(
        self,
        chunks: List[dict],
        query: str,
        max_tokens_each: int = 50,
    ) -> List[dict]:
        """
        Extract spans from multiple chunks.

        Args:
            chunks: list of {text: ..., id: ..., ...}
            query:  search query

        Returns:
            list of {text, span, score, chunk_id}
        """
        results = []
        for chunk in chunks:
            text = chunk.get("text", "")
            span_data = self.extract_span(text, query, max_tokens_each)
            results.append(
                {
                    **span_data,
                    "chunk_id": chunk.get("id", "unknown"),
                }
            )
        return results
