"""classify_stage — 1.3.0-α acceptance.

Confirms the first-in-pipeline stage attaches route_class + policy +
session_id onto the context, and is idempotent.
"""

from __future__ import annotations

from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.request import Request
from tokenpak.services.request_pipeline.classify_stage import ClassifyStage
from tokenpak.services.request_pipeline.stages import PipelineContext


def test_attaches_route_class_and_policy():
    req = Request(
        headers={"x-claude-code-session-id": "abc123"},
        body=b'{"stream": true}',
    )
    ctx = PipelineContext(request=req)
    ClassifyStage().apply_request(ctx)
    assert ctx.route_class is RouteClass.CLAUDE_CODE_TUI
    assert ctx.policy is not None
    assert ctx.policy.body_handling == "byte_preserve"


def test_captures_session_id_from_policy_header():
    req = Request(
        headers={"x-claude-code-session-id": "session-xyz"},
        body=b'{"stream": true}',
    )
    ctx = PipelineContext(request=req)
    ClassifyStage().apply_request(ctx)
    # Policy names x-claude-code-session-id as the session-id header
    # for CC routes → stage should have captured it onto metadata.
    assert ctx.request.metadata.get("session_id") == "session-xyz"


def test_is_idempotent():
    req = Request(headers={"x-claude-code-session-id": "s1"})
    ctx = PipelineContext(request=req)
    stage = ClassifyStage()
    stage.apply_request(ctx)
    first = ctx.route_class
    stage.apply_request(ctx)  # second call should no-op
    assert ctx.route_class is first


def test_generic_route_produces_mutate_policy():
    ctx = PipelineContext(request=Request(body=b"random stuff"))
    ClassifyStage().apply_request(ctx)
    assert ctx.route_class is RouteClass.GENERIC
    assert ctx.policy.body_handling == "mutate"


def test_telemetry_breadcrumb_written():
    req = Request(headers={"User-Agent": "anthropic-python/0.40"})
    ctx = PipelineContext(request=req)
    ClassifyStage().apply_request(ctx)
    assert ctx.stage_telemetry["classify"]["route_class"] == "anthropic-sdk"
