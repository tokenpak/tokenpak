"""
Audit trail recording for TokenPak compilation decisions.

Logs what blocks were removed, why, and performance metrics.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Literal
from enum import Enum


class CompressionMethod(str, Enum):
    """Compression strategies."""
    EXTRACTIVE = "extractive"
    LLM = "llm"
    TRUNCATION = "truncation"
    DEDUPLICATION = "deduplication"
    SEMANTIC = "semantic"


class BlockType(str, Enum):
    """Block types in context."""
    INSTRUCTION = "instruction"
    KNOWLEDGE = "knowledge"
    EVIDENCE = "evidence"
    EXAMPLE = "example"
    CUSTOM = "custom"


@dataclass
class BlockAudit:
    """Audit record for a single block decision."""
    block_id: str
    block_type: BlockType
    original_size: int
    final_size: int
    action: Literal["kept", "removed", "compacted", "deduplicated"]
    compression_method: Optional[CompressionMethod] = None
    reason: str = ""
    similarity_to_kept: Optional[float] = None  # For dedup


@dataclass
class CompileAudit:
    """Audit trail for a /compile request."""
    request_id: str
    timestamp: str  # ISO 8601
    
    # Input
    input_block_count: int
    input_blocks_by_type: Dict[BlockType, int]
    input_total_size: int
    
    # Output
    output_block_count: int
    output_blocks_by_type: Dict[BlockType, int]
    output_total_size: int
    
    # Decisions
    blocks_audited: List[BlockAudit] = field(default_factory=list)
    compression_methods_used: Dict[CompressionMethod, int] = field(default_factory=dict)
    
    # Latency breakdown (ms)
    parse_latency_ms: float = 0.0
    compile_latency_ms: float = 0.0
    render_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    
    # Summary
    compression_ratio: float = 1.0
    tokens_removed: int = 0
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        data = asdict(self)
        # Convert enums to strings
        data["blocks_audited"] = [
            {
                **asdict(block),
                "block_type": block.block_type.value,
                "action": block.action,
                "compression_method": block.compression_method.value if block.compression_method else None
            }
            for block in self.blocks_audited
        ]
        data["input_blocks_by_type"] = {
            k.value: v for k, v in data["input_blocks_by_type"].items()
        }
        data["output_blocks_by_type"] = {
            k.value: v for k, v in data["output_blocks_by_type"].items()
        }
        data["compression_methods_used"] = {
            k.value: v for k, v in data["compression_methods_used"].items()
        }
        return data
    
    def to_json(self) -> str:
        """Convert to JSON."""
        return json.dumps(self.to_dict(), default=str)


@dataclass
class CacheAudit:
    """Audit trail for /cache/* requests."""
    request_id: str
    timestamp: str  # ISO 8601
    operation: Literal["get", "set", "invalidate", "clear"]
    block_id: Optional[str] = None
    cache_hit: bool = False
    cached_value_size: int = 0
    ttl_seconds: Optional[int] = None
    message: str = ""


@dataclass
class MetricsAudit:
    """Audit trail for /metrics requests."""
    request_id: str
    timestamp: str  # ISO 8601
    aggregation_window: str  # e.g., "1h", "24h"
    data_points_returned: int = 0
    metrics_included: List[str] = field(default_factory=list)


def _get_iso_timestamp() -> str:
    """Get ISO 8601 timestamp with timezone."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_compile_audit(
    request_id: str,
    input_block_count: int,
    input_blocks_by_type: Dict[BlockType, int],
    input_total_size: int,
) -> CompileAudit:
    """Create compile audit record."""
    return CompileAudit(
        request_id=request_id,
        timestamp=_get_iso_timestamp(),
        input_block_count=input_block_count,
        input_blocks_by_type=input_blocks_by_type,
        input_total_size=input_total_size,
        output_block_count=0,
        output_blocks_by_type={},
        output_total_size=0,
    )


def create_cache_audit(
    request_id: str,
    operation: Literal["get", "set", "invalidate", "clear"],
    block_id: Optional[str] = None,
) -> CacheAudit:
    """Create cache audit record."""
    return CacheAudit(
        request_id=request_id,
        timestamp=_get_iso_timestamp(),
        operation=operation,
        block_id=block_id,
    )


def create_metrics_audit(
    request_id: str,
    aggregation_window: str,
) -> MetricsAudit:
    """Create metrics audit record."""
    return MetricsAudit(
        request_id=request_id,
        timestamp=_get_iso_timestamp(),
        aggregation_window=aggregation_window,
    )
