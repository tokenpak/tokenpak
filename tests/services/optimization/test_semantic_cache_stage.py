"""Tests for SemanticCacheStage in the services/optimization layer (TIP-04).

Covers:
- Hit: safe route repeated query returns cached response.
- Miss: first-time query on safe route is a miss.
- Bypass: code_edit / debugging routes disable semantic cache matching.
- No raw prompt storage: only normalized/hashed text reaches the store.
- Scope: default is session-scoped, not global.
- Trace: miss_reason populated on miss/bypass.
- Record: stage.record() stores a response for future hits.

Ported from tests/proxy/test_semantic_cache_openai_responses.py to
tests/services/optimization/ as part of TIP-04 rework per QA rejection
(needs-rework: move from proxy.optimization to services.optimization).
"""

from __future__ import annotations

import json

from tokenpak.services.optimization import (
    OptimizationContext,
    SemanticCacheStage,
    get_cached_response,
)
from tokenpak.services.optimization.cache_stage import _get_cache_result
from tokenpak.services.optimization.trace import OptimizationTrace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_responses_body(query: str, stream: bool = False) -> bytes:
    payload = {"model": "gpt-4o-mini", "input": query, "stream": stream}
    return json.dumps(payload).encode()


def _make_codex_body(messages: list, stream: bool = False) -> bytes:
    payload = {"model": "gpt-5-codex", "input": messages, "stream": stream}
    return json.dumps(payload).encode()


def _make_ctx(
    body: bytes,
    route: str = "status_check",
    session_id: str = "sess-test-001",
    has_semantic_cap: bool = True,
) -> OptimizationContext:
    """Build a minimal OptimizationContext for cache stage tests."""
    class _Adapter:
        capabilities = frozenset({"tip.cache.semantic.v1"}) if has_semantic_cap else frozenset()

    # Build canonical messages from the body (for cache_key.extract_query_text)
    data = json.loads(body)
    raw_input = data.get("input", "")
    messages = []
    if isinstance(raw_input, str):
        messages = [{"role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        messages = raw_input

    class _Canonical:
        pass

    canonical = _Canonical()
    canonical.messages = messages  # type: ignore[attr-defined]

    trace = OptimizationTrace(request_id="req-001", mode="observe")
    headers = {
        "x-session-id": session_id,
        "content-type": "application/json",
    }
    return OptimizationContext(
        request_id="req-001",
        raw_body=body,
        trace=trace,
        canonical=canonical,
        adapter=_Adapter(),
        route=route,
        headers=headers,
    )


def _stage_with_flag(env: dict | None = None) -> SemanticCacheStage:
    env = env or {"TOKENPAK_SEMANTIC_CACHE_STAGE": "1"}
    return SemanticCacheStage(env=env)


# ---------------------------------------------------------------------------
# Eligibility tests
# ---------------------------------------------------------------------------


def test_eligible_flag_off_by_default():
    """Stage is ineligible when flag is not set."""
    stage = SemanticCacheStage(env={})
    ctx = _make_ctx(_make_responses_body("What is tokenpak?"), route="status_check")
    result = stage.eligible(ctx)
    assert not result.eligible
    assert result.skip_reason == "flag-off"


def test_eligible_safe_route_with_capability():
    """status_check route with tip.cache.semantic.v1 → eligible."""
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("What is the proxy status?"), route="status_check")
    result = stage.eligible(ctx)
    assert result.eligible, f"expected eligible, got skip_reason={result.skip_reason}"


def test_eligible_configuration_inspection():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Show current config"), route="configuration_inspection")
    assert stage.eligible(ctx).eligible


def test_eligible_summarization():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Summarize this document."), route="summarization")
    assert stage.eligible(ctx).eligible


def test_ineligible_code_edit_route():
    """code_edit route bypasses semantic cache (lossless required)."""
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Edit line 42 of file.py"), route="code_edit")
    result = stage.eligible(ctx)
    assert not result.eligible
    assert result.skip_reason == "route-not-cacheable"


def test_ineligible_code_generation_route():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Write a sort function"), route="code_generation")
    result = stage.eligible(ctx)
    assert not result.eligible
    assert result.skip_reason == "route-not-cacheable"


def test_ineligible_debugging_route():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Why does this crash?"), route="debugging")
    result = stage.eligible(ctx)
    assert not result.eligible
    assert result.skip_reason == "route-not-cacheable"


def test_ineligible_test_failure_route():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Fix the failing test"), route="test_failure")
    result = stage.eligible(ctx)
    assert not result.eligible
    assert result.skip_reason == "route-not-cacheable"


def test_ineligible_missing_semantic_capability():
    """Adapter without tip.cache.semantic.v1 → not eligible."""
    stage = _stage_with_flag()
    ctx = _make_ctx(
        _make_responses_body("What is the proxy status?"),
        route="status_check",
        has_semantic_cap=False,
    )
    result = stage.eligible(ctx)
    assert not result.eligible
    assert result.skip_reason == "capability-missing"


def test_ineligible_streaming_request():
    """Streaming requests bypass semantic cache."""
    stage = _stage_with_flag()
    body = _make_responses_body("What is the proxy status?", stream=True)
    ctx = _make_ctx(body, route="status_check")
    result = stage.eligible(ctx)
    assert not result.eligible
    assert result.skip_reason == "streaming-not-supported"


# ---------------------------------------------------------------------------
# Miss tests (first request — no prior record)
# ---------------------------------------------------------------------------


def test_first_request_is_cache_miss():
    """First time a query is seen → cache miss."""
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("What is the proxy status?"), route="status_check")
    ctx = stage.apply(ctx)

    result = _get_cache_result(ctx)
    assert result is not None
    assert not result.hit
    assert result.miss_reason  # some miss reason is populated


def test_cache_miss_sets_query_hash():
    """apply() populates query_hash on miss (hashed, never raw text)."""
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Check proxy health"), route="status_check")
    ctx = stage.apply(ctx)
    result = _get_cache_result(ctx)
    assert result is not None
    # query_hash is a hex substring — at most 12 chars, never the raw query
    assert isinstance(result.query_hash, str)
    assert "Check proxy health" not in result.query_hash


def test_no_cached_response_on_miss():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("What is the proxy status?"), route="status_check")
    ctx = stage.apply(ctx)
    assert get_cached_response(ctx) is None


# ---------------------------------------------------------------------------
# Hit tests (record then lookup)
# ---------------------------------------------------------------------------


def test_cache_hit_after_record_status_check():
    """Safe route: second identical query hits the cache after record()."""
    stage = _stage_with_flag()
    fake_response = {"output": [{"type": "text", "text": "Proxy is healthy"}]}
    query = "What is the proxy status?"
    session = "sess-hit-001"

    # First request — miss, then record
    ctx1 = _make_ctx(_make_responses_body(query), route="status_check", session_id=session)
    ctx1 = stage.apply(ctx1)
    assert not _get_cache_result(ctx1).hit
    stage.record(ctx1, fake_response)

    # Second request — same query, same session → hit
    ctx2 = _make_ctx(_make_responses_body(query), route="status_check", session_id=session)
    ctx2 = stage.apply(ctx2)

    result = _get_cache_result(ctx2)
    assert result is not None
    assert result.hit, f"expected cache hit, got miss_reason={result.miss_reason}"
    assert result.allow_response_reuse  # status_check allows response reuse


def test_hit_returns_cached_response():
    """get_cached_response() returns the stored upstream response on hit."""
    stage = _stage_with_flag()
    fake_response = {"output": [{"text": "All good"}]}
    query = "Is the proxy healthy?"
    session = "sess-hit-002"

    ctx1 = _make_ctx(_make_responses_body(query), route="status_check", session_id=session)
    stage.apply(ctx1)
    stage.record(ctx1, fake_response)

    ctx2 = _make_ctx(_make_responses_body(query), route="status_check", session_id=session)
    stage.apply(ctx2)

    cached = get_cached_response(ctx2)
    assert cached == fake_response


def test_cache_hit_similar_query_via_filler_removal():
    """Queries differing only by filler words normalize to identical text → exact hit."""
    stage = _stage_with_flag()
    fake_response = {"output": [{"text": "OK"}]}
    session = "sess-jaccard-001"

    ctx1 = _make_ctx(_make_responses_body("Can you check the proxy status"), route="status_check", session_id=session)
    stage.apply(ctx1)
    stage.record(ctx1, fake_response)

    ctx2 = _make_ctx(_make_responses_body("Check the proxy status please"), route="status_check", session_id=session)
    stage.apply(ctx2)

    result = _get_cache_result(ctx2)
    assert result is not None
    assert result.hit, f"expected hit after filler removal, got miss_reason={result.miss_reason}"


def test_code_edit_no_response_reuse_even_if_called():
    """code_edit is ineligible — apply() directly tests policy fields."""
    stage = _stage_with_flag()
    query = "Edit line 42"
    session = "sess-code-001"

    # Bypass eligibility and call apply() directly to test policy field
    ctx = _make_ctx(_make_responses_body(query), route="code_edit", session_id=session)
    ctx = stage.apply(ctx)

    result = _get_cache_result(ctx)
    assert result is not None
    assert not result.allow_response_reuse
    assert not result.semantic_enabled  # lossless route: semantic matching off
    assert get_cached_response(ctx) is None


# ---------------------------------------------------------------------------
# Session isolation test
# ---------------------------------------------------------------------------


def test_session_scoped_no_cross_session_leak():
    """Cache entries in one session must NOT be visible in another session."""
    stage = _stage_with_flag()
    fake_response = {"output": [{"text": "Status: OK"}]}
    query = "What is the proxy status?"

    # Session A records
    ctx_a = _make_ctx(_make_responses_body(query), route="status_check", session_id="sess-A")
    stage.apply(ctx_a)
    stage.record(ctx_a, fake_response)

    # Session B should miss
    ctx_b = _make_ctx(_make_responses_body(query), route="status_check", session_id="sess-B")
    stage.apply(ctx_b)

    result_b = _get_cache_result(ctx_b)
    assert result_b is not None
    assert not result_b.hit, "cross-session cache leak: session B hit session A entry"


# ---------------------------------------------------------------------------
# No raw prompt storage
# ---------------------------------------------------------------------------


def test_no_raw_prompt_stored_in_trace():
    """The CacheStageTrace must not contain raw prompt text."""
    stage = _stage_with_flag()
    raw_query = "Show me the current configuration settings for the proxy"
    ctx = _make_ctx(_make_responses_body(raw_query), route="status_check")
    ctx = stage.apply(ctx)

    result = _get_cache_result(ctx)
    assert result is not None
    assert raw_query not in result.query_hash
    assert raw_query not in result.miss_reason
    detail_str = result.to_detail_str()
    assert raw_query not in detail_str


# ---------------------------------------------------------------------------
# Trace / telemetry
# ---------------------------------------------------------------------------


def test_trace_fields_populated_on_miss():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Check health"), route="status_check")
    ctx = stage.apply(ctx)

    result = _get_cache_result(ctx)
    assert result is not None
    assert result.route == "status_check"
    assert result.allow_response_reuse is True
    assert result.semantic_enabled is True
    assert result.miss_reason  # non-empty on miss


def test_trace_to_detail_str_is_valid_json():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("Check health"), route="status_check")
    ctx = stage.apply(ctx)
    result = _get_cache_result(ctx)
    parsed = json.loads(result.to_detail_str())
    assert "hit" in parsed
    assert "miss_reason" in parsed
    assert "route" in parsed


def test_recorded_flag_set_after_record():
    stage = _stage_with_flag()
    ctx = _make_ctx(_make_responses_body("What is status?"), route="status_check")
    ctx = stage.apply(ctx)
    stage.record(ctx, {"output": []})
    result = _get_cache_result(ctx)
    assert result is not None
    assert result.recorded is True


# ---------------------------------------------------------------------------
# OpenAI Responses adapter capability
# ---------------------------------------------------------------------------


def test_openai_responses_adapter_has_semantic_capability():
    """OpenAIResponsesAdapter declares tip.cache.semantic.v1 after TIP-04 update."""
    from tokenpak.proxy.adapters.openai_responses_adapter import OpenAIResponsesAdapter
    adapter = OpenAIResponsesAdapter()
    assert "tip.cache.semantic.v1" in adapter.capabilities
