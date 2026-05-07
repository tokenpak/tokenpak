"""
tests.test_compression_ratio_regression
========================================

Regression tests for TokenPak compression ratios.

Verifies that compression thresholds don't degrade, determinism holds,
and lossless/lossy guarantees are met.
"""


import pytest

pytest.importorskip("tokenpak.capsule.builder", reason="module not available in current build")
import pytest

from tokenpak.capsule.builder import CapsuleBuilder, _compress_text

# ==========================================
# Realistic Payloads (for ratio testing)
# Payloads use double newlines (markdown paragraph breaks)
# so compression actually applies at the paragraph level
# ==========================================

# ~500 chars: brief prose
PAYLOAD_500 = """The quick brown fox jumps over the lazy dog. This is a simple test payload that should demonstrate compression behavior on shorter texts. While it may not achieve significant compression due to its limited size, it should still maintain deterministic output across multiple runs.

The paragraph structure and sentence boundaries should be preserved in the compressed form. Additional paragraphs help test the paragraph-level compression algorithm.""".strip()

# ~2000 chars: moderate prose (should compress well)
PAYLOAD_2000 = """TokenPak is a comprehensive token compression and optimization framework designed to reduce the cost of large language model API calls. By intelligently compressing context windows and applying sophisticated tokenization strategies, TokenPak helps organizations minimize their LLM expenditures while maintaining semantic fidelity. The framework is production-ready and can handle enterprise-scale workloads.

The system operates through several key components that work together seamlessly. First, the capsule builder identifies verbose historical context blocks and applies deterministic compression. Compression targets paragraph-level text while preserving structure-bearing elements like headers and code blocks. This ensures that the compressed output remains readable and useful to the model.

Second, the tokenizer layer applies advanced tokenization strategies that account for subword boundaries and special tokens. This allows for more accurate token counting and better compression. Token budgets are managed dynamically based on usage patterns and configured thresholds.

Third, the routing system intelligently selects which model should process a given request based on token budget and latency requirements. This multi-tier approach allows for cost-effective processing without sacrificing quality or user experience.""".strip()

# ~5000 chars: verbose documentation
PAYLOAD_5000 = """TokenPak is a production-grade token compression and budget management framework for large language model applications. It provides transparent, deterministic compression of context blocks while maintaining semantic fidelity. The system is designed for zero-configuration deployment and can be integrated into existing LLM pipelines with minimal changes.

Architecture overview: The Capsule Builder is the core compression engine. It processes incoming request bodies and identifies eligible text blocks for compression. Compression is applied only to messages outside the "hot window" (the most recent N messages), ensuring that the current conversation context remains untouched. This design pattern prevents loss of critical recent context while aggressively compressing historical information that is less frequently referenced.

The compression algorithm operates at the paragraph level, preserving all structure-bearing elements like headers, code blocks, lists, and blockquotes. Prose paragraphs are deterministically compressed using whitespace normalization, sentence-aware truncation, and word-boundary truncation as fallback. Each compressed block is wrapped in a capsule envelope that includes metadata about the compression ratio and character deltas. This allows downstream systems to understand the compression characteristics without needing to re-analyze the data.

The Budgeter component tracks token usage across requests and maintains cumulative budgets for cost control. It supports both absolute budgets (maximum total tokens) and ratio-based budgets (maximum cost multiplier). When a request would exceed budget, the Budgeter can automatically trigger compression or routing to cheaper models. The Budgeter maintains historical statistics that can be used for capacity planning and cost forecasting.

The Router selects the optimal model for a given request based on token budget, latency requirements, and model capabilities. It maintains a registry of available models and their characteristics, allowing for intelligent routing decisions. The routing decision can be cached to reduce latency and computation overhead.

Features include automatic format detection, support for both JSON and plaintext payloads, integration with popular LLM providers, comprehensive logging and monitoring, and flexible configuration through environment variables or programmatic APIs.""".strip()

# ~10000 chars: very verbose content (duplicate 5000 twice)
PAYLOAD_10000 = (PAYLOAD_5000 + "\n\n" + PAYLOAD_5000).strip()


# ==========================================
# Test Suite
# ==========================================


class TestCompressionRatios:
    """Verify compression ratios meet acceptable thresholds."""

    def test_compress_500_char_payload(self):
        """Small payloads may not compress significantly."""
        result = _compress_text(PAYLOAD_500)
        ratio = len(result) / len(PAYLOAD_500)
        # Small payloads are harder to compress; target <= 0.95 (5% reduction)
        assert ratio <= 0.95, f"500-char compression ratio {ratio} exceeds 0.95"

    def test_compress_2000_char_payload(self):
        """Medium payloads should achieve >=15% compression."""
        result = _compress_text(PAYLOAD_2000)
        ratio = len(result) / len(PAYLOAD_2000)
        # Expect at least 15% reduction (ratio <= 0.85)
        assert ratio <= 0.85, f"2000-char compression ratio {ratio} exceeds 0.85 (15% reduction)"

    def test_compress_5000_char_payload(self):
        """Larger payloads should compress well."""
        result = _compress_text(PAYLOAD_5000)
        ratio = len(result) / len(PAYLOAD_5000)
        # Expect at least 20% reduction (ratio <= 0.80)
        assert ratio <= 0.80, f"5000-char compression ratio {ratio} exceeds 0.80 (20% reduction)"

    def test_compress_10000_char_payload(self):
        """Very large payloads should compress very well."""
        result = _compress_text(PAYLOAD_10000)
        ratio = len(result) / len(PAYLOAD_10000)
        # Expect at least 25% reduction (ratio <= 0.75)
        assert ratio <= 0.75, f"10000-char compression ratio {ratio} exceeds 0.75 (25% reduction)"


class TestCompressionDeterminism:
    """Verify compression is deterministic."""

    @pytest.mark.parametrize(
        "payload",
        [PAYLOAD_500, PAYLOAD_2000, PAYLOAD_5000, PAYLOAD_10000],
        ids=["500", "2000", "5000", "10000"],
    )
    def test_deterministic_compression(self, payload):
        """Same input always produces identical output."""
        result1 = _compress_text(payload)
        result2 = _compress_text(payload)
        result3 = _compress_text(payload)
        assert result1 == result2 == result3, "Compression output is not deterministic"


class TestCapsuleEnvelope:
    """Verify capsule wrapping and ratio reporting."""

    def test_capsule_builder_default_disabled(self):
        """CapsuleBuilder is disabled by default."""
        import json

        builder = CapsuleBuilder()
        request = {
            "messages": [
                {"role": "user", "content": PAYLOAD_2000},
            ]
        }
        body_bytes = json.dumps(request).encode("utf-8")
        new_body, stats = builder.process(body_bytes)

        assert new_body == body_bytes, "Expected no-op when disabled"
        assert stats["skipped"] is True

    def test_capsule_builder_enabled(self):
        """Enabled CapsuleBuilder wraps eligible blocks outside hot window."""
        import json

        # hot_window=1 means last 1 message is untouched;
        # the first message gets capsulized
        builder = CapsuleBuilder(enabled=True, min_block_chars=400, hot_window=1)
        request = {
            "messages": [
                {"role": "system", "content": PAYLOAD_2000},  # Outside hot window
                {"role": "user", "content": "Hello"},  # Inside hot window (last 1)
            ]
        }
        body_bytes = json.dumps(request).encode("utf-8")
        new_body, stats = builder.process(body_bytes)

        assert stats["blocks_capsulized"] == 1, f"Expected 1 block to be capsulized, got {stats['blocks_capsulized']}"
        assert stats["ratio"] < 0.85, f"Capsule ratio {stats['ratio']} exceeds 0.85"

        # Verify the new body is valid JSON
        new_data = json.loads(new_body)
        assert "messages" in new_data

    def test_capsule_id_consistency(self):
        """Capsule IDs are derived from original content."""
        from tokenpak.capsule.builder import _capsule_id

        cid1 = _capsule_id(PAYLOAD_2000)
        cid2 = _capsule_id(PAYLOAD_2000)
        assert cid1 == cid2, "Capsule ID is not deterministic"

        # Different content should (very likely) have different IDs
        cid3 = _capsule_id("different content")
        assert cid1 != cid3, "Different content should have different capsule IDs"


class TestStructurePreservation:
    """Verify that compression preserves structure-bearing elements."""

    def test_headers_preserved(self):
        """Markdown headers should not be compressed."""
        text = "# Main Title\n\nSome long prose text that should be compressed and truncated at sentence boundaries to demonstrate paragraph-level compression working properly."
        result = _compress_text(text)
        assert "# Main Title" in result, "Header not preserved after compression"

    def test_code_blocks_preserved(self):
        """Code blocks should not be compressed."""
        text = "```python\ndef hello():\n    pass\n```\n\nSome very long prose text that should definitely be compressed and truncated to demonstrate paragraph-level compression working as expected for realistic payloads."
        result = _compress_text(text)
        assert "def hello():" in result, "Code block not preserved after compression"

    def test_lists_preserved(self):
        """List items should be preserved."""
        text = "- Item 1\n- Item 2\n- Item 3\n\nSome very long prose that should be compressed and truncated at sentence boundaries to demonstrate paragraph-level compression of realistic verbose content."
        result = _compress_text(text)
        assert "- Item 1" in result, "List items not preserved"
        assert "- Item 2" in result, "List items not preserved"


class TestRegressionDetection:
    """Verify that compression changes are detected."""

    def test_output_never_larger_than_input(self):
        """Compression output should never be larger than input."""
        for payload in [PAYLOAD_500, PAYLOAD_2000, PAYLOAD_5000, PAYLOAD_10000]:
            result = _compress_text(payload)
            ratio = len(result) / len(payload)
            # Compression should never make output larger
            assert ratio <= 1.0, f"Compression failed (output larger than input, ratio={ratio})"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
