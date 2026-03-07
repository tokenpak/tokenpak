"""Tests for TokenPak × LiteLLM integration.

These tests do NOT require litellm or an API key — they exercise the
formatter, parser, and middleware in isolation.
"""

import sys
import types
import pytest
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(path="docs/intro.md", content="This is the content of the intro.", tokens=10):
    return SimpleNamespace(
        path=path,
        compressed_content=content,
        content=content,
        file_type="markdown",
        quality_score=1.0,
        compressed_tokens=tokens,
        raw_tokens=tokens,
        slice_id="",
    )


# ---------------------------------------------------------------------------
# formatter.py
# ---------------------------------------------------------------------------

class TestFormatter:
    def test_blocks_to_messages_basic(self):
        from tokenpak.integrations.litellm.formatter import blocks_to_messages

        blocks = [_make_block()]
        messages = blocks_to_messages(blocks, budget=8000)

        assert len(messages) >= 1
        system = messages[0]
        assert system["role"] == "system"
        assert "TOKPAK:1" in system["content"]

    def test_blocks_to_messages_appends_existing(self):
        from tokenpak.integrations.litellm.formatter import blocks_to_messages

        blocks = [_make_block()]
        existing = [{"role": "user", "content": "Hello"}]
        messages = blocks_to_messages(blocks, existing_messages=existing)

        roles = [m["role"] for m in messages]
        assert roles[0] == "system"
        assert "user" in roles

    def test_blocks_to_messages_skips_existing_system(self):
        from tokenpak.integrations.litellm.formatter import blocks_to_messages

        blocks = [_make_block()]
        existing = [
            {"role": "system", "content": "Old system prompt"},
            {"role": "user", "content": "Hello"},
        ]
        messages = blocks_to_messages(blocks, existing_messages=existing)
        # Only one system message (the compiled tokenpak)
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert "TOKPAK:1" in system_msgs[0]["content"]

    def test_compile_pack_from_list(self):
        from tokenpak.integrations.litellm.formatter import compile_pack

        blocks = [_make_block(), _make_block(path="docs/api.md", content="API reference")]
        messages = compile_pack(blocks, budget=4000)
        assert messages[0]["role"] == "system"
        assert "TOKPAK:1" in messages[0]["content"]

    def test_compile_pack_from_dict(self):
        from tokenpak.integrations.litellm.formatter import compile_pack

        pack_dict = {
            "version": "1.0",
            "blocks": [
                {"ref": "intro", "type": "text", "content": "Intro content.", "tokens": 5},
            ],
        }
        messages = compile_pack(pack_dict)
        assert messages[0]["role"] == "system"
        assert "TOKPAK:1" in messages[0]["content"]

    def test_compaction_aggressive_truncates(self):
        from tokenpak.integrations.litellm.formatter import blocks_to_messages

        long_content = "word " * 10000  # ~50k chars
        blocks = [_make_block(content=long_content, tokens=12500)]
        messages = blocks_to_messages(blocks, budget=100, compaction="aggressive")
        system = messages[0]["content"]
        # Should be truncated; content in TOKPAK block should be short
        assert len(system) < len(long_content)


# ---------------------------------------------------------------------------
# parser.py
# ---------------------------------------------------------------------------

class TestParser:
    def test_explicit_tokenpak_kwarg(self):
        from tokenpak.integrations.litellm.parser import parse_tokenpak_request

        fake_pack = object()
        pack, cleaned = parse_tokenpak_request({"model": "gpt-4", "tokenpak": fake_pack})
        assert pack is fake_pack
        assert "tokenpak" not in cleaned

    def test_message_content_auto_detect(self):
        from tokenpak.integrations.litellm.parser import parse_tokenpak_request

        pack_data = {"version": "1.0", "blocks": []}
        kwargs = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": {"type": "tokenpak", "pack": pack_data}},
            ],
        }
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack == pack_data
        # The tokenpak message should have been removed
        assert all(
            not (isinstance(m.get("content"), dict) and m["content"].get("type") == "tokenpak")
            for m in cleaned.get("messages", [])
        )

    def test_no_tokenpak_returns_none(self):
        from tokenpak.integrations.litellm.parser import parse_tokenpak_request

        kwargs = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack is None
        assert cleaned["messages"][0]["content"] == "Hi"

    def test_existing_tokpak_wire_format_passthrough(self):
        from tokenpak.integrations.litellm.parser import parse_tokenpak_request

        wire = "TOKPAK:1\nBUDGET: {max:8000, used:100}\nBLOCKS: 1\n---\n[REF: x]\ncontent\n---"
        kwargs = {
            "model": "gpt-4",
            "messages": [{"role": "system", "content": wire}],
        }
        pack, cleaned = parse_tokenpak_request(kwargs)
        assert pack is None  # Already compiled — pass through

    def test_extract_budget_default(self):
        from tokenpak.integrations.litellm.parser import extract_budget_from_kwargs
        assert extract_budget_from_kwargs({}) == 8000

    def test_extract_budget_override(self):
        from tokenpak.integrations.litellm.parser import extract_budget_from_kwargs
        assert extract_budget_from_kwargs({"tokenpak_budget": 4000}) == 4000

    def test_extract_compaction_default(self):
        from tokenpak.integrations.litellm.parser import extract_compaction_from_kwargs
        assert extract_compaction_from_kwargs({}) == "balanced"

    def test_extract_compaction_override(self):
        from tokenpak.integrations.litellm.parser import extract_compaction_from_kwargs
        assert extract_compaction_from_kwargs({"tokenpak_compaction": "aggressive"}) == "aggressive"

    def test_invalid_compaction_falls_back(self):
        from tokenpak.integrations.litellm.parser import extract_compaction_from_kwargs
        assert extract_compaction_from_kwargs({"tokenpak_compaction": "unknown"}) == "balanced"


# ---------------------------------------------------------------------------
# middleware.py
# ---------------------------------------------------------------------------

class TestTokenPakMiddleware:
    def test_init_default(self):
        from tokenpak.integrations.litellm.middleware import TokenPakMiddleware
        mw = TokenPakMiddleware()
        assert mw.compaction == "balanced"
        assert mw.budget == 8000

    def test_invalid_compaction_raises(self):
        from tokenpak.integrations.litellm.middleware import TokenPakMiddleware
        with pytest.raises(ValueError):
            TokenPakMiddleware(compaction="invalid")

    def test_wrap_kwargs_compiles_pack(self):
        from tokenpak.integrations.litellm.middleware import TokenPakMiddleware

        mw = TokenPakMiddleware()
        blocks = [_make_block()]
        result = mw.wrap_kwargs(model="gpt-4", tokenpak=blocks)

        assert "tokenpak" not in result
        assert "messages" in result
        assert result["messages"][0]["role"] == "system"
        assert "TOKPAK:1" in result["messages"][0]["content"]

    def test_wrap_kwargs_no_tokenpak_passthrough(self):
        from tokenpak.integrations.litellm.middleware import TokenPakMiddleware

        mw = TokenPakMiddleware()
        kwargs = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        result = mw.wrap_kwargs(**kwargs)
        assert result == kwargs

    def test_pre_call_hook_injects_messages(self):
        from tokenpak.integrations.litellm.middleware import TokenPakMiddleware

        mw = TokenPakMiddleware()
        blocks = [_make_block()]
        data = {"model": "gpt-4", "tokenpak": blocks}
        result = mw.pre_call_hook(None, None, data, "completion")

        assert "tokenpak" not in result
        assert result["messages"][0]["role"] == "system"

    def test_post_call_hook_attaches_stats(self):
        from tokenpak.integrations.litellm.middleware import TokenPakMiddleware

        mw = TokenPakMiddleware()
        blocks = [_make_block()]
        data = {"model": "gpt-4", "tokenpak": blocks}
        processed = mw.pre_call_hook(None, None, data, "completion")

        # Simulate a response object
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=150))
        mw.post_call_success_hook(processed, None, response)

        assert hasattr(response, "tokenpak_stats")
        assert "compile_ms" in response.tokenpak_stats
        assert "budget" in response.tokenpak_stats


# ---------------------------------------------------------------------------
# proxy.py
# ---------------------------------------------------------------------------

class TestProxyHandler:
    def test_missing_tokenpak_returns_error(self):
        import asyncio
        from tokenpak.integrations.litellm.proxy import ProxyHandler

        handler = ProxyHandler()

        async def run():
            return await handler.handle({"model": "gpt-4"})

        result = asyncio.run(run())
        assert "error" in result
        assert result["error"]["status"] == 400

    def test_process_compiles_when_litellm_absent(self, monkeypatch):
        """If litellm not installed, should return 500."""
        import asyncio
        import sys

        # Temporarily hide litellm
        orig = sys.modules.get("litellm", None)
        sys.modules["litellm"] = None  # type: ignore

        from tokenpak.integrations.litellm.proxy import ProxyHandler
        handler = ProxyHandler()

        async def run():
            return await handler._process({
                "model": "gpt-4",
                "tokenpak": {"version": "1.0", "blocks": []},
            })

        result = asyncio.run(run())
        assert "error" in result
        assert result["error"]["status"] == 500

        # Restore
        if orig is not None:
            sys.modules["litellm"] = orig
        else:
            del sys.modules["litellm"]
