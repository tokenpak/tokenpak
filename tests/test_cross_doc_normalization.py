# SPDX-License-Identifier: MIT
"""Tests for tokenpak._internal.ingest.cross_doc — Cross-Document Normalization."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.ingest.cross_doc", reason="module not available in current build")
import pytest

from tokenpak._internal.ingest.cross_doc import (
    AgreementMap,
    CrossDocAnalyzer,
    DocCard,
    EvidenceMatrix,
    MetricTable,
    SchemaConverter,
    analyze_docs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PAPER_A = """
Title: Efficient Transformers for NLP

Authors: Alice Smith, Bob Jones

Abstract
We present a novel attention mechanism that reduces compute by 40%.

Keywords: transformers, attention, NLP, efficiency

Methods
We use a sparse attention kernel trained on 100B tokens.
Our approach reduces memory by using linear complexity.

Results
We find that our method achieves 92% accuracy on GLUE benchmarks.
We demonstrate 40% speedup vs baseline on all tasks.

Conclusion
In conclusion, sparse attention is a promising direction for efficient NLP.
"""

PAPER_B = """
Title: Dense Retrieval for Open-Domain QA

Authors: Carol Lee, Dan Wu

Abstract
We propose a bi-encoder dense retrieval system for open-domain QA, achieving
top-1 accuracy of 78% on NaturalQuestions.

Keywords: retrieval, QA, dense, bi-encoder

Methods
Our model is fine-tuned on NQ train set using in-batch negatives.
We apply a bi-encoder architecture for fast retrieval.

Results
Results show that dense retrieval outperforms BM25 by 15 accuracy points.
Our method achieves 78% accuracy and 95% recall@10.

Conclusion
We conclude that dense retrieval generalizes well across domains.
"""

PAPER_C = """
Title: BM25 vs Dense: A Comparison Study

Authors: Eve Park, Frank Müller

Abstract
This paper presents a systematic comparison of BM25 and dense retrieval
methods across 8 datasets with accuracy ranging from 65% to 88%.

Keywords: retrieval, BM25, comparison, benchmark

Methods
We use a standard BM25 implementation and compare against DPR.
Our approach involves 5-fold cross-validation on each dataset.

Results
We find that BM25 significantly outperforms dense methods on out-of-domain data.
We show that dense retrieval achieves higher accuracy on in-domain benchmarks.

Conclusion
In conclusion, hybrid retrieval (BM25 + dense) outperforms either alone.
Future work should focus on domain-adaptive retrieval.
"""


@pytest.fixture
def converter() -> SchemaConverter:
    return SchemaConverter()


@pytest.fixture
def analyzer() -> CrossDocAnalyzer:
    return CrossDocAnalyzer()


@pytest.fixture
def three_docs() -> list[dict]:
    return [
        {"source": "paper_a", "text": PAPER_A},
        {"source": "paper_b", "text": PAPER_B},
        {"source": "paper_c", "text": PAPER_C},
    ]


# ---------------------------------------------------------------------------
# 1. SchemaConverter — normalization correctness
# ---------------------------------------------------------------------------


class TestSchemaConverter:
    def test_extracts_title(self, converter):
        card = converter.convert(PAPER_A, source="paper_a")
        assert card.title is not None
        assert "Transformer" in card.title or "Efficient" in card.title

    def test_extracts_authors(self, converter):
        card = converter.convert(PAPER_A, source="paper_a")
        assert len(card.authors) >= 1
        assert any("Alice" in a or "Smith" in a for a in card.authors)

    def test_extracts_abstract(self, converter):
        card = converter.convert(PAPER_A, source="paper_a")
        assert card.abstract is not None
        assert len(card.abstract) <= converter.max_abstract_chars

    def test_extracts_key_findings(self, converter):
        card = converter.convert(PAPER_A, source="paper_a")
        assert len(card.key_findings) >= 1

    def test_extracts_methods(self, converter):
        card = converter.convert(PAPER_B, source="paper_b")
        assert len(card.methods) >= 1

    def test_extracts_metrics(self, converter):
        card = converter.convert(PAPER_B, source="paper_b")
        # Should find at least one numeric metric (e.g. accuracy=78)
        assert len(card.metrics) >= 1

    def test_extracts_keywords(self, converter):
        card = converter.convert(PAPER_A, source="paper_a")
        assert len(card.keywords) >= 1

    def test_extracts_conclusions(self, converter):
        card = converter.convert(PAPER_A, source="paper_a")
        assert len(card.conclusions) >= 1

    def test_source_preserved(self, converter):
        card = converter.convert(PAPER_A, source="my_source")
        assert card.source == "my_source"

    def test_metadata_passthrough(self, converter):
        card = converter.convert(PAPER_A, source="x", metadata={"year": 2024, "venue": "EMNLP"})
        assert card.metadata["year"] == 2024
        assert card.metadata["venue"] == "EMNLP"

    def test_metadata_title_override(self, converter):
        card = converter.convert(PAPER_A, source="x", metadata={"title": "Override Title"})
        assert card.title == "Override Title"

    def test_token_estimate_positive(self, converter):
        card = converter.convert(PAPER_A, source="x")
        assert card.token_estimate() > 0

    def test_to_dict_keys(self, converter):
        card = converter.convert(PAPER_A, source="x")
        d = card.to_dict()
        for key in ("source", "title", "authors", "abstract", "key_findings",
                    "methods", "metrics", "conclusions", "keywords", "metadata"):
            assert key in d


# ---------------------------------------------------------------------------
# 2. CrossDocAnalyzer.normalize — batch normalization
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_returns_correct_count(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        assert len(cards) == 3

    def test_all_cards_are_doc_cards(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        assert all(isinstance(c, DocCard) for c in cards)

    def test_sources_preserved(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        sources = [c.source for c in cards]
        assert sources == ["paper_a", "paper_b", "paper_c"]

    def test_default_source_if_missing(self, analyzer):
        docs = [{"text": "hello world"}]
        cards = analyzer.normalize(docs)
        assert cards[0].source == "doc_0"

    def test_empty_text_handled(self, analyzer):
        docs = [{"source": "empty", "text": ""}]
        cards = analyzer.normalize(docs)
        assert len(cards) == 1
        assert cards[0].source == "empty"


# ---------------------------------------------------------------------------
# 3. CrossDocAnalyzer.compare — side_by_side mode
# ---------------------------------------------------------------------------


class TestSideBySide:
    def test_returns_comparison_report(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="side_by_side")
        assert report.mode == "side_by_side"

    def test_agreement_maps_present(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="side_by_side")
        assert len(report.agreement_maps) > 0

    def test_agreement_map_fields(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="side_by_side")
        field_names = [am.field for am in report.agreement_maps]
        assert "num_findings" in field_names
        assert "num_metrics" in field_names

    def test_evidence_matrix_built(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="side_by_side")
        assert isinstance(report.evidence_matrix, EvidenceMatrix)
        assert len(report.evidence_matrix.rows) == 3

    def test_metric_table_built(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="side_by_side")
        assert isinstance(report.metric_table, MetricTable)
        assert len(report.metric_table.rows) == 3

    def test_summary_runs(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="side_by_side")
        s = report.summary()
        assert "Cross-Document Report" in s
        assert "side_by_side" in s


# ---------------------------------------------------------------------------
# 4. CrossDocAnalyzer.compare — merged mode
# ---------------------------------------------------------------------------


class TestMergedMode:
    def test_synthesis_present(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="merged")
        assert report.synthesis is not None
        assert len(report.synthesis) > 0

    def test_synthesis_contains_findings(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="merged")
        # Should mention findings section if any card has them
        has_findings = any(c.key_findings for c in cards)
        if has_findings:
            assert "Findings" in report.synthesis or "•" in report.synthesis

    def test_merged_summary(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="merged")
        s = report.summary()
        assert "Merged Synthesis" in s


# ---------------------------------------------------------------------------
# 5. CrossDocAnalyzer.compare — conflict mode
# ---------------------------------------------------------------------------


class TestConflictMode:
    def test_conflicts_is_list(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="conflict")
        assert isinstance(report.conflicts, list)

    def test_conflict_summary(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        report = analyzer.compare(cards, mode="conflict")
        s = report.summary()
        assert "Conflict" in s or "conflict" in s

    def test_invalid_mode_raises(self, analyzer, three_docs):
        cards = analyzer.normalize(three_docs)
        with pytest.raises(ValueError, match="Unknown mode"):
            analyzer.compare(cards, mode="bogus_mode")


# ---------------------------------------------------------------------------
# 6. AgreementMap
# ---------------------------------------------------------------------------


class TestAgreementMap:
    def test_agreement_ratio_full(self):
        am = AgreementMap(
            field="x",
            values=[("a", "foo"), ("b", "foo"), ("c", "foo")],
            consensus="foo",
        )
        assert am.agreement_ratio == pytest.approx(1.0)
        assert am.status == "agreement"

    def test_agreement_ratio_conflict(self):
        am = AgreementMap(
            field="x",
            values=[("a", "foo"), ("b", "bar"), ("c", "baz")],
            consensus=None,
        )
        assert am.agreement_ratio == pytest.approx(1 / 3)
        assert am.status == "conflict"

    def test_agreement_ratio_partial(self):
        am = AgreementMap(
            field="x",
            values=[("a", "foo"), ("b", "foo"), ("c", "bar")],
            consensus="foo",
        )
        assert am.agreement_ratio == pytest.approx(2 / 3)
        assert am.status == "partial"

    def test_empty_values(self):
        am = AgreementMap(field="x", values=[], consensus=None)
        assert am.agreement_ratio == 0.0


# ---------------------------------------------------------------------------
# 7. MetricTable
# ---------------------------------------------------------------------------


class TestMetricTable:
    def test_to_table_renders(self):
        mt = MetricTable(
            metric_names=["accuracy", "speedup"],
            rows=[
                {"source": "doc_a", "metrics": {"accuracy": 92, "speedup": 1.4}},
                {"source": "doc_b", "metrics": {"accuracy": 78, "speedup": 1.0}},
            ],
        )
        table = mt.to_table()
        assert "accuracy" in table
        assert "doc_a" in table

    def test_divergence_computed(self):
        mt = MetricTable(
            metric_names=["accuracy"],
            rows=[
                {"source": "a", "metrics": {"accuracy": 90}},
                {"source": "b", "metrics": {"accuracy": 70}},
                {"source": "c", "metrics": {"accuracy": 80}},
            ],
        )
        div = mt.divergence()
        assert "accuracy" in div
        assert div["accuracy"] > 0

    def test_divergence_single_doc(self):
        mt = MetricTable(
            metric_names=["accuracy"],
            rows=[{"source": "a", "metrics": {"accuracy": 90}}],
        )
        div = mt.divergence()
        assert "accuracy" not in div  # need ≥2 values


# ---------------------------------------------------------------------------
# 8. EvidenceMatrix
# ---------------------------------------------------------------------------


class TestEvidenceMatrix:
    def test_to_table_renders(self):
        em = EvidenceMatrix(
            claims=["claim one", "claim two"],
            rows=[
                {"source": "doc_a", "evidence": {0: True, 1: None}},
                {"source": "doc_b", "evidence": {0: None, 1: True}},
            ],
        )
        table = em.to_table()
        assert "doc_a" in table
        assert "doc_b" in table


# ---------------------------------------------------------------------------
# 9. analyze_docs() convenience function
# ---------------------------------------------------------------------------


class TestAnalyzeDocs:
    def test_side_by_side(self, three_docs):
        report = analyze_docs(three_docs, mode="side_by_side")
        assert report.mode == "side_by_side"
        assert len(report.cards) == 3

    def test_merged(self, three_docs):
        report = analyze_docs(three_docs, mode="merged")
        assert report.synthesis is not None

    def test_conflict(self, three_docs):
        report = analyze_docs(three_docs, mode="conflict")
        assert isinstance(report.conflicts, list)

    def test_custom_converter(self, three_docs):
        conv = SchemaConverter(max_abstract_chars=50, max_findings=2)
        report = analyze_docs(three_docs, mode="side_by_side", converter=conv)
        for card in report.cards:
            if card.abstract:
                assert len(card.abstract) <= 50
            assert len(card.key_findings) <= 2

    def test_single_doc(self):
        docs = [{"source": "solo", "text": PAPER_A}]
        report = analyze_docs(docs, mode="side_by_side")
        assert len(report.cards) == 1


# ---------------------------------------------------------------------------
# 10. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_docs_raises(self, analyzer):
        with pytest.raises((ValueError, IndexError)):
            analyzer.compare([], mode="side_by_side")

    def test_duplicate_docs(self, analyzer):
        docs = [
            {"source": "dup_a", "text": PAPER_A},
            {"source": "dup_b", "text": PAPER_A},
        ]
        cards = analyzer.normalize(docs)
        report = analyzer.compare(cards, mode="conflict")
        # Duplicate docs should show high agreement, minimal conflicts
        agreement_count = sum(
            1 for am in report.agreement_maps if am.status == "agreement"
        )
        # They should agree on most structural fields
        assert agreement_count >= 3

    def test_very_short_text(self, analyzer):
        docs = [{"source": "short", "text": "Hello."}]
        cards = analyzer.normalize(docs)
        assert cards[0].source == "short"

    def test_repr_doc_card(self, converter):
        card = converter.convert(PAPER_A, source="test")
        r = repr(card)
        assert "DocCard" in r
        assert "test" in r

    def test_repr_agreement_map(self):
        am = AgreementMap(field="f", values=[("a", 1)], consensus="1")
        r = repr(am)
        assert "AgreementMap" in r
