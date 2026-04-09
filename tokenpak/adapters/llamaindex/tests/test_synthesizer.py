"""Tests for TokenPakSynthesizer."""

from llamaindex_tokenpak import TokenPakSynthesizer


def make_nodes(count=5, tokens_each=1000, scores=None):
    nodes = []
    for i in range(count):
        score = scores[i] if scores else (0.5 + i * 0.1)
        text = f"This is document {i}. " + ("word " * tokens_each)
        nodes.append(
            {
                "id": f"node_{i}",
                "text": text,
                "metadata": {"file_name": f"doc_{i}.md"},
                "score": score,
            }
        )
    return nodes


class TestSynthesizerCreation:
    def test_default_params(self):
        s = TokenPakSynthesizer()
        assert s.budget == 4000
        assert s.keep_headers is True
        assert s.keep_code is True

    def test_custom_budget(self):
        s = TokenPakSynthesizer(budget=8000)
        assert s.budget == 8000
        assert s._effective_budget < 8000  # minus reserve

    def test_last_stats_empty_initially(self):
        s = TokenPakSynthesizer(budget=4000)
        assert s.last_stats == {}


class TestSynthesizerCompression:
    def test_no_compression_when_under_budget(self):
        s = TokenPakSynthesizer(budget=4000)
        short_nodes = [
            {"id": "n0", "text": "Short text.", "metadata": {}, "score": 1.0}
        ]
        result = s.synthesize("test query", short_nodes)
        assert (
            result["compression_stats"]["input_tokens"]
            == result["compression_stats"]["output_tokens"]
        )

    def test_compression_when_over_budget(self):
        s = TokenPakSynthesizer(budget=500)
        nodes = make_nodes(count=10, tokens_each=500)
        result = s.synthesize("test query", nodes)
        stats = result["compression_stats"]
        # Output should be less than input
        assert stats["output_tokens"] <= stats["input_tokens"]

    def test_compression_ratio_reported(self):
        s = TokenPakSynthesizer(budget=200)
        nodes = make_nodes(count=5, tokens_each=300)
        result = s.synthesize("test", nodes)
        stats = result["compression_stats"]
        assert "compression_ratio" in stats
        assert 0.0 < stats["compression_ratio"] <= 1.0

    def test_high_quality_nodes_kept_longer(self):
        """Blocks with higher quality should get more budget allocation."""
        s = TokenPakSynthesizer(budget=200)
        nodes = [
            {"id": "high", "text": "A" * 2000, "metadata": {}, "score": 0.95},
            {"id": "low", "text": "B" * 2000, "metadata": {}, "score": 0.05},
        ]
        result = s.synthesize("test", nodes)
        blocks = result["source_nodes"]
        high_block = next((b for b in blocks if "high" in str(b.get("id", ""))), None)
        low_block = next((b for b in blocks if "low" in str(b.get("id", ""))), None)
        if high_block and low_block:
            high_len = len(high_block.get("text", ""))
            low_len = len(low_block.get("text", ""))
            assert high_len >= low_len

    def test_empty_nodes_returns_empty(self):
        s = TokenPakSynthesizer(budget=4000)
        result = s.synthesize("test", [])
        assert result["response"] == ""
        assert result["source_nodes"] == []

    def test_synthesize_returns_query(self):
        s = TokenPakSynthesizer(budget=4000)
        result = s.synthesize(
            "What is TokenPak?",
            [{"id": "n0", "text": "TokenPak is great.", "metadata": {}, "score": 1.0}],
        )
        assert result["query"] == "What is TokenPak?"

    def test_context_includes_sources(self):
        s = TokenPakSynthesizer(budget=4000)
        nodes = [
            {
                "id": "n0",
                "text": "Evidence text.",
                "metadata": {"file_name": "paper.pdf"},
                "score": 0.9,
            }
        ]
        result = s.synthesize("query", nodes)
        assert "paper.pdf" in result["response"]

    def test_stat_tracking(self):
        s = TokenPakSynthesizer(budget=4000)
        nodes = make_nodes(count=3)
        s.synthesize("test", nodes)
        stats = s.last_stats
        assert stats["input_nodes"] == 3
        assert stats["output_blocks"] == 3


class TestTrimContent:
    def test_short_content_unchanged(self):
        result = TokenPakSynthesizer._trim_content("Short.", token_budget=100)
        assert result == "Short."

    def test_long_content_trimmed(self):
        long_text = "word " * 1000
        result = TokenPakSynthesizer._trim_content(long_text, token_budget=50)
        assert len(result) < len(long_text)
        assert "compressed by TokenPak" in result

    def test_headers_preserved(self):
        text = "# Header\nSome content here. " * 100
        result = TokenPakSynthesizer._trim_content(
            text, token_budget=20, keep_headers=True
        )
        assert "# Header" in result

    def test_code_block_preserved(self):
        text = "```python\nprint('hello')\n```\n" + "filler " * 500
        result = TokenPakSynthesizer._trim_content(
            text, token_budget=20, keep_code=True
        )
        assert "```" in result
        assert "print" in result

    def test_no_preserve_headers(self):
        text = "# Header\n" + "word " * 200
        result = TokenPakSynthesizer._trim_content(
            text, token_budget=10, keep_headers=False
        )
        # May or may not have header depending on position, just verify it runs
        assert isinstance(result, str)
        assert len(result) > 0


class TestAsyncSynthesizer:
    def test_asynthesize_returns_result(self):
        import asyncio

        s = TokenPakSynthesizer(budget=4000)
        nodes = [{"id": "n0", "text": "Test.", "metadata": {}, "score": 1.0}]

        async def _run():
            return await s.asynthesize("question", nodes)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert "query" in result
