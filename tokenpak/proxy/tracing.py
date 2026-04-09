"""
TokenPak proxy pipeline tracing — StageTrace, PipelineTrace, TraceStorage.

Extracted from tokenpak/runtime/proxy.py (L1-607 extraction, phase 1a).
"""

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Pipeline Trace — captures per-request pipeline execution details
# ---------------------------------------------------------------------------
class _CompressionTimeout(Exception):
    """Raised internally when the compression pipeline exceeds MAX_COMPRESSION_TIME_MS."""


@dataclass
class StageTrace:
    """Trace for a single pipeline stage."""

    name: str  # capsule, segmentizer, recipe_engine, compaction, vault_injection, validation_gate
    enabled: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_delta: int = 0
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineTrace:
    """Complete trace for a request through the pipeline."""

    request_id: str
    timestamp: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_saved: int = 0
    cost_saved: float = 0.0
    total_cost: float = 0.0
    duration_ms: float = 0.0
    stages: List[StageTrace] = field(default_factory=list)
    status: str = "pending"  # pending, complete, error

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.stages]
        return d


class TraceStorage:
    """Thread-safe storage for recent pipeline traces."""

    def __init__(self, max_traces: int = 10):
        self._traces: deque = deque(maxlen=max_traces)
        self._lock = threading.Lock()
        self._by_id: Dict[str, PipelineTrace] = {}

    def store(self, trace: PipelineTrace):
        """Store a completed trace."""
        with self._lock:
            self._traces.append(trace)
            self._by_id[trace.request_id] = trace
            # Clean up old entries from _by_id
            if len(self._by_id) > len(self._traces) * 2:
                valid_ids = {t.request_id for t in self._traces}
                self._by_id = {k: v for k, v in self._by_id.items() if k in valid_ids}

    def get_last(self) -> Optional[PipelineTrace]:
        """Get the most recent trace."""
        with self._lock:
            return self._traces[-1] if self._traces else None

    def get_by_id(self, request_id: str) -> Optional[PipelineTrace]:
        """Get a specific trace by ID."""
        with self._lock:
            return self._by_id.get(request_id)

    def get_all(self) -> List[PipelineTrace]:
        """Get all stored traces."""
        with self._lock:
            return list(self._traces)


# Global trace storage singleton
TRACE_STORAGE = TraceStorage(max_traces=10)
