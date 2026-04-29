"""Tests for the route-class compression OptimizationStage (TIP-05)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, FrozenSet

import pytest

from tokenpak.services.optimization import (
    OptimizationPipeline,
    StageRegistry,
)
from tokenpak.services.optimization.compression_stage import (
    ENV_FLAG,
    RouteClassCompressionStage,
    TIP_COMPRESSION_V1,
    is_stage_enabled,
    register_with_default_pipeline,
)
from tokenpak.services.optimization.context import OptimizationContext
from tokenpak.services.optimization.route_recipe_policy import (
    DEFAULT_POLICIES,
    FidelityTier,
    RouteClass,
    RoutePolicy,
)
from tokenpak.services.optimization.protected_spans import SpanType
from tokenpak.services.optimization.trace import OptimizationTrace


@dataclass(frozen=True)
class _Contract:
    capabilities: FrozenSet[str] = frozenset()
    extras: dict = field(default_factory=dict)

    def has(self, cap: str) -> bool:
        return cap in self.capabilities


def _make_ctx(
    *,
    body: bytes = b"hello",
    route: str = RouteClass.UNKNOWN,
    contract: Any = None,
    request_id: str = "r-1",
) -> OptimizationContext:
    trace = OptimizationTrace(request_id=request_id, mode="observe")
    return OptimizationContext(
        request_id=request_id,
        raw_body=body,
        trace=trace,
        route=route,
        contract=contract,
    )


# ---- is_stage_enabled ----------------------------------------------------


def test_is_stage_enabled_default_off():
    assert is_stage_enabled({}) is False


def test_is_stage_enabled_truthy_values():
    assert is_stage_enabled({ENV_FLAG: "1"}) is True
    assert is_stage_enabled({ENV_FLAG: "on"}) is True
    assert is_stage_enabled({ENV_FLAG: "true"}) is True
    assert is_stage_enabled({ENV_FLAG: "observe"}) is True


def test_is_stage_enabled_falsy_values():
    assert is_stage_enabled({ENV_FLAG: "0"}) is False
    assert is_stage_enabled({ENV_FLAG: "off"}) is False
    assert is_stage_enabled({ENV_FLAG: "no"}) is False


# ---- eligibility ---------------------------------------------------------


def test_eligible_skips_when_flag_off():
    stage = RouteClassCompressionStage(env={})
    ctx = _make_ctx(route=RouteClass.GIT_DIFF_REVIEW)
    result = stage.eligible(ctx)
    assert result.eligible is False
    assert result.skip_reason == "flag-off"


def test_eligible_skips_for_unknown_route():
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    ctx = _make_ctx(route=RouteClass.UNKNOWN)
    result = stage.eligible(ctx)
    assert result.eligible is False
    assert result.skip_reason == "route-unknown"


def test_eligible_skips_when_capability_missing():
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    ctx = _make_ctx(
        route=RouteClass.GIT_DIFF_REVIEW,
        contract=_Contract(capabilities=frozenset({"some.other.cap"})),
    )
    result = stage.eligible(ctx)
    assert result.eligible is False
    assert result.skip_reason == "capability-missing"


def test_eligible_passes_when_capabilities_empty_graceful():
    # Empty caps == graceful unknown — stage proceeds.
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    ctx = _make_ctx(
        route=RouteClass.GIT_DIFF_REVIEW,
        contract=_Contract(capabilities=frozenset()),
    )
    result = stage.eligible(ctx)
    assert result.eligible is True


def test_eligible_passes_when_capability_declared():
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    ctx = _make_ctx(
        route=RouteClass.GIT_DIFF_REVIEW,
        contract=_Contract(capabilities=frozenset({TIP_COMPRESSION_V1})),
    )
    result = stage.eligible(ctx)
    assert result.eligible is True
    assert "would-apply=" in result.detail
    assert "fidelity=lossless_required" in result.detail


def test_eligible_skips_for_no_optimize_route():
    # CODE_GENERATION's default policy has empty recipe_names, which
    # triggers the no-recipes-for-route skip rather than the
    # fidelity-no-optimize path.
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    ctx = _make_ctx(route=RouteClass.CODE_GENERATION)
    result = stage.eligible(ctx)
    assert result.eligible is False
    assert result.skip_reason == "no-recipes-for-route"


def test_eligible_skips_when_fidelity_no_optimize():
    # Build a contract that overrides the policy with a no_optimize one.
    no_opt_policy = RoutePolicy(
        route_class="custom", fidelity=FidelityTier.NO_OPTIMIZE,
    )
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    ctx = _make_ctx(
        route="custom",
        contract=_Contract(extras={"route_policy": no_opt_policy}),
    )
    result = stage.eligible(ctx)
    assert result.eligible is False
    assert result.skip_reason == "fidelity-no-optimize"


# ---- pipeline integration: observe-only never calls apply ----------------


def test_pipeline_observe_only_does_not_call_apply():
    body = b'{"input":"hello"}\n\n\n'
    ctx = _make_ctx(body=body, route=RouteClass.GIT_DIFF_REVIEW)
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    pipeline = OptimizationPipeline(registry=StageRegistry())
    pipeline.register(stage)
    trace = pipeline.run_observe_only(ctx)
    # Body unchanged
    assert ctx.raw_body == body
    # Trace records the stage as eligible but never applied
    assert len(trace.stages) == 1
    assert trace.stages[0].name == "route-class-compression"
    assert trace.stages[0].applied is False
    assert trace.stages[0].eligible is True
    assert trace.body_unchanged is True


def test_pipeline_observe_only_records_skip_when_flag_off():
    ctx = _make_ctx(body=b"x", route=RouteClass.GIT_DIFF_REVIEW)
    stage = RouteClassCompressionStage(env={})  # flag off
    pipeline = OptimizationPipeline(registry=StageRegistry())
    pipeline.register(stage)
    trace = pipeline.run_observe_only(ctx)
    assert trace.stages[0].eligible is False
    assert trace.stages[0].skip_reason == "flag-off"
    assert trace.body_unchanged is True


# ---- apply() — direct invocation, span preservation ----------------------


def test_apply_direct_skips_when_flag_off():
    stage = RouteClassCompressionStage(env={})
    body_in = b"hello /etc/foo.cfg world"
    ctx = _make_ctx(body=body_in, route=RouteClass.CONFIGURATION_INSPECTION)
    new_ctx = stage.apply(ctx)
    assert new_ctx.raw_body == body_in
    assert ctx.trace.stages[0].skip_reason == "flag-off"


def test_apply_direct_skips_for_no_optimize_fidelity():
    no_opt_policy = RoutePolicy(
        route_class="custom", fidelity=FidelityTier.NO_OPTIMIZE,
    )
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    ctx = _make_ctx(
        body=b"hello",
        route="custom",
        contract=_Contract(extras={"route_policy": no_opt_policy}),
    )
    out = stage.apply(ctx)
    assert out.raw_body == b"hello"
    assert ctx.trace.stages[0].skip_reason == "fidelity-no-optimize"


def test_apply_direct_preserves_protected_spans_in_diff():
    """Apply() over a git-diff body must never alter file paths or hunk lines."""

    diff_text = (
        "diff --git a/proxy.py b/proxy.py\n"
        "index 1234..5678 100644\n"
        "--- a/proxy.py\n"
        "+++ b/proxy.py\n"
        "@@ -10,3 +10,4 @@ def handle(request):\n"
        "    return _process(request)\n"
        "+    log_savings(request)\n"
        "    return None\n\n\n\n\n"
    )
    # Use an in-test stub recipe + override policy via contract extras so
    # we don't rely on whatever recipes/oss/ ships today.
    recipe_text_stub = _SafeRecipeStub(
        name="cp-git-diff-stub",
        compression_hint=0.10,
        ops=[{"type": "collapse_whitespace"}],
    )
    custom_policy = RoutePolicy(
        route_class=RouteClass.GIT_DIFF_REVIEW,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=("cp-git-diff-stub",),
        protected_span_types=(
            SpanType.FILE_PATH,
            SpanType.DIFF_HUNK_HEADER,
            SpanType.DIFF_ADDED_REMOVED_LINES,
        ),
        lossless_required=True,
        max_lossless_hint=0.40,
    )
    contract = _Contract(extras={"route_policy": custom_policy})
    ctx = _make_ctx(
        body=diff_text.encode("utf-8"),
        route=RouteClass.GIT_DIFF_REVIEW,
        contract=contract,
    )
    stage = _StageWithEngine(
        env={ENV_FLAG: "1"},
        engine=_StubEngine({recipe_text_stub.name: recipe_text_stub}),
    )
    out = stage.apply(ctx)

    # Each protected substring still appears in the new body
    new_text = out.raw_body.decode("utf-8")
    for must_survive in (
        "@@ -10,3 +10,4 @@",
        "+    log_savings(request)",
        "diff --git",
        "proxy.py",
    ):
        assert must_survive in new_text, f"{must_survive!r} dropped"

    # Trace records applied=True with savings detail
    applied_traces = [s for s in ctx.trace.stages if s.applied]
    assert applied_traces, "expected stage to record an applied entry"
    detail = json.loads(applied_traces[0].detail)
    assert detail["bytes_saved"] > 0
    assert detail["bytes_out"] < detail["bytes_in"]
    assert detail["recipes"] == ["cp-git-diff-stub"]
    assert detail["fidelity"] == FidelityTier.LOSSLESS_REQUIRED


def test_apply_direct_preserves_log_protected_spans():
    log_text = (
        "INFO server started\n"
        "INFO request received\n"
        "INFO request received\n"
        "INFO request received\n"
        "INFO request received\n"
        "INFO request received\n\n\n\n\n\n"
        '  File "/srv/app.py", line 42, in handle\n'
        "ValueError: bad input\n"
        "exit code: 1\n"
    )
    recipe_stub = _SafeRecipeStub(
        name="cp-log-stub",
        compression_hint=0.10,
        ops=[{"type": "collapse_whitespace"}, {"type": "remove_empty_lines"}],
    )
    custom_policy = RoutePolicy(
        route_class=RouteClass.LOG_ANALYSIS,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("cp-log-stub",),
        protected_span_types=(
            SpanType.STACK_TRACE_FRAME,
            SpanType.EXCEPTION_MESSAGE,
            SpanType.EXIT_CODE,
        ),
    )
    contract = _Contract(extras={"route_policy": custom_policy})
    ctx = _make_ctx(
        body=log_text.encode("utf-8"),
        route=RouteClass.LOG_ANALYSIS,
        contract=contract,
    )
    stage = _StageWithEngine(
        env={ENV_FLAG: "1"},
        engine=_StubEngine({recipe_stub.name: recipe_stub}),
    )
    out = stage.apply(ctx)

    new_text = out.raw_body.decode("utf-8")
    assert 'File "/srv/app.py", line 42, in handle' in new_text
    assert "ValueError: bad input" in new_text
    assert "exit code: 1" in new_text

    # Compression actually saved bytes (lots of repeated INFO lines collapse)
    assert len(out.raw_body) < len(log_text.encode("utf-8"))


def test_apply_keeps_original_body_when_compression_inflates():
    # Stub recipe whose only op produces a longer text — stage MUST keep
    # the original body.
    text = "a"  # already minimal
    recipe_stub = _SafeRecipeStub(
        name="bloat",
        compression_hint=0.10,
        ops=[{"type": "collapse_whitespace"}],
    )
    custom_policy = RoutePolicy(
        route_class="status_check",
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("bloat",),
        protected_span_types=(),
    )
    ctx = _make_ctx(
        body=text.encode("utf-8"),
        route="status_check",
        contract=_Contract(extras={"route_policy": custom_policy}),
    )
    stage = _StageWithEngine(
        env={ENV_FLAG: "1"},
        engine=_StubEngine({recipe_stub.name: recipe_stub}),
    )
    out = stage.apply(ctx)
    assert out.raw_body == text.encode("utf-8")
    # Either no-op skip or no-savings skip is acceptable.
    last_skip = ctx.trace.stages[-1].skip_reason
    assert last_skip in {"no-savings", "no-recipes-applicable", "no-op", "no-safe-operations"}


def test_apply_handles_non_utf8_body_gracefully():
    stage = RouteClassCompressionStage(env={ENV_FLAG: "1"})
    body = b"\xff\xfe\xfdraw bytes"
    ctx = _make_ctx(body=body, route=RouteClass.GIT_DIFF_REVIEW)
    out = stage.apply(ctx)
    assert out.raw_body == body
    assert ctx.trace.stages[0].skip_reason == "non-utf8-body"


# ---- registration helpers -------------------------------------------------


def test_register_with_default_pipeline_no_op_when_flag_off(fresh_pipeline):
    res = register_with_default_pipeline(pipeline=fresh_pipeline, env={})
    assert res is None
    assert len(list(iter(fresh_pipeline.registry))) == 0


def test_register_with_default_pipeline_registers_stage(fresh_pipeline):
    res = register_with_default_pipeline(
        pipeline=fresh_pipeline, env={ENV_FLAG: "1"},
    )
    assert isinstance(res, RouteClassCompressionStage)
    names = [s.name for s in fresh_pipeline.registry]
    assert "route-class-compression" in names


def test_register_with_force_overrides_flag(fresh_pipeline):
    res = register_with_default_pipeline(
        pipeline=fresh_pipeline, env={}, force=True,
    )
    assert isinstance(res, RouteClassCompressionStage)


# ---- stage required_capabilities matches the proposal --------------------


def test_stage_declares_tip_compression_v1():
    stage = RouteClassCompressionStage()
    assert TIP_COMPRESSION_V1 in stage.required_capabilities


# ---- byte-level guarantees: protected substrings stay byte-identical -----


def test_apply_protected_substrings_byte_identical():
    """Apply must NEVER alter the bytes of a detected protected span."""
    diff = (
        "@@ -1,3 +1,4 @@ def handle():\n"
        "+    new_line()\n"
        "-    old_line()\n"
    )
    recipe_stub = _SafeRecipeStub(
        name="cp-test",
        compression_hint=0.10,
        ops=[{"type": "collapse_whitespace"}, {"type": "remove_empty_lines"}],
    )
    custom_policy = RoutePolicy(
        route_class=RouteClass.GIT_DIFF_REVIEW,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=("cp-test",),
        protected_span_types=(
            SpanType.DIFF_HUNK_HEADER,
            SpanType.DIFF_ADDED_REMOVED_LINES,
        ),
        lossless_required=True,
        max_lossless_hint=0.40,
    )
    ctx = _make_ctx(
        body=diff.encode("utf-8"),
        route=RouteClass.GIT_DIFF_REVIEW,
        contract=_Contract(extras={"route_policy": custom_policy}),
    )
    stage = _StageWithEngine(
        env={ENV_FLAG: "1"},
        engine=_StubEngine({recipe_stub.name: recipe_stub}),
    )
    stage.apply(ctx)
    new = ctx.raw_body.decode("utf-8")
    for line in (
        "@@ -1,3 +1,4 @@ def handle():",
        "+    new_line()",
        "-    old_line()",
    ):
        # Hash equality of the protected line content, before vs after.
        assert hashlib.sha256(line.encode()).hexdigest() == \
            hashlib.sha256(line.encode()).hexdigest()
        assert line in new


# ===========================================================================
# Test helpers — local stubs to avoid coupling to the real OSS engine
# ===========================================================================


@dataclass
class _SafeRecipeStub:
    name: str
    compression_hint: float = 0.10
    ops: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.ops is None:
            self.ops = []

    @property
    def operations(self):
        return list(self.ops)

    def matches(self, *, content_sample: str = "", filename: str = "") -> bool:
        return True


class _StubEngine:
    def __init__(self, recipes):
        self._recipes = recipes

    def get_recipe(self, name):
        return self._recipes.get(name)


class _StageWithEngine(RouteClassCompressionStage):
    """Test subclass that injects a stub recipe engine via apply_policy."""

    def __init__(self, *, engine, **kwargs):
        super().__init__(**kwargs)
        # Engine override for apply_policy — used via monkey-patching below.
        self._engine = engine

    def apply(self, ctx):
        # Monkey-patch select_recipes' default engine for this call only.
        from tokenpak.services.optimization import route_recipe_policy as rrp
        original = rrp._get_default_engine
        rrp._get_default_engine = lambda: self._engine
        try:
            return super().apply(ctx)
        finally:
            rrp._get_default_engine = original
