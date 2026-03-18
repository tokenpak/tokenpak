"""Progressive-disclosure retrieval planning for ingest/query flows.

Level strategy (cheapest -> most expensive):
1. Document summary + section map
2. Relevant section summaries
3. Full relevant sections
4. Raw chunks from relevant sections
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


LEVEL_SUMMARY_MAP = 1
LEVEL_SECTION_SUMMARIES = 2
LEVEL_FULL_SECTIONS = 3
LEVEL_RAW_CHUNKS = 4


@dataclass(frozen=True)
class SectionView:
    """Section-level representation used by progressive disclosure."""

    section_id: str
    title: str
    summary: str = ""
    full_text: str = ""
    chunks: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentView:
    """Document representation used to generate disclosure payloads."""

    doc_id: str
    summary: str
    sections: Tuple[SectionView, ...] = field(default_factory=tuple)


def choose_disclosure_level(
    *,
    intent: Optional[str],
    query: str,
    ambiguity: bool = False,
    precision_needed: bool = False,
    conflicting_sources: bool = False,
) -> int:
    """Auto-select disclosure level from intent + query complexity.

    Escalation priorities:
    - conflicting sources / ambiguity / precision needs -> raw chunks (L4)
    - explicit quote/citation/exact asks -> full section (L3)
    - compare/multi-hop asks -> section summaries (L2)
    - default -> summary + map (L1)
    """
    if conflicting_sources or ambiguity or precision_needed:
        return LEVEL_RAW_CHUNKS

    merged = " ".join(x for x in (intent or "", query or "") if x).lower()

    if _contains_any(merged, ("exact", "verbatim", "quote", "citation", "cite", "line")):
        return LEVEL_FULL_SECTIONS

    complexity = _query_complexity_score(query)
    if complexity >= 5 or _contains_any(merged, ("compare", "difference", "tradeoff", "why", "how")):
        return LEVEL_SECTION_SUMMARIES

    return LEVEL_SUMMARY_MAP


def shortlist_sections(document: DocumentView, query: str, top_k: int = 3) -> List[SectionView]:
    """Identify top 1-3 relevant sections for a query."""
    top_k = max(1, min(3, top_k))
    terms = _tokenize(query)

    scored: List[Tuple[int, SectionView]] = []
    for section in document.sections:
        corpus = " ".join((section.title, section.summary, section.full_text)).lower()
        score = sum(corpus.count(t) for t in terms)
        score += 2 if any(t in section.title.lower() for t in terms) else 0
        scored.append((score, section))

    # keep deterministic ordering for ties (score desc + title asc + id asc)
    scored.sort(key=lambda item: (-item[0], item[1].title.lower(), item[1].section_id))

    if not scored:
        return []

    # If query has no useful terms, default to first sections deterministically.
    if all(score == 0 for score, _ in scored):
        return [section for _, section in scored[:top_k]]

    return [section for score, section in scored[:top_k] if score > 0][:top_k]


def build_disclosure_payload(
    document: DocumentView,
    *,
    query: str,
    intent: Optional[str] = None,
    top_k: int = 3,
    ambiguity: bool = False,
    precision_needed: bool = False,
    conflicting_sources: bool = False,
) -> Dict[str, Any]:
    """Build progressive-disclosure payload with automatic fallback behavior."""
    level = choose_disclosure_level(
        intent=intent,
        query=query,
        ambiguity=ambiguity,
        precision_needed=precision_needed,
        conflicting_sources=conflicting_sources,
    )

    selected = shortlist_sections(document, query=query, top_k=top_k)
    selected = selected[: max(1, min(3, top_k))]

    payload: Dict[str, Any] = {
        "doc_id": document.doc_id,
        "level": level,
        "summary": document.summary,
        "section_map": [
            {"section_id": section.section_id, "title": section.title}
            for section in document.sections
        ],
        "selected_section_ids": [s.section_id for s in selected],
    }

    if level >= LEVEL_SECTION_SUMMARIES:
        payload["relevant_section_summaries"] = [
            {
                "section_id": s.section_id,
                "title": s.title,
                "summary": s.summary,
            }
            for s in selected
        ]

    if level >= LEVEL_FULL_SECTIONS:
        payload["relevant_sections"] = [
            {
                "section_id": s.section_id,
                "title": s.title,
                "content": s.full_text,
            }
            for s in selected
        ]

    if level >= LEVEL_RAW_CHUNKS:
        payload["raw_chunks"] = [
            {
                "section_id": s.section_id,
                "chunks": list(s.chunks),
            }
            for s in selected
        ]

    return payload


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _tokenize(text: str) -> List[str]:
    words = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    stop = {"the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "is", "are", "what"}
    return [w for w in words if w not in stop and len(w) > 2]


def _query_complexity_score(query: str) -> int:
    terms = _tokenize(query)
    score = len(terms) // 4
    if "?" in query:
        score += 1
    if len(query) > 140:
        score += 1
    if _contains_any(query.lower(), ("compare", "versus", "tradeoff", "pros", "cons", "because", "why", "how")):
        score += 2
    return score
