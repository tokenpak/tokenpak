# SPDX-License-Identifier: Apache-2.0
"""EvidencePack — extractive spans with provenance for TokenPak Protocol Phase 2.

Replaces full-chunk memory dumps with compact evidence items:
  - Old: 10 chunks × 300 tokens/chunk = 3,000 tokens
  - New: 10 spans × 50 tokens/span = 500 tokens  (83% savings)

Wire format:
  EVIDENCE:
  - E1 {src:MEMORY, ref:M-8831, span:L120-L143, score:0.82, text:"...excerpt..."}
  - E2 {src:FILE, ref:@SOUL#v12, span:R3-R6, score:0.61, text:"...excerpt..."}

Usage:
    pack = EvidencePack()
    pack.add_from_memory(memory_chunks, query, max_items=10)
    pack.add_from_file("/path/to/file.md", query, max_items=3)
    wire = pack.to_wire_format()
"""

from pathlib import Path
from typing import List, Optional

from .span_extractor import SpanExtractor


class EvidenceItem:
    """A single evidence item with provenance."""

    __slots__ = ("src", "ref", "span", "score", "text")

    def __init__(
        self,
        src: str,
        ref: str,
        span: str,
        score: float,
        text: str,
    ):
        self.src = src
        self.ref = ref
        self.span = span
        self.score = score
        self.text = text

    def to_wire_line(self, index: int) -> str:
        """Render as EVIDENCE wire line."""
        escaped_text = self.text.replace('"', '\\"')
        return (
            f"- E{index} "
            f"{{src:{self.src}, ref:{self.ref}, span:{self.span}, "
            f'score:{self.score:.2f}, text:"{escaped_text}"}}'
        )

    def to_dict(self) -> dict:
        return {
            "src": self.src,
            "ref": self.ref,
            "span": self.span,
            "score": self.score,
            "text": self.text,
        }

    def __repr__(self) -> str:
        return f"<EvidenceItem src={self.src!r} score={self.score:.2f} len={len(self.text)}>"


class EvidencePack:
    """
    Builds an EVIDENCE section from memory search results and files.

    Integration with memory search (replacing full chunk dumps):
        # Old way:
        memory_results = memory.search(query, top_k=10)
        context_text = "\\n\\n".join([r['text'] for r in memory_results])

        # New way:
        memory_results = memory.search(query, top_k=10)
        pack = EvidencePack()
        pack.add_from_memory(memory_results, query, max_items=10)
        context_text = pack.to_wire_format()
    """

    def __init__(self, use_reranker: bool = False):
        self.extractor = SpanExtractor(use_reranker=use_reranker)
        self.items: List[EvidenceItem] = []

    # ── Builders ─────────────────────────────────────────────────────────────

    def add_from_memory(
        self,
        memory_chunks: List[dict],
        query: str,
        max_items: int = 10,
        max_tokens_each: int = 50,
    ) -> None:
        """
        Convert memory search results into evidence items.

        Expected chunk keys: id (or chunk_id), text
        """
        for chunk in memory_chunks[:max_items]:
            chunk_text = chunk.get("text", "")
            if not chunk_text.strip():
                continue

            chunk_id = chunk.get("id") or chunk.get("chunk_id", "M-unknown")
            span_data = self.extractor.extract_span(chunk_text, query, max_tokens_each)

            self.items.append(
                EvidenceItem(
                    src="MEMORY",
                    ref=str(chunk_id),
                    span=span_data["span"],
                    score=float(span_data["score"]),
                    text=span_data["text"],
                )
            )

    def add_from_file(
        self,
        file_path: str,
        query: str,
        max_tokens_each: int = 80,
        ref_override: Optional[str] = None,
    ) -> None:
        """
        Extract the most relevant span from a file.

        Args:
            file_path:     Path to the file
            query:         Search query
            max_tokens_each: Max span size
            ref_override:  Use this as the ref (e.g. '@SOUL#v12'), otherwise uses file_path
        """
        path = Path(file_path)
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8", errors="replace")
        span_data = self.extractor.extract_span(content, query, max_tokens_each)

        self.items.append(
            EvidenceItem(
                src="FILE",
                ref=ref_override or file_path,
                span=span_data["span"],
                score=float(span_data["score"]),
                text=span_data["text"],
            )
        )

    def add_from_log(
        self,
        log_ref: str,
        log_text: str,
        query: str,
        turn_range: Optional[str] = None,
        max_tokens_each: int = 50,
    ) -> None:
        """
        Extract span from a session log or JSONL.

        Args:
            log_ref:     e.g. "session_2026-02-25.jsonl"
            log_text:    Full log text to extract from
            query:       Query to score against
            turn_range:  Optional override for span ref, e.g. "turns:38-40"
        """
        span_data = self.extractor.extract_span(log_text, query, max_tokens_each)
        span_ref = turn_range or span_data["span"]

        self.items.append(
            EvidenceItem(
                src="LOG",
                ref=log_ref,
                span=span_ref,
                score=float(span_data["score"]),
                text=span_data["text"],
            )
        )

    def add_item(
        self,
        src: str,
        ref: str,
        text: str,
        score: float = 1.0,
        span: str = "manual",
    ) -> None:
        """Manually add a pre-extracted evidence item."""
        self.items.append(EvidenceItem(src=src, ref=ref, span=span, score=score, text=text))

    # ── Wire format ──────────────────────────────────────────────────────────

    def to_wire_format(self) -> str:
        """Format evidence pack for LLM payload."""
        if not self.items:
            return "EVIDENCE:\n(none)"
        lines = [item.to_wire_line(i + 1) for i, item in enumerate(self.items)]
        return "EVIDENCE:\n" + "\n".join(lines)

    # ── Filtering ────────────────────────────────────────────────────────────

    def filter_by_score(self, min_score: float = 0.1) -> "EvidencePack":
        """Return new EvidencePack with items above min_score."""
        new_pack = EvidencePack()
        new_pack.items = [it for it in self.items if it.score >= min_score]
        return new_pack

    def top_n(self, n: int) -> "EvidencePack":
        """Return new EvidencePack with top N items by score."""
        new_pack = EvidencePack()
        new_pack.items = sorted(self.items, key=lambda x: x.score, reverse=True)[:n]
        return new_pack

    def sort_by_score(self, descending: bool = True) -> None:
        """Sort items in-place by score."""
        self.items.sort(key=lambda x: x.score, reverse=descending)

    # ── Stats ────────────────────────────────────────────────────────────────

    def total_tokens(self) -> int:
        """Estimate total tokens in all evidence items."""
        try:
            import tiktoken

            enc = tiktoken.encoding_for_model("gpt-4")
            return sum(len(enc.encode(it.text)) for it in self.items)
        except ImportError:
            return sum(max(1, len(it.text) // 4) for it in self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __repr__(self) -> str:
        return f"<EvidencePack items={len(self.items)} tokens≈{self.total_tokens()}>"
