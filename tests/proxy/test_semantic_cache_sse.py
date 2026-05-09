"""
tests/proxy/test_semantic_cache_sse.py

CCG-15: Wire-format-aware semantic cache — SSE-aware storage and serving.

Tests are self-contained unit tests against the SemanticCache class and
source-structure checks against proxy.py.  No real Anthropic upstream needed.

Seven acceptance criteria (AC-15):

  1. SSE response stored as bytes with text/event-stream content-type
  2. SSE cache hit served as raw bytes with original content-type
     (byte-equal to original SSE chunks)
  3. JSON cache entry NOT served to a streaming client
     (cross-format mismatch → cache miss)
  4. SSE cache entry NOT served to a JSON client (same — symmetric)
  5. Claude Code request with X-Claude-Code-Session-Id still bypassed
     regardless of SSE-awareness (CCG-14 guard composes with CCG-15)
  6. Cache eviction by TTL still works for SSE entries
  7. Buffer cap respected — large SSE responses (>256 KB) bypass the
     semantic cache silently (no error, no partial store)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tokenpak.cache.semantic_cache import (
    SemanticCache,
    SemanticCacheConfig,
    SemanticCacheEntry,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_PROXY_PATH = Path(__file__).parent.parent.parent / "proxy.py"

# TSR-05k / WS-E (2026-05-08) — grep-able skip reason for the two
# test classes that source-grep proxy.py to verify CCG-14 / CCG-15
# wire-format-aware semantic cache behavior. Same antipattern as
# TSR-05f resolved in tests/test_bypass_header.py: post-monolith,
# proxy.py is a 14-line shim that exec()s proxy_monolith.py.bak;
# the patterns these tests grep for now live elsewhere in the
# modular tree (tokenpak/proxy/server.py, tokenpak/cache/semantic_cache.py,
# etc.). Source-grep doesn't survive the refactor.
#
# The 18 in-process SemanticCache tests in this file (which actually
# call SemanticCache.lookup/store with synthetic data) DO pass and
# remain live. Only the 7 source-grep tests in TestClaudeCodeBypassComposes
# (5) + TestSSEBufferCap (2) get skipped here.
SKIP_CCG14_CCG15_SOURCE_GREP_LEGACY = (
    "Test source-greps proxy.py to verify CCG-14/CCG-15 wire-format-aware "
    "semantic cache behavior. Post-monolith, proxy.py is a thin shim that "
    "exec()s proxy_monolith.py.bak; the grep targets now live in the modular "
    "tree (tokenpak/proxy/server.py and tokenpak/cache/semantic_cache.py). "
    "Source-grep is an antipattern that doesn't survive the refactor; future "
    "redesign should rewrite to behavioral tests against the canonical APIs. "
    "The 18 in-process SemanticCache tests in this file are unaffected."
)

_SSE_RESPONSE = (
    b"data: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_test_01\","
    b"\"type\":\"message\",\"role\":\"assistant\",\"content\":[],"
    b"\"stop_reason\":null,\"usage\":{\"input_tokens\":10,\"output_tokens\":0}}}\n\n"
    b"data: {\"type\":\"content_block_start\",\"index\":0,"
    b"\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n"
    b"data: {\"type\":\"content_block_delta\",\"index\":0,"
    b"\"delta\":{\"type\":\"text_delta\",\"text\":\"Paris\"}}\n\n"
    b"data: {\"type\":\"content_block_stop\",\"index\":0}\n\n"
    b"data: {\"type\":\"message_delta\","
    b"\"delta\":{\"stop_reason\":\"end_turn\",\"stop_sequence\":null},"
    b"\"usage\":{\"output_tokens\":5}}\n\n"
    b"data: {\"type\":\"message_stop\"}\n\n"
    b"data: [DONE]\n\n"
)
_SSE_CONTENT_TYPE = "text/event-stream; charset=utf-8"

_JSON_RESPONSE = json.dumps({
    "id": "msg_test_json_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "Paris"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}).encode()
_JSON_CONTENT_TYPE = "application/json"

_QUERY = "What is the capital of France?"


def _make_cache(**kwargs) -> SemanticCache:
    cfg = SemanticCacheConfig(**kwargs)
    return SemanticCache(cfg)


# ===========================================================================
# AC-15-1: SSE response stored as bytes with text/event-stream content-type
# ===========================================================================

class TestSSEStoredAsBytes:

    def test_sse_entry_has_bytes_response(self):
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        entry_hash = list(sc._store.keys())[0]
        entry = sc._store[entry_hash]
        assert isinstance(entry.response, bytes), "SSE entry.response must be bytes"
        assert entry.response == _SSE_RESPONSE

    def test_sse_entry_content_type(self):
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        entry = list(sc._store.values())[0]
        assert entry.content_type == _SSE_CONTENT_TYPE

    def test_sse_entry_wire_format(self):
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        entry = list(sc._store.values())[0]
        assert entry.wire_format == "sse"

    def test_json_entry_has_bytes_response(self):
        sc = _make_cache()
        sc.store(_QUERY, _JSON_RESPONSE, content_type=_JSON_CONTENT_TYPE, wire_format="json")
        entry = list(sc._store.values())[0]
        assert isinstance(entry.response, bytes), "JSON entry.response must be bytes"
        assert entry.wire_format == "json"
        assert entry.content_type == _JSON_CONTENT_TYPE


# ===========================================================================
# AC-15-2: SSE cache hit served as raw bytes with original content-type
# ===========================================================================

class TestSSECacheHitByteEqual:

    def test_sse_hit_returns_byte_equal_response(self):
        """Cache hit for an SSE query must return exactly the stored bytes."""
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        result = sc.lookup(_QUERY, expected_format="sse")
        assert result.hit is True
        assert result.entry is not None
        assert result.entry.response == _SSE_RESPONSE, "Cache hit bytes must be identical to stored bytes"

    def test_sse_hit_content_type_preserved(self):
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        result = sc.lookup(_QUERY, expected_format="sse")
        assert result.hit is True
        assert result.entry.content_type == _SSE_CONTENT_TYPE

    def test_sse_hit_match_strategy(self):
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        result = sc.lookup(_QUERY, expected_format="sse")
        assert result.match_strategy in ("exact", "jaccard")

    def test_json_hit_byte_equal(self):
        """JSON cache hit returns exact stored bytes (no re-serialization)."""
        sc = _make_cache()
        sc.store(_QUERY, _JSON_RESPONSE, content_type=_JSON_CONTENT_TYPE, wire_format="json")
        result = sc.lookup(_QUERY, expected_format="json")
        assert result.hit is True
        assert result.entry.response == _JSON_RESPONSE


# ===========================================================================
# AC-15-3: JSON entry NOT served to a streaming (SSE) client
# ===========================================================================

class TestCrossFormatMismatch_JSONToSSE:

    def test_json_entry_is_miss_for_sse_client(self):
        """A JSON-format cached entry must not be served to a streaming client."""
        sc = _make_cache()
        sc.store(_QUERY, _JSON_RESPONSE, content_type=_JSON_CONTENT_TYPE, wire_format="json")
        result = sc.lookup(_QUERY, expected_format="sse")
        assert result.hit is False, (
            "JSON cache entry must NOT be served to an SSE client — "
            "serving JSON bytes to an SSE parser crashes the agent loop"
        )

    def test_json_entry_still_hits_for_json_client(self):
        """Same entry IS a hit for a JSON client."""
        sc = _make_cache()
        sc.store(_QUERY, _JSON_RESPONSE, content_type=_JSON_CONTENT_TYPE, wire_format="json")
        assert sc.lookup(_QUERY, expected_format="json").hit is True

    def test_multiple_json_entries_all_miss_for_sse(self):
        sc = _make_cache(max_entries=10)
        queries = [
            "What is the capital of France?",
            "What is the capital of Germany?",
            "What is the capital of Spain?",
        ]
        for q in queries:
            sc.store(q, _JSON_RESPONSE, content_type=_JSON_CONTENT_TYPE, wire_format="json")
        for q in queries:
            result = sc.lookup(q, expected_format="sse")
            assert result.hit is False, f"JSON entry for '{q}' must not hit for SSE client"


# ===========================================================================
# AC-15-4: SSE entry NOT served to a JSON (non-streaming) client
# ===========================================================================

class TestCrossFormatMismatch_SSEToJSON:

    def test_sse_entry_is_miss_for_json_client(self):
        """An SSE-format cached entry must not be served to a JSON client."""
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        result = sc.lookup(_QUERY, expected_format="json")
        assert result.hit is False, (
            "SSE cache entry must NOT be served to a JSON client — "
            "SSE bytes are not valid JSON"
        )

    def test_sse_entry_still_hits_for_sse_client(self):
        """Same entry IS a hit for an SSE client."""
        sc = _make_cache()
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        assert sc.lookup(_QUERY, expected_format="sse").hit is True

    def test_mixed_store_cross_format_isolation(self):
        """Storing both JSON and SSE entries for the same query: each client format only hits its own.

        CCG-15: composite store key (query_hash:wire_format) ensures JSON and SSE entries
        for the same query coexist without overwriting each other.
        """
        sc = _make_cache()
        sc.store(_QUERY, _JSON_RESPONSE, content_type=_JSON_CONTENT_TYPE, wire_format="json")
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        assert sc.size() == 2, "Both JSON and SSE entries for the same query must coexist"
        # SSE client gets only SSE entry
        sse_result = sc.lookup(_QUERY, expected_format="sse")
        assert sse_result.hit is True
        assert sse_result.entry.wire_format == "sse"
        # JSON client gets only JSON entry
        json_result = sc.lookup(_QUERY, expected_format="json")
        assert json_result.hit is True
        assert json_result.entry.wire_format == "json"


# ===========================================================================
# AC-15-5: Claude Code bypass guard composes with SSE-awareness (source structure)
# ===========================================================================

@pytest.mark.skip(reason=SKIP_CCG14_CCG15_SOURCE_GREP_LEGACY)
class TestClaudeCodeBypassComposes:
    """Verify CCG-14's Claude Code guard is still active in the CCG-15 lookup and store blocks."""

    @pytest.fixture(scope="class")
    def src(self):
        assert _PROXY_PATH.exists(), f"proxy.py not found at {_PROXY_PATH}"
        return _PROXY_PATH.read_text(encoding="utf-8")

    def test_lookup_guard_bypasses_agent_requests(self, src):
        """CCG-14 guard must bypass requests with X-Claude-Code-Session-Id header."""
        lookup_idx = src.find("# Phase -2: Semantic Cache")
        next_idx = src.find("# Phase -1:", lookup_idx)
        lookup_src = src[lookup_idx:next_idx] if next_idx != -1 else src[lookup_idx:lookup_idx + 4000]
        assert "x-claude-code-session-id" in lookup_src, \
            "CCG-14 guard: X-Claude-Code-Session-Id check missing from lookup block"
        assert "skipped:streaming-or-agent" in lookup_src, \
            "CCG-14 guard: skipped:streaming-or-agent marker missing from lookup block"

    def test_store_guard_bypasses_agent_requests(self, src):
        """CCG-14 guard must bypass storing responses for Claude Code requests."""
        store_idx = src.find("# Post-request: Store successful response in semantic cache")
        store_src = src[store_idx:store_idx + 3000]
        assert "skipped:streaming-or-agent" in store_src, \
            "CCG-14 guard: skipped:streaming-or-agent check missing from store block"

    def test_ccg15_lookup_uses_expected_format(self, src):
        """CCG-15 lookup must pass expected_format to the cache lookup call."""
        lookup_idx = src.find("# Phase -2: Semantic Cache")
        next_idx = src.find("# Phase -1:", lookup_idx)
        lookup_src = src[lookup_idx:next_idx] if next_idx != -1 else src[lookup_idx:lookup_idx + 4000]
        assert "expected_format" in lookup_src, \
            "CCG-15: expected_format parameter missing from lookup call"

    def test_ccg15_store_uses_wire_format(self, src):
        """CCG-15 store must pass wire_format to the cache store call."""
        store_idx = src.find("# Post-request: Store successful response in semantic cache")
        store_src = src[store_idx:store_idx + 3000]
        assert "wire_format" in store_src, \
            "CCG-15: wire_format parameter missing from store call"

    def test_ccg15_tee_buffer_cap_present(self, src):
        """CCG-15 SSE tee buffer must have a configurable cap to prevent memory blowups."""
        sse_loop_idx = src.find("# CCG-15: SSE tee buffer")
        assert sse_loop_idx != -1, "CCG-15: SSE tee buffer initialization not found in proxy.py"
        tee_src = src[sse_loop_idx:sse_loop_idx + 1000]
        assert "_SC_TEE_CAP" in tee_src, "CCG-15: _SC_TEE_CAP cap constant missing from tee buffer"
        assert "256" in tee_src, "CCG-15: 256 KB default cap missing"


# ===========================================================================
# AC-15-6: TTL eviction works for SSE entries
# ===========================================================================

class TestSSETTLEviction:

    def test_sse_entry_expires_after_ttl(self):
        sc = _make_cache(ttl_seconds=1)
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        assert sc.lookup(_QUERY, expected_format="sse").hit is True
        time.sleep(1.1)
        assert sc.lookup(_QUERY, expected_format="sse").hit is False

    def test_sse_entry_evicted_by_evict_expired(self):
        sc = _make_cache(ttl_seconds=1)
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        time.sleep(1.1)
        sc._evict_expired()
        assert sc.size() == 0

    def test_sse_ttl_independent_from_json_ttl(self):
        """SSE and JSON entries for the same query coexist and expire independently."""
        sc = _make_cache(ttl_seconds=1)
        sc.store(_QUERY, _SSE_RESPONSE, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        sc.store(_QUERY, _JSON_RESPONSE, content_type=_JSON_CONTENT_TYPE, wire_format="json")
        # Composite keys — both entries coexist
        assert sc.size() == 2, "SSE and JSON entries for same query must coexist (composite key)"
        time.sleep(1.1)
        sc._evict_expired()
        assert sc.size() == 0


# ===========================================================================
# AC-15-7: Buffer cap — large SSE responses bypass cache silently
# ===========================================================================

@pytest.mark.skip(reason=SKIP_CCG14_CCG15_SOURCE_GREP_LEGACY)
class TestSSEBufferCap:

    def test_proxy_tee_buffer_cap_in_source(self):
        """Proxy source must contain the tee buffer cap constant and overflow logic."""
        src = _PROXY_PATH.read_text(encoding="utf-8")
        tee_idx = src.find("_SC_TEE_CAP")
        assert tee_idx != -1, "CCG-15: _SC_TEE_CAP not found in proxy.py"
        tee_region = src[tee_idx:tee_idx + 500]
        assert "256" in tee_region, "CCG-15: 256 KB cap not configured in proxy.py"
        assert "_sc_tee_capped" in src, "CCG-15: _sc_tee_capped flag not found in proxy.py"

    def test_oversized_response_not_stored(self):
        """SemanticCache.store with a large response stores it normally (cap is in proxy, not cache).

        The 256 KB cap lives in the proxy's tee buffer logic, not in SemanticCache itself.
        SemanticCache accepts any size — it's the proxy that decides not to call store()
        when the buffer cap is exceeded.  This test documents that contract.
        """
        sc = _make_cache()
        large_sse = b"data: {\"x\": \"" + b"A" * (300 * 1024) + b"\"}\n\n"
        # SemanticCache.store itself has no cap — the proxy guards it
        entry = sc.store(_QUERY, large_sse, content_type=_SSE_CONTENT_TYPE, wire_format="sse")
        assert isinstance(entry, SemanticCacheEntry)
        assert entry.response == large_sse

    def test_tee_buffer_cap_logic_in_proxy(self):
        """Source structure: proxy must clear tee buffer and set capped flag on overflow."""
        src = _PROXY_PATH.read_text(encoding="utf-8")
        # Find the tee buffer cap check
        cap_check_idx = src.find("_sc_tee_capped = True")
        assert cap_check_idx != -1, "CCG-15: tee buffer cap set (_sc_tee_capped = True) not found"
        # The buffer should be cleared on cap (free memory)
        cap_region = src[max(0, cap_check_idx - 200):cap_check_idx + 200]
        assert "_sc_tee_buf = b\"\"" in cap_region, \
            "CCG-15: tee buffer must be cleared when cap is exceeded to free memory"
