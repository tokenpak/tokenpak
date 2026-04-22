"""ContextEnrichmentStage — 1.3.0-β acceptance."""

from __future__ import annotations

import json

import pytest

from tokenpak.core.routing.policy import Policy
from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.routing_service.context_enrichment import (
    ContextEnrichmentStage,
)
from tokenpak.services.request import Request
from tokenpak.services.request_pipeline.stages import PipelineContext


def _request_body(user_msg: str, system: str | list | None = None) -> bytes:
    data = {"model": "claude-haiku-4-5", "messages": [{"role": "user", "content": user_msg}]}
    if system is not None:
        data["system"] = system
    return json.dumps(data).encode("utf-8")


def _ctx(body: bytes, policy: Policy) -> PipelineContext:
    ctx = PipelineContext(request=Request(body=body, headers={}))
    ctx.route_class = RouteClass.ANTHROPIC_SDK
    ctx.policy = policy
    return ctx


def _mock_retriever(hits: list[str]):
    def _search(query: str, top_k: int) -> list[str]:
        return hits[:top_k]
    return _search


def test_injection_disabled_is_noop():
    policy = Policy(injection_enabled=False, body_handling="mutate")
    ctx = _ctx(_request_body("a long prompt that should have triggered enrichment easily"), policy)
    ContextEnrichmentStage(retriever=_mock_retriever(["vault content"])).apply_request(ctx)
    assert b"tokenpak vault context" not in ctx.request.body


def test_byte_preserve_skipped_with_telemetry():
    policy = Policy(injection_enabled=True, body_handling="byte_preserve")
    ctx = _ctx(_request_body("a long enough prompt that would normally trigger enrichment"), policy)
    ContextEnrichmentStage(retriever=_mock_retriever(["irrelevant"])).apply_request(ctx)
    # Body must not mutate on byte_preserve routes.
    assert b"tokenpak vault context" not in ctx.request.body
    assert (
        ctx.stage_telemetry["routing"]["enrichment_skipped"] == "byte_preserve"
    )


def test_short_prompt_hits_relevance_gate():
    policy = Policy(
        injection_enabled=True, body_handling="mutate", injection_min_query_tokens=50
    )
    ctx = _ctx(_request_body("hi"), policy)
    ContextEnrichmentStage(retriever=_mock_retriever(["ctx"])).apply_request(ctx)
    assert b"tokenpak vault context" not in ctx.request.body
    assert (
        ctx.stage_telemetry["routing"]["enrichment_skipped"]
        == "below_min_query_tokens"
    )


def test_long_prompt_gets_enriched():
    policy = Policy(
        injection_enabled=True,
        body_handling="mutate",
        injection_budget_chars=500,
        injection_min_query_tokens=10,
    )
    long_prompt = "please explain in detail how the proxy classifier resolves claude code route classes end to end" * 3
    ctx = _ctx(_request_body(long_prompt), policy)
    retriever = _mock_retriever(["relevant vault snippet A", "relevant vault snippet B"])
    ContextEnrichmentStage(retriever=retriever).apply_request(ctx)
    assert b"tokenpak vault context" in ctx.request.body
    assert ctx.stage_telemetry["routing"]["enrichment_applied"] is True
    assert ctx.stage_telemetry["routing"]["injected_hits"] == 2


def test_injection_budget_truncates():
    policy = Policy(
        injection_enabled=True,
        body_handling="mutate",
        injection_budget_chars=100,  # small
        injection_min_query_tokens=10,
    )
    long_prompt = "explain the route classifier in detail please, it's important" * 4
    ctx = _ctx(_request_body(long_prompt), policy)
    huge_hit = "x" * 10000
    ContextEnrichmentStage(retriever=_mock_retriever([huge_hit])).apply_request(ctx)
    # Injected chars should respect the budget (with a little overhead).
    injected = ctx.stage_telemetry["routing"]["injected_chars"]
    assert injected <= 100 + 50  # header + separator overhead


def test_no_retriever_no_change():
    """If the vault retriever can't be built, the Stage is a no-op."""
    policy = Policy(
        injection_enabled=True,
        body_handling="mutate",
        injection_min_query_tokens=10,
    )
    long_prompt = "please explain in detail how the classifier works " * 5
    ctx = _ctx(_request_body(long_prompt), policy)
    # Explicit None retriever + BlockStore import may fail; Stage should
    # set the telemetry marker without mutating body.
    stage = ContextEnrichmentStage()
    stage.apply_request(ctx)
    # Body unchanged regardless of retriever availability.
    assert b"tokenpak vault context" not in ctx.request.body


def test_injection_preserves_existing_system_string():
    policy = Policy(
        injection_enabled=True,
        body_handling="mutate",
        injection_budget_chars=1000,
        injection_min_query_tokens=10,
    )
    body = _request_body(
        "please describe the architecture in detail for our integration work",
        system="You are a helpful assistant.",
    )
    ctx = _ctx(body, policy)
    ContextEnrichmentStage(retriever=_mock_retriever(["vault snippet"])).apply_request(ctx)
    data = json.loads(ctx.request.body)
    # System is now a list, original content preserved as first block,
    # vault context appended.
    assert isinstance(data["system"], list)
    assert data["system"][0]["text"] == "You are a helpful assistant."
    assert "vault snippet" in data["system"][-1]["text"]
