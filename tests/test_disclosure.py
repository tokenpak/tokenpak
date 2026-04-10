from __future__ import annotations

from tokenpak._internal.ingest.disclosure import (
    DocumentView,
    SectionView,
    LEVEL_FULL_SECTIONS,
    LEVEL_RAW_CHUNKS,
    LEVEL_SECTION_SUMMARIES,
    LEVEL_SUMMARY_MAP,
    build_disclosure_payload,
    choose_disclosure_level,
    shortlist_sections,
)


def _sample_doc() -> DocumentView:
    return DocumentView(
        doc_id="doc-123",
        summary="System design overview for proxy fallback and costs.",
        sections=(
            SectionView(
                section_id="s1",
                title="Overview",
                summary="High-level architecture.",
                full_text="TokenPak architecture overview with constraints.",
                chunks=("overview chunk 1", "overview chunk 2"),
            ),
            SectionView(
                section_id="s2",
                title="Fallback Logic",
                summary="When to fail over providers.",
                full_text="Fallback is triggered by timeout and provider errors.",
                chunks=("fallback raw 1", "fallback raw 2"),
            ),
            SectionView(
                section_id="s3",
                title="Cost Controls",
                summary="Budget and spend guardrails.",
                full_text="Caps, throttles, and per-session ceilings.",
                chunks=("cost raw 1",),
            ),
        ),
    )


def test_choose_disclosure_level_defaults_to_summary_map() -> None:
    level = choose_disclosure_level(intent="summarize", query="what is this doc about")
    assert level == LEVEL_SUMMARY_MAP


def test_choose_disclosure_level_escalates_to_full_for_exact_intent() -> None:
    level = choose_disclosure_level(intent="quote exact wording", query="cite fallback trigger line")
    assert level == LEVEL_FULL_SECTIONS


def test_choose_disclosure_level_escalates_to_raw_for_precision_signals() -> None:
    level = choose_disclosure_level(
        intent="investigate discrepancy",
        query="why are two sources disagreeing",
        ambiguity=True,
    )
    assert level == LEVEL_RAW_CHUNKS


def test_shortlist_sections_limits_to_top_3_and_ranks_relevance() -> None:
    doc = _sample_doc()
    selected = shortlist_sections(doc, query="fallback provider timeout errors", top_k=3)
    assert 1 <= len(selected) <= 3
    assert selected[0].section_id == "s2"


def test_build_payload_level_2_includes_relevant_section_summaries_only() -> None:
    doc = _sample_doc()
    payload = build_disclosure_payload(
        doc,
        query="compare fallback and costs tradeoffs",
        intent="compare",
    )
    assert payload["level"] == LEVEL_SECTION_SUMMARIES
    assert "relevant_section_summaries" in payload
    assert "relevant_sections" not in payload
    assert "raw_chunks" not in payload


def test_build_payload_level_4_includes_raw_chunks() -> None:
    doc = _sample_doc()
    payload = build_disclosure_payload(
        doc,
        query="resolve conflicting sources",
        intent="investigate",
        conflicting_sources=True,
    )
    assert payload["level"] == LEVEL_RAW_CHUNKS
    assert "raw_chunks" in payload
    assert len(payload["raw_chunks"]) >= 1
