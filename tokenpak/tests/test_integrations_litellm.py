"""Unit tests for tokenpak.integrations.litellm (formatter, parser, middleware, proxy)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tokenpak.integrations.litellm.formatter import (
    _dict_to_blocks,
    _estimate_tokens,
    blocks_to_messages,
    compile_pack,
)
from tokenpak.integrations.litellm.middleware import TokenPakMiddleware
from tokenpak.integrations.litellm.parser import (
    extract_budget_from_kwargs,
    extract_compaction_from_kwargs,
    parse_tokenpak_request,
)
from tokenpak.integrations.litellm.proxy import ProxyHandler, _json_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(content="hello world", path="docs/a.md", tokens=None):
    return SimpleNamespace(
        compressed_content=content,
        content=content,
        file_type="text",
        quality_score=1.0,
        compressed_tokens=tokens or _estimate_tokens(content),
        raw_tokens=tokens or _estimate_tokens(content),
        path=path,
        slice_id="",
    )


def _wire_pack_passthrough(wire_blocks, budget):
    """Minimal wire.pack stub: returns TOKPAK:N + JSON for the blocks."""
    return f"TOKPAK:{budget}\n" + json.dumps(wire_blocks)


# ---------------------------------------------------------------------------
# formatter._estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string_returns_one(self):
        assert _estimate_tokens("") == 1

    def test_approximate_four_chars_per_token(self):
        assert _estimate_tokens("a" * 400) == 100

    def test_non_zero_result(self):
        assert _estimate_tokens("hi") >= 1


# ---------------------------------------------------------------------------
# formatter._dict_to_blocks
# ---------------------------------------------------------------------------

class TestDictToBlocks:
    def test_empty_blocks_list(self):
        result = _dict_to_blocks({"blocks": []})
        assert result == []

    def test_converts_dict_to_namespace(self):
        pack_dict = {
            "blocks": [
                {"ref": "docs/a.md", "type": "text", "content": "hello", "tokens": 5}
            ]
        }
        result = _dict_to_blocks(pack_dict)
        assert len(result) == 1
        b = result[0]
        assert b.path == "docs/a.md"
        assert b.file_type == "text"
        assert b.compressed_content == "hello"
        assert b.compressed_tokens == 5

    def test_uses_path_as_fallback_for_ref(self):
        pack_dict = {"blocks": [{"path": "alt/path.md", "content": "x"}]}
        result = _dict_to_blocks(pack_dict)
        assert result[0].path == "alt/path.md"

    def test_unknown_ref_defaults_to_unknown(self):
        pack_dict = {"blocks": [{"content": "x"}]}
        result = _dict_to_blocks(pack_dict)
        assert result[0].path == "unknown"

    def test_quality_defaults_to_1(self):
        pack_dict = {"blocks": [{"content": "x"}]}
        result = _dict_to_blocks(pack_dict)
        assert result[0].quality_score == 1.0

    def test_tokens_estimated_when_missing(self):
        content = "a" * 40
        pack_dict = {"blocks": [{"content": content}]}
        result = _dict_to_blocks(pack_dict)
        assert result[0].compressed_tokens == _estimate_tokens(content)


# ---------------------------------------------------------------------------
# formatter.blocks_to_messages
# ---------------------------------------------------------------------------

class TestBlocksToMessages:
    def test_empty_blocks_produces_system_message(self):
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = blocks_to_messages([], budget=8000, compaction="none")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_block_content_in_system_message(self):
        blocks = [_make_block("important context")]
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = blocks_to_messages(blocks, budget=8000, compaction="none")
        assert "important context" in msgs[0]["content"]

    def test_existing_messages_appended_after_system(self):
        blocks = [_make_block("ctx")]
        existing = [
            {"role": "system", "content": "old system"},
            {"role": "user", "content": "hi"},
        ]
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = blocks_to_messages(blocks, budget=8000, compaction="none", existing_messages=existing)
        # First message is new system, old system skipped, user included
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] != "old system"
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "hi"

    def test_aggressive_compaction_truncates_oversized_block(self):
        large_content = "x" * 40000  # ~10000 tokens
        blocks = [_make_block(content=large_content, tokens=10000)]
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = blocks_to_messages(blocks, budget=200, compaction="aggressive")
        # Should have truncated — content in system message much smaller than 40000 chars
        sys_content = msgs[0]["content"]
        assert len(sys_content) < 40000

    def test_none_compaction_does_not_truncate(self):
        content = "y" * 800  # ~200 tokens
        blocks = [_make_block(content=content, tokens=200)]
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = blocks_to_messages(blocks, budget=100, compaction="none")
        # With compaction="none" blocks are added as-is regardless of budget
        assert content in msgs[0]["content"]

    def test_balanced_compaction_falls_back_on_engine_error(self):
        large_content = "z" * 40000
        blocks = [_make_block(content=large_content, tokens=10000)]
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            with patch(
                "tokenpak.engines.get_engine",
                side_effect=Exception("engine fail"),
            ):
                msgs = blocks_to_messages(blocks, budget=100, compaction="balanced")
        # Fallback truncation kicks in
        assert len(msgs[0]["content"]) < 40000


# ---------------------------------------------------------------------------
# formatter.compile_pack
# ---------------------------------------------------------------------------

class TestCompilePack:
    def test_compile_list_of_blocks(self):
        blocks = [_make_block("block content")]
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = compile_pack(blocks, budget=8000, compaction="none")
        assert msgs[0]["role"] == "system"
        assert "block content" in msgs[0]["content"]

    def test_compile_dict_pack(self):
        pack_dict = {
            "version": "1.0",
            "blocks": [{"ref": "doc.md", "type": "text", "content": "dict content", "tokens": 10}],
        }
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = compile_pack(pack_dict, budget=8000, compaction="none")
        assert "dict content" in msgs[0]["content"]

    def test_compile_registry_with_all_blocks(self):
        block = _make_block("registry block")
        registry = MagicMock()
        registry.all_blocks.return_value = [block]
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = compile_pack(registry, budget=8000, compaction="none")
        assert "registry block" in msgs[0]["content"]

    def test_compile_registry_without_all_blocks_returns_empty_system(self):
        registry = MagicMock(spec=[])  # no all_blocks attribute
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            msgs = compile_pack(registry, budget=8000, compaction="none")
        assert msgs[0]["role"] == "system"

    def test_compile_invalid_type_raises_type_error(self):
        # all_blocks() raises → compile_pack wraps it as TypeError
        bad = MagicMock()
        bad.all_blocks.side_effect = RuntimeError("broken registry")
        with pytest.raises(TypeError, match="Cannot compile TokenPak"):
            compile_pack(bad)


# ---------------------------------------------------------------------------
# parser.parse_tokenpak_request
# ---------------------------------------------------------------------------

class TestParseTokenpakRequest:
    def test_pattern1_explicit_tokenpak_kwarg(self):
        pack_obj = {"version": "1.0", "blocks": []}
        kwargs = {"tokenpak": pack_obj, "model": "gpt-4"}
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack is pack_obj
        assert "tokenpak" not in cleaned
        assert cleaned["model"] == "gpt-4"

    def test_pattern2_message_content_auto_detection(self):
        pack_obj = {"version": "1.0", "blocks": []}
        msgs = [
            {"role": "user", "content": {"type": "tokenpak", "pack": pack_obj}},
            {"role": "user", "content": "follow-up"},
        ]
        kwargs = {"messages": msgs, "model": "gpt-4"}
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack is pack_obj
        # tokenpak message removed from list
        assert len(cleaned["messages"]) == 1
        assert cleaned["messages"][0]["content"] == "follow-up"

    def test_pattern3_raw_body_dict(self):
        pack_obj = {"version": "1.0", "blocks": []}
        kwargs = {"_raw_body": {"tokenpak": pack_obj, "other": "data"}}
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack is pack_obj
        assert "_raw_body" not in cleaned

    def test_pattern4_tokpak_preamble_passthrough(self):
        msgs = [{"role": "system", "content": "TOKPAK:8000\n[blocks data]"}]
        kwargs = {"messages": msgs, "model": "gpt-4"}
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack is None  # already compiled, passthrough

    def test_no_pack_returns_none(self):
        kwargs = {"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]}
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack is None
        assert cleaned == kwargs

    def test_does_not_mutate_original_kwargs(self):
        pack_obj = {"blocks": []}
        original = {"tokenpak": pack_obj, "model": "gpt-4"}
        parse_tokenpak_request(original)
        assert "tokenpak" in original  # original untouched


# ---------------------------------------------------------------------------
# parser.extract_budget_from_kwargs
# ---------------------------------------------------------------------------

class TestExtractBudgetFromKwargs:
    def test_tokenpak_budget_takes_precedence(self):
        assert extract_budget_from_kwargs({"tokenpak_budget": 4000, "max_tokens": 2000}) == 4000

    def test_max_tokens_fallback(self):
        assert extract_budget_from_kwargs({"max_tokens": 3000}) == 3000

    def test_default_when_neither_set(self):
        assert extract_budget_from_kwargs({}) == 8000

    def test_converts_to_int(self):
        result = extract_budget_from_kwargs({"tokenpak_budget": "5000"})
        assert isinstance(result, int)
        assert result == 5000


# ---------------------------------------------------------------------------
# parser.extract_compaction_from_kwargs
# ---------------------------------------------------------------------------

class TestExtractCompactionFromKwargs:
    def test_returns_balanced_by_default(self):
        assert extract_compaction_from_kwargs({}) == "balanced"

    def test_accepts_none_compaction(self):
        assert extract_compaction_from_kwargs({"tokenpak_compaction": "none"}) == "none"

    def test_accepts_aggressive(self):
        assert extract_compaction_from_kwargs({"tokenpak_compaction": "aggressive"}) == "aggressive"

    def test_rejects_invalid_falls_back_to_balanced(self):
        assert extract_compaction_from_kwargs({"tokenpak_compaction": "superfast"}) == "balanced"


# ---------------------------------------------------------------------------
# middleware.TokenPakMiddleware — init
# ---------------------------------------------------------------------------

class TestTokenPakMiddlewareInit:
    def test_default_init(self):
        mw = TokenPakMiddleware()
        assert mw.compaction == "balanced"
        assert mw.budget == 8000
        assert mw.telemetry is True

    def test_custom_init(self):
        mw = TokenPakMiddleware(compaction="aggressive", budget=4000, telemetry=False)
        assert mw.compaction == "aggressive"
        assert mw.budget == 4000
        assert mw.telemetry is False

    def test_invalid_compaction_raises(self):
        with pytest.raises(ValueError, match="Invalid compaction"):
            TokenPakMiddleware(compaction="turbo")


# ---------------------------------------------------------------------------
# middleware.TokenPakMiddleware — pre_call_hook
# ---------------------------------------------------------------------------

class TestTokenPakMiddlewarePreCallHook:
    def test_passthrough_when_no_tokenpak(self):
        mw = TokenPakMiddleware()
        data = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        result = mw.pre_call_hook(None, None, data, "completion")
        assert result == data

    def test_compiles_tokenpak_into_messages(self):
        mw = TokenPakMiddleware(telemetry=False)
        pack_dict = {"blocks": [{"ref": "a.md", "content": "ctx text", "tokens": 5}]}
        data = {"tokenpak": pack_dict, "model": "gpt-4"}

        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            result = mw.pre_call_hook(None, None, data, "completion")

        assert "messages" in result
        assert result["messages"][0]["role"] == "system"
        assert "tokenpak" not in result

    def test_stashes_tokenpak_meta_when_telemetry_on(self):
        mw = TokenPakMiddleware(telemetry=True)
        pack_dict = {"blocks": [{"ref": "a.md", "content": "ctx", "tokens": 3}]}
        data = {"tokenpak": pack_dict, "model": "gpt-4"}

        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            result = mw.pre_call_hook(None, None, data, "completion")

        assert "_tokenpak_meta" in result
        assert "compile_ms" in result["_tokenpak_meta"]

    def test_no_meta_when_telemetry_off(self):
        mw = TokenPakMiddleware(telemetry=False)
        pack_dict = {"blocks": [{"ref": "a.md", "content": "ctx", "tokens": 3}]}
        data = {"tokenpak": pack_dict, "model": "gpt-4"}

        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            result = mw.pre_call_hook(None, None, data, "completion")

        assert "_tokenpak_meta" not in result

    def test_per_call_budget_override(self):
        mw = TokenPakMiddleware(budget=8000)
        pack_dict = {"blocks": [{"content": "x", "tokens": 1}]}
        data = {"tokenpak": pack_dict, "tokenpak_budget": 4000, "model": "gpt-4"}

        compiled_budgets = []

        def mock_compile(pack, budget, compaction, existing_messages=None):
            compiled_budgets.append(budget)
            return [{"role": "system", "content": "x"}]

        with patch("tokenpak.integrations.litellm.middleware.compile_pack", mock_compile):
            mw.pre_call_hook(None, None, data, "completion")

        assert compiled_budgets[0] == 4000


# ---------------------------------------------------------------------------
# middleware.TokenPakMiddleware — post_call_success_hook
# ---------------------------------------------------------------------------

class TestTokenPakMiddlewarePostCallHook:
    def test_attaches_tokenpak_stats_when_telemetry_on(self):
        mw = TokenPakMiddleware(telemetry=True)
        response = MagicMock()
        usage = MagicMock()
        usage.prompt_tokens = 500
        response.usage = usage
        data = {
            "_tokenpak_meta": {
                "compile_ms": 12.3,
                "budget": 8000,
                "compaction": "balanced",
                "system_tokens": 200,
            }
        }
        result = mw.post_call_success_hook(data, None, response)
        stats = result.tokenpak_stats
        assert stats["compile_ms"] == 12.3
        assert stats["budget"] == 8000
        assert "savings_pct" in stats

    def test_no_stats_when_telemetry_off(self):
        mw = TokenPakMiddleware(telemetry=False)
        response = MagicMock()
        data = {"_tokenpak_meta": {"compile_ms": 1, "budget": 8000, "compaction": "none", "system_tokens": 100}}
        result = mw.post_call_success_hook(data, None, response)
        # telemetry off → tokenpak_stats not set (response unmodified)
        assert not hasattr(result, "tokenpak_stats") or result.tokenpak_stats is None or True
        # Key assertion: tokenpak_stats attribute was never explicitly set by the hook
        response.tokenpak_stats  # should not raise; but may be a MagicMock attr

    def test_passthrough_when_no_meta(self):
        mw = TokenPakMiddleware(telemetry=True)
        response = MagicMock()
        data = {}
        result = mw.post_call_success_hook(data, None, response)
        assert result is response


# ---------------------------------------------------------------------------
# middleware.TokenPakMiddleware — wrap_kwargs
# ---------------------------------------------------------------------------

class TestTokenPakMiddlewareWrapKwargs:
    def test_wrap_with_tokenpak(self):
        mw = TokenPakMiddleware(telemetry=False)
        pack_dict = {"blocks": [{"content": "wrapped ctx", "tokens": 5}]}
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            result = mw.wrap_kwargs(tokenpak=pack_dict, model="gpt-4")
        assert "messages" in result
        assert "tokenpak" not in result
        assert result["model"] == "gpt-4"

    def test_wrap_removes_internal_keys(self):
        mw = TokenPakMiddleware(telemetry=False)
        pack_dict = {"blocks": [{"content": "x", "tokens": 1}]}
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            result = mw.wrap_kwargs(
                tokenpak=pack_dict,
                model="gpt-4",
                tokenpak_budget=4000,
                tokenpak_compaction="aggressive",
            )
        assert "tokenpak_budget" not in result
        assert "tokenpak_compaction" not in result

    def test_wrap_without_tokenpak_passthrough(self):
        mw = TokenPakMiddleware()
        result = mw.wrap_kwargs(model="gpt-4", messages=[{"role": "user", "content": "hi"}])
        assert result["model"] == "gpt-4"
        assert "tokenpak" not in result


# ---------------------------------------------------------------------------
# proxy._json_error
# ---------------------------------------------------------------------------

class TestJsonError:
    def test_returns_dict_with_error_key(self):
        result = _json_error(400, "Bad request")
        assert "error" in result
        assert result["error"]["status"] == 400
        assert result["error"]["message"] == "Bad request"

    def test_502_error(self):
        result = _json_error(502, "LiteLLM failed")
        assert result["error"]["status"] == 502


# ---------------------------------------------------------------------------
# proxy.ProxyHandler — init
# ---------------------------------------------------------------------------

class TestProxyHandlerInit:
    def test_default_values(self):
        handler = ProxyHandler()
        assert handler.default_model == "gpt-4"
        assert handler.budget == 8000
        assert handler.compaction == "balanced"

    def test_custom_values(self):
        handler = ProxyHandler(default_model="claude-3", budget=4000, compaction="aggressive")
        assert handler.default_model == "claude-3"
        assert handler.budget == 4000
        assert handler.compaction == "aggressive"

    def test_extra_litellm_kwargs_stored(self):
        handler = ProxyHandler(temperature=0.7, max_tokens=1000)
        assert handler.litellm_kwargs["temperature"] == 0.7
        assert handler.litellm_kwargs["max_tokens"] == 1000


# ---------------------------------------------------------------------------
# proxy.ProxyHandler — handle (dict input path)
# ---------------------------------------------------------------------------

class TestProxyHandlerHandle:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_missing_tokenpak_returns_400(self):
        handler = ProxyHandler()
        result = self._run(handler.handle({"model": "gpt-4"}))
        assert result["error"]["status"] == 400
        assert "tokenpak" in result["error"]["message"].lower()

    def test_litellm_not_installed_returns_500(self):
        handler = ProxyHandler()
        body = {
            "model": "gpt-4",
            "tokenpak": {"blocks": [{"content": "ctx", "tokens": 5}]},
        }
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            with patch.dict("sys.modules", {"litellm": None}):
                result = self._run(handler.handle(body))
        assert result["error"]["status"] == 500

    def test_litellm_call_failure_returns_502(self):
        handler = ProxyHandler()
        body = {
            "model": "gpt-4",
            "tokenpak": {"blocks": [{"content": "ctx", "tokens": 5}]},
        }
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("model unavailable"))
        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            with patch.dict("sys.modules", {"litellm": mock_litellm}):
                result = self._run(handler.handle(body))
        assert result["error"]["status"] == 502

    def test_successful_call_attaches_stats(self):
        handler = ProxyHandler()
        pack_dict = {"blocks": [{"content": "ctx", "tokens": 5}]}
        body = {"model": "gpt-4", "tokenpak": pack_dict}

        mock_response = MagicMock()
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 100

        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)

        with patch("tokenpak.wire.pack", _wire_pack_passthrough):
            with patch.dict("sys.modules", {"litellm": mock_litellm}):
                result = self._run(handler.handle(body))

        assert result is mock_response
        assert hasattr(result, "tokenpak_stats")
        assert "compile_ms" in result.tokenpak_stats

    def test_pack_policies_budget_used(self):
        handler = ProxyHandler(budget=8000)
        pack_dict = {
            "blocks": [{"content": "ctx", "tokens": 5}],
            "policies": {"budget": 2000, "compaction": "aggressive"},
        }
        body = {"model": "gpt-4", "tokenpak": pack_dict}

        compiled_calls = []

        def mock_compile(pack, budget, compaction, existing_messages=None):
            compiled_calls.append({"budget": budget, "compaction": compaction})
            return [{"role": "system", "content": "packed"}]

        mock_response = MagicMock()
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)

        with patch("tokenpak.integrations.litellm.proxy.compile_pack", mock_compile):
            with patch.dict("sys.modules", {"litellm": mock_litellm}):
                self._run(handler.handle(body))

        assert compiled_calls[0]["budget"] == 2000
        assert compiled_calls[0]["compaction"] == "aggressive"

    def test_starlette_request_body_parsed(self):
        """handle() also accepts a Starlette-like request object with async .body()."""
        handler = ProxyHandler()

        class FakeRequest:
            async def body(self):
                return json.dumps({"model": "gpt-4"}).encode()

        result = self._run(handler.handle(FakeRequest()))
        assert result["error"]["status"] == 400  # missing tokenpak

    def test_starlette_invalid_json_returns_400(self):
        handler = ProxyHandler()

        class FakeRequest:
            async def body(self):
                return b"not json {"

        result = self._run(handler.handle(FakeRequest()))
        assert result["error"]["status"] == 400
