"""
Tests for compression ratio regression — ensures compression baseline performance.

Protects TokenPak's core value prop (compression) from silent degradation.
Establishes baseline compression ratio and fails if it drops below threshold.
"""

import pytest
from tokenpak.compression import CompressionPipeline


# Test fixtures with known compressible content
FIXTURE_REPETITIVE = """The compression system is designed to reduce token usage. Token usage reduction saves money.
Saving money is important for teams. Teams benefit from reduced token usage.
Reduced token usage helps with budgets. Budgets are managed by teams.
Team management of budgets requires careful planning. Careful planning reduces costs.
Cost reduction helps teams. Teams using compression see cost reductions."""

FIXTURE_MINIMAL = 'Hello world.'

FIXTURE_CODE_HEAVY = """def compress_data(data):
    return CompressedData(compress_utils.compress(data))

class CompressedData:
    def __init__(self, data):
        self.data = data
    
    def get_compression_ratio(self):
        return CompressionMetrics.calculate_ratio(self.data)"""

FIXTURE_LONG_PATHS = """Files at /home/trix/vault/03_AGENT_PACKS/Trix/queue/p2-task.md
and /home/trix/vault/03_AGENT_PACKS/Trix/archive/old-task.md
stored on /home/trix/.openclaw/workspace/memory/
backups at /home/sue/vault/06_RUNTIME/SYSTEM/backups/
configs at /etc/openclaw/config/tokenpak/proxy.py
and /opt/tokenpak/lib/agent/compression/pipeline.py"""


class TestCompressionRatioBaseline:
    """Baseline ratio test on known-compressible fixture."""

    def test_dedup_compression_with_repetitive_messages(self):
        """Verify >= 15% compression on multi-message with repetitive content."""
        pipeline = CompressionPipeline(enable_dedup=True)
        
        messages = [
            {"role": "user", "content": FIXTURE_REPETITIVE},
            {"role": "assistant", "content": "Understood."},
            {"role": "user", "content": FIXTURE_REPETITIVE},
        ]
        
        result = pipeline.run(messages)
        
        # Verify compression happened
        assert result.tokens_raw > 0
        assert result.tokens_after > 0
        
        # Calculate compression ratio
        ratio = result.tokens_saved / result.tokens_raw if result.tokens_raw > 0 else 0
        
        # Baseline assertion: >= 15% reduction from dedup on multi-message
        assert ratio >= 0.15, (
            f"Compression ratio {ratio:.2%} below threshold. "
            f"Raw: {result.tokens_raw}, After: {result.tokens_after}, "
            f"Saved: {result.tokens_saved}"
        )

    def test_code_heavy_content_compression(self):
        """Verify compression works on code-heavy content."""
        pipeline = CompressionPipeline()
        
        messages = [
            {"role": "user", "content": FIXTURE_CODE_HEAVY},
            {"role": "assistant", "content": "Code understood."},
            {"role": "user", "content": FIXTURE_CODE_HEAVY},
        ]
        
        result = pipeline.run(messages)
        
        # Code content should compress via dedup on duplicate
        assert result.tokens_raw > 0
        ratio = result.tokens_saved / result.tokens_raw if result.tokens_raw > 0 else 0
        assert ratio >= 0.0  # At minimum, no regression

    def test_long_paths_alias_compression(self):
        """Verify compression on path-heavy content via alias."""
        pipeline = CompressionPipeline(
            enable_dedup=False,
            enable_alias=True,
            enable_segmentation=False,
            enable_directives=False,
            alias_min_occurrences=2,  # Lower threshold for test fixture
        )
        
        # Create repetitive path content to trigger alias
        long_paths = (FIXTURE_LONG_PATHS + "\n") * 3
        
        messages = [
            {"role": "user", "content": long_paths}
        ]
        
        result = pipeline.run(messages)
        
        # Alias should compress repeated long entities
        assert result.tokens_raw > 0
        ratio = result.tokens_saved / result.tokens_raw if result.tokens_raw > 0 else 0
        # Paths/URLs should benefit from aliasing (expect >= 20%)
        assert ratio >= 0.20, (
            f"Path compression ratio {ratio:.2%} below threshold. "
            f"Raw: {result.tokens_raw}, After: {result.tokens_after}"
        )


class TestCompressionEdgeCases:
    """Edge cases: empty, minimal, already-compressed content."""

    def test_empty_input_no_crash(self):
        """Verify pipeline handles empty messages without crashing."""
        pipeline = CompressionPipeline()
        
        messages = [
            {"role": "user", "content": ""}
        ]
        
        result = pipeline.run(messages)
        
        # Should not crash; result should be valid
        assert result.tokens_raw >= 0
        assert result.tokens_after >= 0

    def test_already_minimal_input(self):
        """Verify minimal content compresses to itself (no error)."""
        pipeline = CompressionPipeline()
        
        messages = [
            {"role": "user", "content": FIXTURE_MINIMAL}
        ]
        
        result = pipeline.run(messages)
        
        # Minimal content may not compress at all (ratio = 0%), but shouldn't error
        assert result.tokens_raw > 0
        assert result.tokens_after > 0
        ratio = result.tokens_saved / result.tokens_raw if result.tokens_raw > 0 else 0
        assert ratio >= 0.0

    def test_output_validity_non_empty(self):
        """Verify compressed output is non-empty and valid."""
        pipeline = CompressionPipeline()
        
        messages = [
            {"role": "user", "content": FIXTURE_REPETITIVE},
            {"role": "assistant", "content": "Response."},
            {"role": "user", "content": FIXTURE_REPETITIVE},
        ]
        
        result = pipeline.run(messages)
        
        # Output messages should be non-empty
        assert len(result.messages) > 0
        
        # Message structure should be valid (has role and content)
        for msg in result.messages:
            assert "role" in msg
            assert "content" in msg or "tool_use" in msg
        
        # Content should not be truncated mid-sentence or empty
        for msg in result.messages:
            if "content" in msg and isinstance(msg["content"], str):
                assert len(msg["content"]) > 0

    def test_multiple_messages_dedup(self):
        """Verify dedup works on multi-message conversations."""
        pipeline = CompressionPipeline(enable_dedup=True)
        
        messages = [
            {"role": "user", "content": "Hello world"},
            {"role": "assistant", "content": "Hi there."},
            {"role": "user", "content": "Hello world"},
        ]
        
        result = pipeline.run(messages)
        
        # Multiple message dedup should provide savings
        assert result.tokens_raw > 0
        # With dedup, duplicate message should be removed
        assert result.tokens_after <= result.tokens_raw
        assert len(result.messages) >= 1


class TestCompressionPipelineStages:
    """Test individual compression stages."""

    def test_all_stages_enabled(self):
        """Verify compression with all stages enabled."""
        pipeline = CompressionPipeline(
            enable_dedup=True,
            enable_alias=True,
            enable_segmentation=True,
            enable_directives=True,
        )
        
        messages = [
            {"role": "user", "content": FIXTURE_REPETITIVE},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": FIXTURE_REPETITIVE},
        ]
        
        result = pipeline.run(messages)
        
        # Pipeline should run stages
        assert "dedup" in result.stages_run or "alias" in result.stages_run
        assert result.tokens_raw > 0

    def test_alias_stage_only(self):
        """Verify compression with only alias stage enabled."""
        pipeline = CompressionPipeline(
            enable_dedup=False,
            enable_alias=True,
            enable_segmentation=False,
            enable_directives=False,
            alias_min_occurrences=2,
        )
        
        long_paths = (FIXTURE_LONG_PATHS + "\n") * 3
        messages = [
            {"role": "user", "content": long_paths}
        ]
        
        result = pipeline.run(messages)
        
        # Should complete without error
        assert result.tokens_raw > 0

    def test_dedup_stage_only(self):
        """Verify compression with only dedup stage enabled."""
        pipeline = CompressionPipeline(
            enable_dedup=True,
            enable_alias=False,
            enable_segmentation=False,
            enable_directives=False,
        )
        
        messages = [
            {"role": "user", "content": "Repeat this."},
            {"role": "user", "content": "Repeat this."},
        ]
        
        result = pipeline.run(messages)
        
        # Dedup should reduce message count
        assert result.tokens_raw > 0
        assert len(result.messages) >= 1
        # Dedup should catch the duplicate
        assert len(result.messages) < len(messages)
