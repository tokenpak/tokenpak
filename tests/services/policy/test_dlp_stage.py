"""DLPStage — 1.3.0-β pipeline integration acceptance."""

from __future__ import annotations

import pytest

from tokenpak.core.routing.policy import Policy
from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.policy_service.dlp_stage import DLPStage
from tokenpak.services.request import Request
from tokenpak.services.request_pipeline.stages import PipelineContext


def _ctx(body: bytes, policy: Policy) -> PipelineContext:
    ctx = PipelineContext(request=Request(body=body, headers={}))
    ctx.route_class = RouteClass.GENERIC
    ctx.policy = policy
    return ctx


def test_off_policy_skips_scan():
    policy = Policy(dlp_mode="off")
    ctx = _ctx(b"sk_live_fakekeyabc1234567890MMMMMM", policy)
    DLPStage().apply_request(ctx)
    assert "security" not in ctx.stage_telemetry
    assert ctx.short_circuit is False


def test_warn_policy_logs_but_forwards():
    policy = Policy(dlp_mode="warn")
    body = b"token=ghp_" + b"a" * 36
    ctx = _ctx(body, policy)
    DLPStage().apply_request(ctx)
    assert ctx.short_circuit is False
    assert ctx.request.body == body
    tele = ctx.stage_telemetry["security"]
    assert tele["dlp_mode"] == "warn"
    assert tele["findings_count"] >= 1
    assert "github_pat" in tele["rules_triggered"]


def test_redact_policy_rewrites_body():
    policy = Policy(dlp_mode="redact", body_handling="mutate")
    body = b"stripe_live_key=sk_live_abc123defghijk4567890mnop"
    ctx = _ctx(body, policy)
    DLPStage().apply_request(ctx)
    assert ctx.short_circuit is False
    assert b"sk_live_" not in ctx.request.body
    assert b"<REDACTED:stripe_live_key>" in ctx.request.body


def test_block_policy_short_circuits():
    policy = Policy(dlp_mode="block")
    body = b"key=AKIAIOSFODNN7EXAMPLE"
    ctx = _ctx(body, policy)
    DLPStage().apply_request(ctx)
    assert ctx.short_circuit is True
    tele = ctx.stage_telemetry["security"]
    assert tele["blocked"] is True


def test_byte_preserve_downgrades_redact_to_warn():
    """Claude Code routes can't accept body rewrites (OAuth billing contract).
    Stage must NOT mutate the body even if policy says redact."""
    policy = Policy(dlp_mode="redact", body_handling="byte_preserve")
    body = b"key=AKIAIOSFODNN7EXAMPLE"
    ctx = _ctx(body, policy)
    DLPStage().apply_request(ctx)
    assert ctx.request.body == body  # unchanged
    assert ctx.short_circuit is False
    tele = ctx.stage_telemetry["security"]
    assert tele["dlp_mode"] == "warn"
    assert tele["dlp_mode_downgraded"].startswith("redact->warn")


def test_no_findings_no_change():
    policy = Policy(dlp_mode="redact", body_handling="mutate")
    body = b"just a normal prompt"
    ctx = _ctx(body, policy)
    DLPStage().apply_request(ctx)
    assert ctx.request.body == body
    assert ctx.short_circuit is False


def test_missing_policy_stage_is_noop():
    """If classify_stage didn't run, DLPStage must not scan."""
    ctx = PipelineContext(request=Request(body=b"AKIAIOSFODNN7EXAMPLE"))
    DLPStage().apply_request(ctx)
    assert "security" not in ctx.stage_telemetry
