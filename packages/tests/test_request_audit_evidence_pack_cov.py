"""Tests for request_audit.py and evidence_pack.py modules.

Coverage task: TPK-COV-AUDIT-EVID-001
Covers:
- RequestAudit dataclass and properties
- RequestAuditor tracker class
- format_audit_report function
- EvidenceItem class
- EvidencePack class methods
- Edge cases: empty records, missing fields, large payloads, special characters
"""

import time
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# request_audit.py tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequestAudit:
    """Tests for RequestAudit dataclass."""

    def test_request_audit_default_values(self):
        """RequestAudit should have sensible defaults."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit()
        assert audit.request_id == ""
        assert audit.timestamp == 0.0
        assert audit.model == ""
        assert audit.input_tokens == 0
        assert audit.sent_input_tokens == 0
        assert audit.output_tokens == 0
        assert audit.cache_read_tokens == 0
        assert audit.cache_hit is False
        assert audit.status == 200
        assert audit.metadata == {}

    def test_request_audit_populated_fields(self):
        """RequestAudit should store all provided fields."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(
            request_id="req-123",
            timestamp=1700000000.0,
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=600,
            output_tokens=200,
            cache_read_tokens=100,
            cache_hit=True,
            status=200,
            latency_ms=150,
            metadata={"user": "test"},
        )
        assert audit.request_id == "req-123"
        assert audit.model == "claude-sonnet-4-6"
        assert audit.input_tokens == 1000
        assert audit.sent_input_tokens == 600
        assert audit.cache_hit is True
        assert audit.metadata == {"user": "test"}

    def test_compression_tokens_saved(self):
        """compression_tokens_saved should be input - sent."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(input_tokens=1000, sent_input_tokens=600)
        assert audit.compression_tokens_saved == 400

    def test_compression_tokens_saved_no_negative(self):
        """compression_tokens_saved should never be negative."""
        from tokenpak.request_audit import RequestAudit

        # Edge case: sent more than input (shouldn't happen, but handle gracefully)
        audit = RequestAudit(input_tokens=500, sent_input_tokens=600)
        assert audit.compression_tokens_saved == 0

    def test_baseline_and_actual_cost(self):
        """baseline_cost and actual_cost calculations."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=600,
            output_tokens=200,
            cache_read_tokens=100,
        )
        # baseline: (1000 * 3.0 / 1M) + (200 * 15.0 / 1M) = 0.003 + 0.003 = 0.006
        assert audit.baseline_cost > 0
        # actual should be less due to compression and cache
        assert audit.actual_cost < audit.baseline_cost

    def test_compression_and_cache_savings(self):
        """compression_savings and cache_savings should be positive with tokens saved."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=600,
            cache_read_tokens=100,
        )
        # 400 tokens saved at sonnet rate: 400 * 3.0 / 1M = 0.0012
        assert audit.compression_savings > 0
        # Cache savings on 100 tokens
        assert audit.cache_savings > 0

    def test_cache_savings_zero_when_no_cache(self):
        """cache_savings should be 0 when no cache read tokens."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=600,
            cache_read_tokens=0,
        )
        assert audit.cache_savings == 0.0

    def test_total_savings_and_savings_pct(self):
        """total_savings should sum compression + cache savings."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=600,
            output_tokens=100,
            cache_read_tokens=50,
        )
        expected = audit.compression_savings + audit.cache_savings
        assert audit.total_savings == pytest.approx(expected)
        assert audit.savings_pct > 0

    def test_savings_pct_zero_baseline(self):
        """savings_pct should be 0 if baseline_cost is 0 (edge case)."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(
            model="claude-sonnet-4-6",
            input_tokens=0,
            output_tokens=0,
        )
        assert audit.savings_pct == 0.0

    def test_to_dict(self):
        """to_dict should return complete serializable dict."""
        from tokenpak.request_audit import RequestAudit

        audit = RequestAudit(
            request_id="req-456",
            timestamp=1700000000.0,
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=700,
            output_tokens=200,
            cache_hit=True,
        )
        d = audit.to_dict()
        assert d["request_id"] == "req-456"
        assert d["model"] == "claude-sonnet-4-6"
        assert "compression_savings_usd" in d
        assert "total_savings_usd" in d
        assert "savings_pct" in d
        assert isinstance(d["metadata"], dict)


class TestRequestAuditor:
    """Tests for RequestAuditor tracker."""

    def test_auditor_record_and_get_recent(self):
        """record() stores audits, get_recent() retrieves them."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor(max_recent=100)
        for i in range(5):
            auditor.record(RequestAudit(request_id=f"req-{i}", timestamp=float(i)))

        recent = auditor.get_recent(3)
        assert len(recent) == 3
        # Should be last 3
        assert recent[-1].request_id == "req-4"

    def test_auditor_auto_timestamps(self):
        """record() auto-sets timestamp if missing."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor()
        audit = RequestAudit(request_id="req-auto")
        assert audit.timestamp == 0.0
        auditor.record(audit)
        assert audit.timestamp > 0

    def test_auditor_max_recent_bounded(self):
        """Auditor should respect maxlen bound."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor(max_recent=5)
        for i in range(10):
            auditor.record(RequestAudit(request_id=f"req-{i}"))

        recent = auditor.get_recent(100)
        assert len(recent) == 5
        # Oldest should be req-5 (0-4 evicted)
        assert recent[0].request_id == "req-5"

    def test_auditor_filter_by_model(self):
        """filter() should filter by model substring."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor()
        auditor.record(RequestAudit(request_id="r1", model="claude-sonnet-4-6", timestamp=1.0))
        auditor.record(RequestAudit(request_id="r2", model="gpt-4o", timestamp=2.0))
        auditor.record(RequestAudit(request_id="r3", model="claude-opus-4-6", timestamp=3.0))

        sonnet = auditor.filter(model="sonnet")
        assert len(sonnet) == 1
        assert sonnet[0].request_id == "r1"

        claude = auditor.filter(model="claude")
        assert len(claude) == 2

    def test_auditor_filter_by_since(self):
        """filter(since=...) should filter by timestamp."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor()
        auditor.record(RequestAudit(request_id="old", timestamp=100.0))
        auditor.record(RequestAudit(request_id="new", timestamp=200.0))

        filtered = auditor.filter(since=150.0)
        assert len(filtered) == 1
        assert filtered[0].request_id == "new"

    def test_auditor_filter_by_request_id(self):
        """filter(request_id=...) should find exact match."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor()
        auditor.record(RequestAudit(request_id="target", timestamp=1.0))
        auditor.record(RequestAudit(request_id="other", timestamp=2.0))

        found = auditor.filter(request_id="target")
        assert len(found) == 1
        assert found[0].request_id == "target"

    def test_auditor_stats_empty(self):
        """stats() on empty auditor should return zeros."""
        from tokenpak.request_audit import RequestAuditor

        auditor = RequestAuditor()
        stats = auditor.stats()
        assert stats["total"] == 0
        assert stats["cache_hits"] == 0
        assert stats["avg_savings"] == 0.0

    def test_auditor_stats_populated(self):
        """stats() should compute correct aggregations."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor()
        # Mix of cache hits and compression-only
        auditor.record(RequestAudit(
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=500,
            cache_hit=True,
            cache_read_tokens=100,
            timestamp=1.0,
        ))
        auditor.record(RequestAudit(
            model="claude-sonnet-4-6",
            input_tokens=800,
            sent_input_tokens=600,
            cache_hit=False,
            timestamp=2.0,
        ))

        stats = auditor.stats()
        assert stats["total"] == 2
        assert stats["cache_hits"] == 1
        assert stats["cache_hit_pct"] == 50.0
        assert stats["total_savings"] > 0

    def test_auditor_to_csv(self):
        """to_csv() should produce valid CSV with headers."""
        from tokenpak.request_audit import RequestAudit, RequestAuditor

        auditor = RequestAuditor()
        auditor.record(RequestAudit(
            request_id="csv-test",
            model="claude-sonnet-4-6",
            input_tokens=1000,
            sent_input_tokens=800,
            timestamp=1700000000.0,
        ))

        csv = auditor.to_csv()
        lines = csv.strip().split("\n")
        assert len(lines) == 2  # header + 1 record
        assert "request_id" in lines[0]
        assert "csv-test" in lines[1]


class TestFormatAuditReport:
    """Tests for format_audit_report function."""

    def test_format_audit_report_empty(self):
        """Empty list should return 'No requests found.'"""
        from tokenpak.request_audit import format_audit_report

        result = format_audit_report([])
        assert result == "No requests found."

    def test_format_audit_report_with_records(self):
        """Report should include model, timestamps, costs."""
        from tokenpak.request_audit import RequestAudit, format_audit_report

        records = [
            RequestAudit(
                request_id="fmt-1",
                model="claude-sonnet-4-6",
                timestamp=1700000000.0,
                input_tokens=1000,
                sent_input_tokens=600,
                output_tokens=100,
                cache_hit=True,
                status=200,
            )
        ]
        report = format_audit_report(records)
        assert "TokenPak Request Audit" in report
        assert "claude-sonnet-4-6" in report
        assert "CACHE HIT" in report
        assert "$" in report


# ═══════════════════════════════════════════════════════════════════════════════
# evidence_pack.py tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvidenceItem:
    """Tests for EvidenceItem class."""

    def test_evidence_item_creation(self):
        """EvidenceItem stores all fields correctly."""
        from tokenpak.evidence_pack import EvidenceItem

        item = EvidenceItem(
            src="MEMORY",
            ref="M-1234",
            span="L10-L20",
            score=0.85,
            text="Test evidence text",
        )
        assert item.src == "MEMORY"
        assert item.ref == "M-1234"
        assert item.span == "L10-L20"
        assert item.score == 0.85
        assert item.text == "Test evidence text"

    def test_evidence_item_to_wire_line(self):
        """to_wire_line should produce correct EVIDENCE format."""
        from tokenpak.evidence_pack import EvidenceItem

        item = EvidenceItem(
            src="FILE",
            ref="@SOUL#v12",
            span="R3-R6",
            score=0.72,
            text="Important context",
        )
        wire = item.to_wire_line(1)
        assert wire.startswith("- E1 {")
        assert "src:FILE" in wire
        assert "ref:@SOUL#v12" in wire
        assert "score:0.72" in wire
        assert 'text:"Important context"' in wire

    def test_evidence_item_to_wire_line_escapes_quotes(self):
        """to_wire_line should escape quotes in text."""
        from tokenpak.evidence_pack import EvidenceItem

        item = EvidenceItem(
            src="LOG",
            ref="session.jsonl",
            span="L1-L5",
            score=0.5,
            text='Text with "quotes" inside',
        )
        wire = item.to_wire_line(3)
        assert '\\"quotes\\"' in wire

    def test_evidence_item_to_dict(self):
        """to_dict should return all fields."""
        from tokenpak.evidence_pack import EvidenceItem

        item = EvidenceItem(
            src="MEMORY",
            ref="M-999",
            span="L1-L10",
            score=0.95,
            text="Dict test",
        )
        d = item.to_dict()
        assert d["src"] == "MEMORY"
        assert d["ref"] == "M-999"
        assert d["score"] == 0.95
        assert d["text"] == "Dict test"

    def test_evidence_item_repr(self):
        """__repr__ should be informative."""
        from tokenpak.evidence_pack import EvidenceItem

        item = EvidenceItem(src="FILE", ref="test.md", span="L1", score=0.8, text="Hello")
        r = repr(item)
        assert "EvidenceItem" in r
        assert "FILE" in r
        assert "0.8" in r


class TestEvidencePack:
    """Tests for EvidencePack class."""

    def test_evidence_pack_empty(self):
        """Empty pack should have 0 items and show '(none)'."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        assert len(pack) == 0
        wire = pack.to_wire_format()
        assert "(none)" in wire

    def test_evidence_pack_add_item(self):
        """add_item should add manual evidence items."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="MANUAL", ref="doc.md", text="Manual evidence", score=1.0)

        assert len(pack) == 1
        assert pack.items[0].src == "MANUAL"
        assert pack.items[0].text == "Manual evidence"

    def test_evidence_pack_to_wire_format(self):
        """to_wire_format should output EVIDENCE: header and lines."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="FILE", ref="a.md", text="First", score=0.9)
        pack.add_item(src="MEMORY", ref="M-1", text="Second", score=0.7)

        wire = pack.to_wire_format()
        assert wire.startswith("EVIDENCE:")
        assert "E1" in wire
        assert "E2" in wire
        assert "First" in wire
        assert "Second" in wire

    @patch("tokenpak.evidence_pack.SpanExtractor")
    def test_evidence_pack_add_from_memory(self, mock_extractor_class):
        """add_from_memory should extract spans from memory chunks."""
        from tokenpak.evidence_pack import EvidencePack

        # Mock the extractor
        mock_extractor = MagicMock()
        mock_extractor.extract_span.return_value = {
            "text": "Extracted span",
            "span": "L5-L10",
            "score": 0.82,
        }
        mock_extractor_class.return_value = mock_extractor

        pack = EvidencePack()
        chunks = [
            {"id": "chunk-1", "text": "Full chunk text here"},
            {"id": "chunk-2", "text": "Another chunk"},
        ]
        pack.add_from_memory(chunks, query="test query", max_items=2)

        assert len(pack) == 2
        assert pack.items[0].src == "MEMORY"
        assert pack.items[0].ref == "chunk-1"
        assert pack.items[0].score == 0.82

    @patch("tokenpak.evidence_pack.SpanExtractor")
    def test_evidence_pack_add_from_memory_skips_empty(self, mock_extractor_class):
        """add_from_memory should skip empty chunks."""
        from tokenpak.evidence_pack import EvidencePack

        mock_extractor = MagicMock()
        mock_extractor_class.return_value = mock_extractor

        pack = EvidencePack()
        chunks = [
            {"id": "chunk-1", "text": ""},
            {"id": "chunk-2", "text": "   "},
        ]
        pack.add_from_memory(chunks, query="test")

        assert len(pack) == 0
        mock_extractor.extract_span.assert_not_called()

    def test_evidence_pack_filter_by_score(self):
        """filter_by_score should return new pack with filtered items."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="A", ref="1", text="High", score=0.9)
        pack.add_item(src="B", ref="2", text="Low", score=0.05)
        pack.add_item(src="C", ref="3", text="Medium", score=0.5)

        filtered = pack.filter_by_score(min_score=0.3)
        assert len(filtered) == 2
        assert all(it.score >= 0.3 for it in filtered.items)

    def test_evidence_pack_top_n(self):
        """top_n should return new pack with top N by score."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="A", ref="1", text="Low", score=0.2)
        pack.add_item(src="B", ref="2", text="High", score=0.95)
        pack.add_item(src="C", ref="3", text="Mid", score=0.6)

        top = pack.top_n(2)
        assert len(top) == 2
        assert top.items[0].score == 0.95
        assert top.items[1].score == 0.6

    def test_evidence_pack_sort_by_score(self):
        """sort_by_score should sort in-place."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="A", ref="1", text="Mid", score=0.5)
        pack.add_item(src="B", ref="2", text="High", score=0.9)
        pack.add_item(src="C", ref="3", text="Low", score=0.1)

        pack.sort_by_score(descending=True)
        assert pack.items[0].score == 0.9
        assert pack.items[2].score == 0.1

        pack.sort_by_score(descending=False)
        assert pack.items[0].score == 0.1

    def test_evidence_pack_total_tokens_estimation(self):
        """total_tokens should estimate token count."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="A", ref="1", text="Short text", score=1.0)
        pack.add_item(src="B", ref="2", text="A bit longer text here", score=1.0)

        # Should return some positive number (exact depends on tiktoken availability)
        assert pack.total_tokens() > 0

    def test_evidence_pack_repr(self):
        """__repr__ should show item count and token estimate."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="X", ref="Y", text="Test", score=0.5)

        r = repr(pack)
        assert "EvidencePack" in r
        assert "items=1" in r

    # ── Edge Cases ────────────────────────────────────────────────────────────

    def test_evidence_item_special_characters(self):
        """EvidenceItem should handle special characters in text."""
        from tokenpak.evidence_pack import EvidenceItem

        text_with_special = "Line1\nLine2\tTabbed\r\nWindows line"
        item = EvidenceItem(src="TEST", ref="ref", span="L1", score=1.0, text=text_with_special)
        wire = item.to_wire_line(1)
        # Should not crash, wire format should contain the text
        assert "Line1" in wire

    def test_evidence_pack_large_payload(self):
        """EvidencePack should handle many items efficiently."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        for i in range(100):
            pack.add_item(
                src="BULK",
                ref=f"item-{i}",
                text=f"Large payload item number {i} " * 10,
                score=i / 100.0,
            )

        assert len(pack) == 100
        wire = pack.to_wire_format()
        assert "E100" in wire

        # Filter and top_n should still work
        top = pack.top_n(5)
        assert len(top) == 5
        assert top.items[0].score == 0.99  # item-99

    def test_evidence_pack_empty_text_item(self):
        """EvidencePack should handle empty text in items."""
        from tokenpak.evidence_pack import EvidencePack

        pack = EvidencePack()
        pack.add_item(src="EMPTY", ref="ref", text="", score=0.5)

        assert len(pack) == 1
        wire = pack.to_wire_format()
        assert 'text:""' in wire
