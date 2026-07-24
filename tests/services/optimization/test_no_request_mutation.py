"""Byte-equality regression tests — observe-only must NEVER alter request bytes."""

from __future__ import annotations

import hashlib
import json

import pytest

from tokenpak.services.optimization.pipeline import run_observe_only
from tokenpak.services.optimization.stage import EligibilityResult, NoOpStage


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_off_mode_returns_identical_bytes(env_off, openai_responses_body):
    body_in = openai_responses_body
    body_out, trace = run_observe_only(
        request_id="r-off",
        body=body_in,
        method="POST",
        path="/v1/responses",
        headers={"content-type": "application/json"},
    )
    assert body_out is body_in
    assert _sha(body_out) == _sha(body_in)
    assert trace.mode == "off"
    assert trace.stages == []
    assert trace.body_bytes_in == trace.body_bytes_out == len(body_in)


def test_observe_mode_returns_identical_bytes(env_observe, openai_responses_body):
    body_in = openai_responses_body
    body_out, trace = run_observe_only(
        request_id="r-obs",
        body=body_in,
        method="POST",
        path="/v1/responses",
        headers={"content-type": "application/json"},
    )
    assert body_out == body_in
    assert _sha(body_out) == _sha(body_in)
    assert trace.mode == "observe"
    # Observe-only contract: body unchanged (no stage marked applied).
    assert trace.body_unchanged is True


def test_observe_mode_with_codex_body(env_observe, codex_responses_body):
    body_in = codex_responses_body
    body_out, trace = run_observe_only(
        request_id="r-codex",
        body=body_in,
        method="POST",
        path="/codex/responses",
        headers={"content-type": "application/json"},
        target_url="https://api.openai.com/v1/responses",
        platform="codex",
        route="sdk",
    )
    assert _sha(body_out) == _sha(body_in)
    assert trace.body_unchanged is True


def test_observe_mode_with_no_op_stage_registered_does_not_mutate(
    env_observe, openai_responses_body
):
    """Even with stages, observe-only never touches the body."""
    from tokenpak.services.optimization.pipeline import _get_default_pipeline

    pipeline = _get_default_pipeline()
    pipeline.register(NoOpStage(name="probe"))

    body_in = openai_responses_body
    body_out, trace = run_observe_only(
        request_id="r-stage",
        body=body_in,
        method="POST",
        path="/v1/responses",
        headers={"content-type": "application/json"},
    )
    assert _sha(body_out) == _sha(body_in)
    assert trace.body_unchanged is True
    assert any(s.name == "probe" for s in trace.stages)
    assert all(s.applied is False for s in trace.stages)


def test_observe_mode_with_eligible_true_stage_still_does_not_mutate(env_observe):
    """A stage that says 'I would run' must still not run in observe-only."""

    class _Mutator:
        name = "mutator"
        required_capabilities = frozenset()

        def eligible(self, ctx):
            return EligibilityResult(eligible=True)

        def apply(self, ctx):
            # If observe-only ever calls this, the body would be replaced.
            ctx.raw_body = b'{"hijacked": true}'
            return ctx

    from tokenpak.services.optimization.pipeline import _get_default_pipeline

    pipeline = _get_default_pipeline()
    pipeline.register(_Mutator())

    body_in = b'{"original":"unchanged"}'
    body_out, trace = run_observe_only(
        request_id="r-mut",
        body=body_in,
        method="POST",
        path="/v1/responses",
    )
    assert body_out == body_in
    assert trace.body_unchanged is True
    # Mutator was eligible but apply was never called.
    mut = next(s for s in trace.stages if s.name == "mutator")
    assert mut.eligible is True
    assert mut.applied is False


def test_bypass_paths_short_circuit(env_observe):
    """Health/stats/empty-body requests are bypassed without running stages."""
    body_out, trace = run_observe_only(
        request_id="r-health",
        body=b'{"alive":true}',
        method="POST",
        path="/health",
        headers={},
    )
    assert trace.bypass_reason.startswith("control-path:")
    assert trace.stages == []

    body_out, trace = run_observe_only(
        request_id="r-empty",
        body=b"",
        method="POST",
        path="/v1/responses",
    )
    assert trace.bypass_reason == "empty-body"
    assert trace.stages == []

    body_out, trace = run_observe_only(
        request_id="r-get",
        body=b'{"x":1}',
        method="GET",
        path="/v1/responses",
    )
    assert trace.bypass_reason == "method-not-post"
    assert trace.stages == []


@pytest.mark.parametrize(
    "fixture_name",
    ["openai_responses_body", "codex_responses_body"],
)
def test_round_trip_byte_equality_across_fixtures(env_observe, fixture_name, request):
    body_in = request.getfixturevalue(fixture_name)
    body_out, _trace = run_observe_only(
        request_id=f"r-{fixture_name}",
        body=body_in,
        method="POST",
        path="/v1/responses",
    )
    assert body_out == body_in
    # And the original still round-trips through json — proving we didn't
    # accidentally damage encoding either.
    assert json.loads(body_in.decode("utf-8")) == json.loads(body_out.decode("utf-8"))
