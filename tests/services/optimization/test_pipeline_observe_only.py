"""End-to-end observe-only pipeline tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from tokenpak.services.optimization import (
    OptimizationPipeline,
    StageRegistry,
)
from tokenpak.services.optimization.context import OptimizationContext
from tokenpak.services.optimization.stage import EligibilityResult, NoOpStage
from tokenpak.services.optimization.trace import OptimizationTrace


@dataclass
class _RecordingStage:
    """Stage that records every call into a shared list."""

    name: str = "record"
    required_capabilities: frozenset = field(default_factory=frozenset)
    calls: list = field(default_factory=list)
    verdict: bool = True
    skip: str = ""

    def eligible(self, ctx) -> EligibilityResult:
        self.calls.append(("eligible", ctx.request_id))
        return EligibilityResult(eligible=self.verdict, skip_reason=self.skip)

    def apply(self, ctx):
        # apply() must NOT be invoked in observe-only mode
        self.calls.append(("apply", ctx.request_id))
        return ctx


def _make_ctx(body: bytes, *, request_id: str = "r-1") -> OptimizationContext:
    trace = OptimizationTrace(request_id=request_id, mode="observe")
    return OptimizationContext(
        request_id=request_id,
        raw_body=body,
        trace=trace,
    )


def test_run_observe_only_emits_one_stage_trace_per_stage(fresh_pipeline):
    a = _RecordingStage(name="a", verdict=True)
    b = _RecordingStage(name="b", verdict=False, skip="route-class-blocked")
    fresh_pipeline.register(a)
    fresh_pipeline.register(b)

    ctx = _make_ctx(b'{"model":"gpt-4o-mini","input":"hi"}')
    trace = fresh_pipeline.run_observe_only(ctx)

    assert [s.name for s in trace.stages] == ["a", "b"]
    assert trace.stages[0].eligible is True
    assert trace.stages[0].applied is False
    assert trace.stages[1].eligible is False
    assert trace.stages[1].skip_reason == "route-class-blocked"
    assert trace.stages[1].applied is False


def test_run_observe_only_never_calls_apply(fresh_pipeline):
    s = _RecordingStage(name="s", verdict=True)
    fresh_pipeline.register(s)

    ctx = _make_ctx(b'{"input":"foo"}')
    fresh_pipeline.run_observe_only(ctx)

    # Only eligible() ran; apply() was never invoked.
    kinds = [c[0] for c in s.calls]
    assert "eligible" in kinds
    assert "apply" not in kinds


def test_eligible_exception_does_not_break_pipeline(fresh_pipeline):
    class _Boom:
        name = "boom"
        required_capabilities = frozenset()

        def eligible(self, ctx):
            raise RuntimeError("synthetic")

        def apply(self, ctx):
            return ctx

    after = _RecordingStage(name="after", verdict=True)
    fresh_pipeline.register(_Boom())
    fresh_pipeline.register(after)

    ctx = _make_ctx(b'{"x":1}')
    trace = fresh_pipeline.run_observe_only(ctx)

    # Boom recorded with eligible-exception skip_reason; downstream stage still ran.
    assert [s.name for s in trace.stages] == ["boom", "after"]
    assert trace.stages[0].eligible is False
    assert trace.stages[0].skip_reason == "eligible-exception"
    assert "RuntimeError" in trace.stages[0].detail
    assert trace.stages[1].eligible is True


def test_empty_registry_yields_empty_trace(fresh_pipeline):
    ctx = _make_ctx(b'{"k":"v"}')
    trace = fresh_pipeline.run_observe_only(ctx)
    assert trace.stages == []
    assert trace.body_unchanged is True


def test_no_op_stage_skips_with_documented_reason(fresh_pipeline):
    fresh_pipeline.register(NoOpStage(name="placeholder"))
    ctx = _make_ctx(b'{"k":"v"}')
    trace = fresh_pipeline.run_observe_only(ctx)
    assert len(trace.stages) == 1
    assert trace.stages[0].skip_reason == "no-op-default"
    assert trace.stages[0].applied is False


def test_pipeline_records_input_and_output_byte_counts(fresh_pipeline):
    ctx = _make_ctx(b"x" * 17)
    trace = fresh_pipeline.run_observe_only(ctx)
    assert trace.body_bytes_in == 17
    assert trace.body_bytes_out == 17
    assert trace.body_unchanged is True


def test_default_pipeline_is_singleton_until_reset():
    from tokenpak.services.optimization.pipeline import (
        _get_default_pipeline,
        reset_default_pipeline,
    )
    a = _get_default_pipeline()
    b = _get_default_pipeline()
    assert a is b
    reset_default_pipeline()
    c = _get_default_pipeline()
    assert c is not a


def test_custom_registry_does_not_pollute_default(fresh_pipeline):
    fresh_pipeline.register(NoOpStage(name="custom"))
    # Default pipeline starts empty
    from tokenpak.services.optimization.pipeline import _get_default_pipeline
    default = _get_default_pipeline()
    assert "custom" not in default.registry
    assert isinstance(default, OptimizationPipeline)
    assert isinstance(fresh_pipeline.registry, StageRegistry)
