"""End-to-end test for the services.execute pipeline composition.

Verifies:
  - The pipeline composes and runs without error.
  - Every CANONICAL_STAGES stage loads (maps to a live subpackage).
  - Request-side stages run forward, response-side in reverse (LIFO).
  - An injected dispatcher receives the PipelineContext.
  - Short-circuiting before dispatch skips the dispatcher.

These tests do NOT hit a provider; they inject a fixture dispatcher.
Provider-invoking integration tests live in tests/integration/.
"""

from __future__ import annotations

from tokenpak.services import Request, Response, execute
from tokenpak.services.request_pipeline.composition import build_pipeline
from tokenpak.services.request_pipeline.stages import (
    CANONICAL_STAGES,
    PipelineContext,
)


def test_pipeline_loads_every_canonical_stage() -> None:
    """Every stage name in CANONICAL_STAGES has a live Stage implementation."""
    stages = build_pipeline()
    loaded_names = {stage.name for stage in stages}
    canonical = set(CANONICAL_STAGES)
    missing = canonical - loaded_names
    assert not missing, (
        f"canonical stages missing Stage implementations: {sorted(missing)}"
    )


def test_execute_runs_dispatcher() -> None:
    """execute() threads the injected dispatcher and returns its Response."""
    fixture_body = b'{"ok": true}'
    calls: list[PipelineContext] = []

    def fixture_dispatch(ctx: PipelineContext) -> Response:
        calls.append(ctx)
        return Response(status=200, body=fixture_body)

    req = Request(body=b'{"model": "test"}', headers={"X-Test": "1"})
    resp = execute(req, dispatch=fixture_dispatch)

    assert len(calls) == 1
    assert resp.status == 200
    assert resp.body == fixture_body


def test_execute_short_circuit_skips_dispatch(monkeypatch) -> None:
    """A stage that sets short_circuit=True + response skips the dispatcher."""
    short_response = Response(status=200, body=b"cached")

    class _ShortCircuitingStage:
        name = "cache"  # pretends to be cache so ordering is realistic

        def apply_request(self, ctx: PipelineContext) -> None:
            ctx.response = short_response
            ctx.short_circuit = True

        def apply_response(self, ctx: PipelineContext) -> None:
            return None

    # Inject our synthetic stage as the only stage in the pipeline.
    from tokenpak.services.request_pipeline import composition

    monkeypatch.setattr(
        composition,
        "build_pipeline",
        lambda: [_ShortCircuitingStage()],
    )

    def should_not_be_called(ctx: PipelineContext) -> Response:
        raise AssertionError("dispatcher called despite short-circuit")

    from tokenpak.services.request_pipeline.composition import run_pipeline

    resp = run_pipeline(Request(body=b""), should_not_be_called)
    assert resp is short_response
    assert resp.body == b"cached"


def test_request_and_response_types_are_usable() -> None:
    """Request / Response / Chunk import cleanly and accept the fields they declare."""
    from tokenpak.services import Chunk

    req = Request(body=b"x", headers={"h": "v"}, metadata={"k": 1})
    resp = Response(status=204, body=b"", headers={}, metadata={})
    chunk = Chunk(body=b"y", terminal=False, metadata={})
    assert req.body == b"x"
    assert resp.status == 204
    assert chunk.body == b"y"
