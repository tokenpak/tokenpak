"""TokenPak Pipeline Trace — Capture compression pipeline execution for demo/debugging."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class StageTrace:
    """Trace for a single pipeline stage."""

    name: str  # capsule, segmentizer, recipe_engine, slot_filler, validation_gate
    enabled: bool
    input_tokens: int
    output_tokens: int
    tokens_delta: int
    details: Dict[str, Any]
    duration_ms: float


@dataclass
class PipelineTrace:
    """Complete trace for one request through the compression pipeline."""

    request_id: str
    timestamp: datetime
    input_tokens: int
    stages: List[StageTrace]
    output_tokens: int
    tokens_saved: int
    cost_saved: float
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat(),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tokens_saved": self.tokens_saved,
            "cost_saved": self.cost_saved,
            "duration_ms": self.duration_ms,
            "stages": [
                {
                    "name": s.name,
                    "enabled": s.enabled,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "tokens_delta": s.tokens_delta,
                    "duration_ms": s.duration_ms,
                    "details": s.details,
                }
                for s in self.stages
            ],
        }


class TraceStorage:
    """In-memory storage for pipeline traces (FIFO, last N traces)."""

    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.traces: deque = deque(maxlen=max_size)
        self.lock = threading.Lock()

    def add(self, trace: PipelineTrace) -> None:
        """Add a trace to storage."""
        with self.lock:
            self.traces.append(trace)

    def get_last(self) -> Optional[PipelineTrace]:
        """Get the most recent trace."""
        with self.lock:
            if self.traces:
                return self.traces[-1]
        return None

    def get_by_id(self, request_id: str) -> Optional[PipelineTrace]:
        """Get a trace by request ID."""
        with self.lock:
            for trace in self.traces:
                if trace.request_id == request_id:
                    return trace
        return None

    def get_all(self) -> List[PipelineTrace]:
        """Get all stored traces."""
        with self.lock:
            return list(self.traces)


# Global singleton
_trace_storage: Optional[TraceStorage] = None


def get_trace_storage() -> TraceStorage:
    """Get or create the global trace storage."""
    global _trace_storage
    if _trace_storage is None:
        _trace_storage = TraceStorage()
    return _trace_storage
