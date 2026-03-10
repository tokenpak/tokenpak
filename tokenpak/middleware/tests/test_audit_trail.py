"""
Unit tests for audit trail module.
"""

import pytest
import json
from tokenpak.middleware.audit_trail import (
    CompileAudit,
    CacheAudit,
    MetricsAudit,
    BlockAudit,
    CompressionMethod,
    BlockType,
    create_compile_audit,
    create_cache_audit,
    create_metrics_audit,
)


class TestBlockAudit:
    """Test BlockAudit data class."""
    
    def test_block_audit_creation(self):
        """Test creating a block audit."""
        audit = BlockAudit(
            block_id="block-1",
            block_type=BlockType.INSTRUCTION,
            original_size=1000,
            final_size=500,
            action="compacted",
            compression_method=CompressionMethod.TRUNCATION,
            reason="Removed redundant examples",
        )
        
        assert audit.block_id == "block-1"
        assert audit.block_type == BlockType.INSTRUCTION
        assert audit.action == "compacted"


class TestCompileAudit:
    """Test CompileAudit data class."""
    
    def test_compile_audit_creation(self):
        """Test creating a compile audit."""
        audit = CompileAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            input_block_count=20,
            input_blocks_by_type={
                BlockType.INSTRUCTION: 5,
                BlockType.KNOWLEDGE: 10,
                BlockType.EVIDENCE: 5,
            },
            input_total_size=50000,
            output_block_count=15,
            output_blocks_by_type={
                BlockType.INSTRUCTION: 5,
                BlockType.KNOWLEDGE: 8,
                BlockType.EVIDENCE: 2,
            },
            output_total_size=35000,
        )
        
        assert audit.request_id == "req-123"
        assert audit.input_block_count == 20
        assert audit.output_block_count == 15
    
    def test_compile_audit_to_dict(self):
        """Test converting compile audit to dict."""
        audit = CompileAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            input_block_count=20,
            input_blocks_by_type={BlockType.INSTRUCTION: 5},
            input_total_size=50000,
            output_block_count=15,
            output_blocks_by_type={BlockType.INSTRUCTION: 5},
            output_total_size=35000,
            compression_methods_used={CompressionMethod.TRUNCATION: 3},
        )
        
        data = audit.to_dict()
        
        assert data["request_id"] == "req-123"
        assert data["input_block_count"] == 20
        assert "instruction" in data["input_blocks_by_type"]
    
    def test_compile_audit_to_json(self):
        """Test converting compile audit to JSON."""
        audit = CompileAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            input_block_count=20,
            input_blocks_by_type={BlockType.INSTRUCTION: 5},
            input_total_size=50000,
            output_block_count=15,
            output_blocks_by_type={BlockType.INSTRUCTION: 5},
            output_total_size=35000,
        )
        
        json_str = audit.to_json()
        data = json.loads(json_str)
        
        assert data["request_id"] == "req-123"
        assert data["input_block_count"] == 20
    
    def test_compile_audit_compression_ratio(self):
        """Test compression ratio calculation."""
        audit = CompileAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            input_block_count=20,
            input_blocks_by_type={},
            input_total_size=100000,
            output_block_count=15,
            output_blocks_by_type={},
            output_total_size=50000,
            compression_ratio=0.5,
        )
        
        assert audit.compression_ratio == 0.5


class TestCacheAudit:
    """Test CacheAudit data class."""
    
    def test_cache_audit_get(self):
        """Test cache get audit."""
        audit = CacheAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            operation="get",
            block_id="block-1",
            cache_hit=True,
            cached_value_size=1000,
        )
        
        assert audit.operation == "get"
        assert audit.cache_hit is True
    
    def test_cache_audit_set(self):
        """Test cache set audit."""
        audit = CacheAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            operation="set",
            block_id="block-1",
            ttl_seconds=3600,
        )
        
        assert audit.operation == "set"
        assert audit.ttl_seconds == 3600
    
    def test_cache_audit_invalidate(self):
        """Test cache invalidate audit."""
        audit = CacheAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            operation="invalidate",
            block_id="block-1",
        )
        
        assert audit.operation == "invalidate"


class TestMetricsAudit:
    """Test MetricsAudit data class."""
    
    def test_metrics_audit_creation(self):
        """Test creating a metrics audit."""
        audit = MetricsAudit(
            request_id="req-123",
            timestamp="2026-03-10T06:00:00Z",
            aggregation_window="24h",
            data_points_returned=1440,
            metrics_included=["compression_ratio", "latency", "blocks_removed"],
        )
        
        assert audit.aggregation_window == "24h"
        assert audit.data_points_returned == 1440
        assert "compression_ratio" in audit.metrics_included


class TestFactoryFunctions:
    """Test factory functions."""
    
    def test_create_compile_audit(self):
        """Test creating compile audit via factory."""
        audit = create_compile_audit(
            request_id="req-123",
            input_block_count=20,
            input_blocks_by_type={BlockType.INSTRUCTION: 5},
            input_total_size=50000,
        )
        
        assert audit.request_id == "req-123"
        assert audit.input_block_count == 20
        assert audit.output_block_count == 0  # Should be initialized
    
    def test_create_cache_audit(self):
        """Test creating cache audit via factory."""
        audit = create_cache_audit(
            request_id="req-123",
            operation="get",
            block_id="block-1",
        )
        
        assert audit.request_id == "req-123"
        assert audit.operation == "get"
        assert audit.block_id == "block-1"
    
    def test_create_metrics_audit(self):
        """Test creating metrics audit via factory."""
        audit = create_metrics_audit(
            request_id="req-123",
            aggregation_window="1h",
        )
        
        assert audit.request_id == "req-123"
        assert audit.aggregation_window == "1h"
