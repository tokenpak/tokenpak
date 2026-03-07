"""Tests for langfuse_tokenpak.analytics"""

import pytest
from langfuse_tokenpak.analytics import TokenPakAnalytics


def make_blocks(specs):
    return [
        {"id": f"b{i}", "type": btype, "tokens": tok, "priority": "medium", "compacted": False}
        for i, (btype, tok) in enumerate(specs)
    ]


class TestTokenPakAnalytics:
    def test_single_pack(self):
        analytics = TokenPakAnalytics()
        blocks = make_blocks([("knowledge", 400), ("instructions", 100)])
        analytics.record_pack(blocks, budget=8000, raw_tokens=1000)

        report = analytics.get_report()
        assert report["pack_count"] == 1
        assert report["total_tokens_after"] == 500
        assert report["total_tokens_before"] == 1000
        assert report["tokens_saved"] == 500
        assert report["savings_percent"] == 50.0
        assert report["compression_ratio"] == 0.5

    def test_multiple_packs(self):
        analytics = TokenPakAnalytics()
        analytics.record_pack(make_blocks([("knowledge", 200)]), raw_tokens=400)
        analytics.record_pack(make_blocks([("evidence", 100)]), raw_tokens=200)

        report = analytics.get_report()
        assert report["pack_count"] == 2
        assert report["total_tokens_after"] == 300
        assert report["total_tokens_before"] == 600

    def test_no_raw_tokens(self):
        analytics = TokenPakAnalytics()
        analytics.record_pack(make_blocks([("instructions", 150)]))
        report = analytics.get_report()
        # Without raw_tokens, assumes no compression
        assert report["compression_ratio"] == 1.0
        assert report["tokens_saved"] == 0

    def test_top_blocks(self):
        analytics = TokenPakAnalytics()
        blocks = make_blocks([(f"type_{i}", i * 100) for i in range(1, 15)])
        analytics.record_pack(blocks)
        report = analytics.get_report()
        assert len(report["top_blocks"]) <= 10
        # Top block should have highest tokens
        assert report["top_blocks"][0]["tokens"] >= report["top_blocks"][-1]["tokens"]

    def test_type_distribution(self):
        analytics = TokenPakAnalytics()
        blocks = make_blocks([("knowledge", 300), ("knowledge", 200), ("evidence", 100)])
        analytics.record_pack(blocks)
        report = analytics.get_report()
        assert "knowledge" in report["type_distribution"]
        assert report["type_distribution"]["knowledge"]["tokens"] == 500
        assert report["type_distribution"]["knowledge"]["count"] == 2

    def test_reset(self):
        analytics = TokenPakAnalytics()
        analytics.record_pack(make_blocks([("instructions", 100)]))
        analytics.reset()
        report = analytics.get_report()
        assert report["pack_count"] == 0
        assert report["total_tokens_after"] == 0
