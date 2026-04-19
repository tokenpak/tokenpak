"""Integration point for proxy to capture and expose pipeline traces."""

import uuid
from datetime import datetime
from typing import List, Optional

from tokenpak.telemetry.pipeline_trace import PipelineTrace, StageTrace, get_trace_storage


class ProxyTraceCapture:
    """Helps proxy capture trace data as request flows through pipeline."""

    def __init__(self, request_id: Optional[str] = None):
        self.request_id = request_id or f"req-{uuid.uuid4().hex[:8]}"
        self.timestamp = datetime.now()
        self.input_tokens_raw = 0
        self.stages: List[StageTrace] = []
        self.output_tokens = 0
        self.tokens_saved = 0
        self.cost_saved = 0.0
        self.start_time = datetime.now()

    def record_capsule_stage(
        self,
        input_tokens: int,
        output_tokens: int,
        blocks_matched: int = 0,
        block_names: Optional[List[str]] = None,
        tokens_injected: int = 0,
        duration_ms: float = 0.0,
    ) -> None:
        """Record capsule/vault injection stage."""
        self.input_tokens_raw = input_tokens

        stage = StageTrace(
            name="capsule",
            enabled=tokens_injected > 0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_delta=tokens_injected,
            details={
                "blocks_matched": blocks_matched,
                "block_names": block_names or [],
                "tokens_injected": tokens_injected,
            },
            duration_ms=duration_ms,
        )
        self.stages.append(stage)

    def record_segmentizer_stage(
        self,
        input_tokens: int,
        output_tokens: int,
        segments_found: int = 0,
        compressible: int = 0,
        protected: int = 0,
        duration_ms: float = 0.0,
    ) -> None:
        """Record segmentizer analysis stage."""
        stage = StageTrace(
            name="segmentizer",
            enabled=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_delta=0,
            details={
                "segments_found": segments_found,
                "compressible": compressible,
                "protected": protected,
            },
            duration_ms=duration_ms,
        )
        self.stages.append(stage)

    def record_recipe_engine_stage(
        self,
        input_tokens: int,
        output_tokens: int,
        recipe_applied: str = "",
        rules_fired: int = 0,
        tokens_pruned: int = 0,
        duration_ms: float = 0.0,
    ) -> None:
        """Record recipe engine transformation stage."""
        tokens_delta = output_tokens - input_tokens
        stage = StageTrace(
            name="recipe_engine",
            enabled=tokens_pruned > 0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_delta=tokens_delta,
            details={
                "recipe_applied": recipe_applied,
                "rules_fired": rules_fired,
                "tokens_pruned": tokens_pruned,
            },
            duration_ms=duration_ms,
        )
        self.stages.append(stage)

    def record_slot_filler_stage(
        self,
        input_tokens: int,
        output_tokens: int,
        refs_resolved: int = 0,
        ref_names: Optional[List[str]] = None,
        tokens_saved: int = 0,
        duration_ms: float = 0.0,
    ) -> None:
        """Record slot/ref filler stage."""
        stage = StageTrace(
            name="slot_filler",
            enabled=refs_resolved > 0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_delta=-tokens_saved,
            details={
                "refs_resolved": refs_resolved,
                "ref_names": ref_names or [],
                "tokens_saved": tokens_saved,
            },
            duration_ms=duration_ms,
        )
        self.stages.append(stage)

    def record_validation_gate_stage(
        self,
        input_tokens: int,
        output_tokens: int,
        passed: bool = True,
        checks: Optional[List[str]] = None,
        duration_ms: float = 0.0,
    ) -> None:
        """Record validation gate stage."""
        stage = StageTrace(
            name="validation_gate",
            enabled=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_delta=0,
            details={
                "passed": passed,
                "checks": checks or [],
            },
            duration_ms=duration_ms,
        )
        self.stages.append(stage)
        self.output_tokens = output_tokens

    def finalize(self, cost_saved: float = 0.0) -> PipelineTrace:
        """Finalize the trace and calculate summary stats."""
        self.tokens_saved = max(0, self.input_tokens_raw - self.output_tokens)
        self.cost_saved = cost_saved

        trace = PipelineTrace(
            request_id=self.request_id,
            timestamp=self.timestamp,
            input_tokens=self.input_tokens_raw,
            stages=self.stages,
            output_tokens=self.output_tokens,
            tokens_saved=self.tokens_saved,
            cost_saved=cost_saved,
            duration_ms=(datetime.now() - self.start_time).total_seconds() * 1000,
        )

        # Store in global trace storage
        storage = get_trace_storage()
        storage.add(trace)

        return trace
