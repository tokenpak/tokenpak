"""Tests for tokenpak.request_audit — per-request savings audit."""

import time
import pytest
from tokenpak.request_audit import RequestAudit, RequestAuditor, format_audit_report


class TestRequestAudit:
    """Test RequestAudit dataclass cost calculations."""

    def test_compression_tokens_saved(self):
        r = RequestAudit(input_tokens=10000, sent_input_tokens=8000)
        assert r.compression_tokens_saved == 2000

    def test_compression_tokens_saved_no_compression(self):
        r = RequestAudit(input_tokens=1000, sent_input_tokens=1000)
        assert r.compression_tokens_saved == 0

    def test_baseline_cost_opus(self):
        r = RequestAudit(model="claude-opus-4-6", input_tokens=1_000_000, output_tokens=0)
        # $15/MTok input
        assert r.baseline_cost == pytest.approx(15.0, rel=0.01)

    def test_baseline_cost_haiku(self):
        r = RequestAudit(model="claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0)
        # $0.80/MTok input
        assert r.baseline_cost == pytest.approx(0.80, rel=0.01)

    def test_compression_savings(self):
        r = RequestAudit(
            model="claude-opus-4-6",
            input_tokens=10000,
            sent_input_tokens=8000,
        )
        # 2000 tokens saved × $15/MTok = $0.03
        assert r.compression_savings == pytest.approx(0.03, rel=0.01)

    def test_cache_savings(self):
        r = RequestAudit(
            model="claude-opus-4-6",
            input_tokens=10000,
            sent_input_tokens=10000,
            cache_read_tokens=50000,
            cache_hit=True,
        )
        # 50000 tokens at $15/MTok = $0.75 full price
        # 50000 tokens at $1.50/MTok = $0.075 cache price
        # Savings = $0.75 - $0.075 = $0.675
        assert r.cache_savings == pytest.approx(0.675, rel=0.01)

    def test_total_savings(self):
        r = RequestAudit(
            model="claude-opus-4-6",
            input_tokens=10000,
            sent_input_tokens=8000,
            cache_read_tokens=50000,
            cache_hit=True,
        )
        expected = r.compression_savings + r.cache_savings
        assert r.total_savings == pytest.approx(expected)

    def test_savings_pct(self):
        r = RequestAudit(
            model="claude-opus-4-6",
            input_tokens=10000,
            sent_input_tokens=5000,
            output_tokens=0,
        )
        # baseline = 10000 * 15 / 1M = 0.15
        # compression savings = 5000 * 15 / 1M = 0.075
        # pct = 0.075 / 0.15 * 100 = 50%
        assert r.savings_pct == pytest.approx(50.0, rel=0.1)

    def test_no_savings(self):
        r = RequestAudit(
            model="claude-haiku-4-5",
            input_tokens=100,
            sent_input_tokens=100,
            cache_read_tokens=0,
        )
        assert r.total_savings == 0.0
        assert r.savings_pct == 0.0

    def test_to_dict(self):
        r = RequestAudit(
            request_id="test-123",
            model="claude-opus-4-6",
            input_tokens=5000,
            sent_input_tokens=4000,
            output_tokens=100,
            cache_read_tokens=1000,
            cache_hit=True,
            status=200,
            timestamp=1000000.0,
        )
        d = r.to_dict()
        assert d["request_id"] == "test-123"
        assert d["model"] == "claude-opus-4-6"
        assert d["input_tokens"] == 5000
        assert d["compression_savings_usd"] > 0
        assert d["savings_pct"] > 0
        assert isinstance(d["metadata"], dict)

    def test_unknown_model_uses_defaults(self):
        r = RequestAudit(
            model="some-unknown-model",
            input_tokens=1_000_000,
            sent_input_tokens=800_000,
        )
        # Should use default rates (sonnet-like: $3/MTok)
        assert r.compression_savings > 0
        assert r.baseline_cost > 0


class TestRequestAuditor:
    """Test RequestAuditor tracking and filtering."""

    def _make_auditor(self, n=5):
        auditor = RequestAuditor(max_recent=100)
        now = time.time()
        for i in range(n):
            auditor.record(RequestAudit(
                request_id=f"req-{i}",
                timestamp=now - (n - i) * 60,
                model="claude-opus-4-6" if i % 2 == 0 else "claude-haiku-4-5",
                input_tokens=10000 + i * 1000,
                sent_input_tokens=8000 + i * 800,
                cache_read_tokens=5000 if i % 3 == 0 else 0,
                cache_hit=i % 3 == 0,
                status=200,
            ))
        return auditor

    def test_record_and_get_recent(self):
        auditor = self._make_auditor(5)
        recent = auditor.get_recent(3)
        assert len(recent) == 3
        assert recent[-1].request_id == "req-4"

    def test_get_recent_all(self):
        auditor = self._make_auditor(5)
        recent = auditor.get_recent(100)
        assert len(recent) == 5

    def test_filter_by_model(self):
        auditor = self._make_auditor(5)
        results = auditor.filter(model="opus")
        assert all("opus" in r.model for r in results)

    def test_filter_by_request_id(self):
        auditor = self._make_auditor(5)
        results = auditor.filter(request_id="req-2")
        assert len(results) == 1
        assert results[0].request_id == "req-2"

    def test_filter_by_since(self):
        auditor = self._make_auditor(5)
        # Get only last 2 minutes
        since = time.time() - 120
        results = auditor.filter(since=since)
        assert len(results) >= 1

    def test_stats(self):
        auditor = self._make_auditor(5)
        stats = auditor.stats()
        assert stats["total"] == 5
        assert stats["cache_hits"] >= 1
        assert stats["avg_savings"] >= 0
        assert stats["total_savings"] >= 0
        assert "cache_hit_pct" in stats

    def test_stats_empty(self):
        auditor = RequestAuditor()
        stats = auditor.stats()
        assert stats["total"] == 0
        assert stats["avg_savings"] == 0.0

    def test_bounded_memory(self):
        auditor = RequestAuditor(max_recent=3)
        for i in range(10):
            auditor.record(RequestAudit(request_id=f"req-{i}"))
        recent = auditor.get_recent(100)
        assert len(recent) == 3
        assert recent[-1].request_id == "req-9"

    def test_csv_export(self):
        auditor = self._make_auditor(3)
        csv = auditor.to_csv()
        lines = csv.strip().split("\n")
        assert len(lines) == 4  # header + 3 data rows
        assert "request_id" in lines[0]
        assert "compression_savings_usd" in lines[0]

    def test_csv_filtered(self):
        auditor = self._make_auditor(5)
        filtered = auditor.filter(model="opus")
        csv = auditor.to_csv(filtered)
        lines = csv.strip().split("\n")
        # header + opus records only
        assert len(lines) >= 2


class TestAuditReport:
    """Test format_audit_report output."""

    def test_format_report_basic(self):
        records = [
            RequestAudit(
                request_id="req-1",
                model="claude-opus-4-6",
                input_tokens=10000,
                sent_input_tokens=8000,
                output_tokens=500,
                timestamp=time.time(),
                status=200,
            )
        ]
        report = format_audit_report(records)
        assert "TokenPak Request Audit" in report
        assert "Request #1" in report
        assert "claude-opus-4-6" in report
        assert "Without TokenPak" in report
        assert "With TokenPak" in report
        assert "SAVED" in report

    def test_format_report_cache_hit(self):
        records = [
            RequestAudit(
                model="claude-opus-4-6",
                input_tokens=10000,
                sent_input_tokens=10000,
                cache_read_tokens=50000,
                cache_hit=True,
                timestamp=time.time(),
            )
        ]
        report = format_audit_report(records)
        assert "CACHE HIT" in report
        assert "Cache" in report

    def test_format_report_empty(self):
        report = format_audit_report([])
        assert "No requests found" in report

    def test_format_report_multiple(self):
        records = [
            RequestAudit(model="claude-opus-4-6", input_tokens=5000, sent_input_tokens=4000, timestamp=time.time()),
            RequestAudit(model="claude-haiku-4-5", input_tokens=3000, sent_input_tokens=2500, timestamp=time.time()),
        ]
        report = format_audit_report(records)
        assert "Request #1" in report
        assert "Request #2" in report
