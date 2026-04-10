# SPDX-License-Identifier: Apache-2.0
"""Tests for _pro_hooks.py and wire.py.

Covers:
  - Hook registration / deregistration across all registry types
  - Getter return values (present / missing)
  - CLI command structure
  - Plugin discovery (entry_points) mocking
  - wire.make_slice_id edge cases
  - wire.pack edge cases: empty blocks, provenance attrs, metadata
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import tokenpak._pro_hooks as ph
from tokenpak.wire import make_slice_id, pack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_registries():
    """Reset all _pro_hooks registries between tests."""
    ph._compression.clear()
    ph._router.clear()
    ph._cli_commands.clear()
    ph._dashboard_pages.clear()
    ph._telemetry.clear()
    ph._agentic.clear()
    ph._adapters.clear()


# ---------------------------------------------------------------------------
# _pro_hooks.py tests (≥ 6)
# ---------------------------------------------------------------------------


class TestProHooksRegistration:
    """Test register_* functions and get_* getters."""

    def setup_method(self):
        _clear_registries()

    def test_register_and_get_compression(self):
        """Registered compression impl is retrievable by name."""
        impl = MagicMock(name="compression_impl")
        ph.register_compression("zstd", impl)
        assert ph.get_compression("zstd") is impl

    def test_get_compression_missing_returns_none(self):
        """Missing compression name returns None."""
        assert ph.get_compression("nonexistent") is None

    def test_register_and_get_router(self):
        """Registered router impl is retrievable."""
        impl = MagicMock(name="router_impl")
        ph.register_router("smart", impl)
        assert ph.get_router("smart") is impl

    def test_get_router_missing_returns_none(self):
        assert ph.get_router("missing_router") is None

    def test_register_cli_command_stores_desc_and_fn(self):
        """CLI command registry stores description and callable."""
        fn = MagicMock()
        ph.register_cli_command("analyze", "Run analysis", fn)
        cmds = ph.get_cli_commands()
        assert "analyze" in cmds
        assert cmds["analyze"]["desc"] == "Run analysis"
        assert cmds["analyze"]["fn"] is fn

    def test_get_cli_commands_returns_copy(self):
        """get_cli_commands() returns a copy; adding new keys doesn't affect the registry."""
        ph.register_cli_command("cmd1", "desc1", MagicMock())
        cmds = ph.get_cli_commands()
        # Add a key that shouldn't appear in the next call
        cmds["injected_key"] = "should not appear"
        assert "injected_key" not in ph.get_cli_commands()

    def test_register_and_get_telemetry(self):
        impl = MagicMock(name="telemetry_impl")
        ph.register_telemetry("prometheus", impl)
        assert ph.get_telemetry("prometheus") is impl

    def test_get_telemetry_missing_returns_none(self):
        assert ph.get_telemetry("not_registered") is None

    def test_register_and_get_agentic(self):
        impl = MagicMock(name="agentic_feature")
        ph.register_agentic("memory", impl)
        assert ph.get_agentic("memory") is impl

    def test_get_agentic_missing_returns_none(self):
        assert ph.get_agentic("unregistered") is None

    def test_register_and_get_adapter(self):
        impl = MagicMock(name="adapter_impl")
        ph.register_adapter("openai", impl)
        adapters = ph.get_adapters()
        assert "openai" in adapters
        assert adapters["openai"] is impl

    def test_get_adapters_returns_copy(self):
        """get_adapters() returns a copy; mutations don't affect registry."""
        impl = MagicMock()
        ph.register_adapter("copy_test", impl)
        adapters = ph.get_adapters()
        del adapters["copy_test"]
        assert "copy_test" in ph.get_adapters()

    def test_register_dashboard_page_and_retrieve(self):
        impl = MagicMock(name="dashboard_page")
        ph.register_dashboard_page("overview", impl)
        pages = ph.get_dashboard_pages()
        assert "overview" in pages
        assert pages["overview"] is impl

    def test_overwrite_compression_registration(self):
        """Re-registering with the same name replaces the previous implementation."""
        first = MagicMock(name="first")
        second = MagicMock(name="second")
        ph.register_compression("lz4", first)
        ph.register_compression("lz4", second)
        assert ph.get_compression("lz4") is second

    def test_multiple_registrations_are_independent(self):
        """Different names in the same registry don't interfere."""
        impl_a = MagicMock(name="a")
        impl_b = MagicMock(name="b")
        ph.register_router("routerA", impl_a)
        ph.register_router("routerB", impl_b)
        assert ph.get_router("routerA") is impl_a
        assert ph.get_router("routerB") is impl_b


class TestProHooksPluginDiscovery:
    """Test _load_plugins entry_points discovery."""

    def setup_method(self):
        _clear_registries()

    def test_load_plugins_swallows_broken_entry_point(self):
        """A plugin that raises during load() should not crash core."""
        bad_ep = MagicMock()
        bad_ep.load.side_effect = ImportError("missing dep")

        with patch("importlib.metadata.entry_points", return_value=[bad_ep]):
            # Should not raise
            ph._load_plugins()

    def test_load_plugins_calls_register_on_plugin(self):
        """A well-formed plugin's register() method is called."""
        plugin_module = MagicMock()
        plugin_module.register = MagicMock()
        ep = MagicMock()
        ep.load.return_value = plugin_module

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            ph._load_plugins()

        plugin_module.register.assert_called_once()

    def test_load_plugins_skips_plugin_without_register(self):
        """A plugin without a register callable is silently skipped."""
        plugin_module = object()  # no `register` attribute
        ep = MagicMock()
        ep.load.return_value = plugin_module

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            ph._load_plugins()  # should not raise

    def test_load_plugins_swallows_metadata_error(self):
        """If entry_points() itself raises, _load_plugins doesn't propagate it."""
        with patch("importlib.metadata.entry_points", side_effect=Exception("meta err")):
            ph._load_plugins()  # should not raise


# ---------------------------------------------------------------------------
# wire.py additional edge-case tests (≥ 6)
# ---------------------------------------------------------------------------


class TestMakeSliceIdEdgeCases:
    """Edge-case tests for make_slice_id."""

    def test_empty_content_and_ref(self):
        """Empty strings produce a valid s_XXXXXXXX id."""
        result = make_slice_id("", "")
        assert result.startswith("s_")
        assert len(result) == 10

    def test_unicode_content(self):
        """Unicode content is handled without error."""
        result = make_slice_id("こんにちは", "unicode/ref")
        assert result.startswith("s_")
        assert len(result) == 10

    def test_long_content_truncated_to_8_hex(self):
        """Regardless of content length, digest is always 8 hex chars."""
        result = make_slice_id("x" * 10_000, "big/ref")
        assert len(result) == 10

    def test_ref_only_affects_id(self):
        """Changing only the ref produces a different id."""
        c = "same content"
        id_a = make_slice_id(c, "ref_a")
        id_b = make_slice_id(c, "ref_b")
        assert id_a != id_b


class TestPackEdgeCases:
    """Edge-case tests for pack."""

    def test_pack_empty_blocks(self):
        """Zero blocks produces a valid TOKPAK header with BLOCKS: 0."""
        result = pack([], budget=1000)
        assert "TOKPAK:1" in result
        assert "BLOCKS: 0" in result
        assert "BUDGET: {max:1000, used:0}" in result

    def test_pack_metadata_included(self):
        """Metadata key=value pairs appear in the output."""
        result = pack([], budget=500, metadata={"agent": "cali", "version": "1"})
        assert "META:" in result
        assert "agent=cali" in result
        assert "version=1" in result

    def test_pack_block_with_preset_slice_id(self):
        """A block that already has slice_id uses it rather than generating a new one."""
        block = {
            "ref": "some/file",
            "type": "context",
            "quality": 0.9,
            "tokens": 50,
            "content": "hello",
            "slice_id": "s_deadbeef",
        }
        result = pack([block], budget=100)
        assert "s_deadbeef" in result

    def test_pack_provenance_with_attributes(self):
        """Provenance SimpleNamespace attributes are rendered in the header."""
        prov = SimpleNamespace(source_type="vault", source_id="doc/test", source_version="abc123def456gh")
        block = {
            "ref": "vault/doc",
            "type": "document",
            "quality": 1.0,
            "tokens": 10,
            "content": "body text",
            "provenance": prov,
        }
        result = pack([block], budget=200)
        assert "[SOURCE: vault:doc/test]" in result
        assert "[VERSION: abc123def456" in result  # truncated to 16

    def test_pack_provenance_with_dict(self):
        """Provenance as a plain dict is also rendered correctly."""
        prov = {"source_type": "api", "source_id": "endpoint/v1", "source_version": ""}
        block = {
            "ref": "api/endpoint",
            "type": "response",
            "quality": 0.8,
            "tokens": 20,
            "content": "api response data",
            "provenance": prov,
        }
        result = pack([block], budget=300)
        assert "[SOURCE: api:endpoint/v1]" in result

    def test_pack_provenance_missing_source_id_skipped(self):
        """Provenance dict without source_id does not add [SOURCE:] tag."""
        # Use a plain dict so both getattr and .get() work without error
        prov = {"source_type": "vault", "source_id": "", "source_version": ""}
        block = {
            "ref": "test",
            "type": "note",
            "quality": 1.0,
            "tokens": 5,
            "content": "short",
            "provenance": prov,
        }
        result = pack([block], budget=50)
        assert "[SOURCE:" not in result

    def test_pack_multiple_blocks_token_sum(self):
        """Used token count is the sum of all blocks."""
        blocks = [
            {"ref": "a", "type": "t", "quality": 1.0, "tokens": 100, "content": "AAA"},
            {"ref": "b", "type": "t", "quality": 1.0, "tokens": 200, "content": "BBB"},
        ]
        result = pack(blocks, budget=500)
        assert "used:300" in result

    def test_pack_content_stripped(self):
        """Block content is stripped of leading/trailing whitespace."""
        block = {
            "ref": "whitespace",
            "type": "raw",
            "quality": 1.0,
            "tokens": 5,
            "content": "   trimmed   ",
        }
        result = pack([block], budget=100)
        # Content appears stripped in the output
        lines = result.splitlines()
        # The content line should be the stripped version
        content_lines = [l for l in lines if l == "trimmed"]
        assert len(content_lines) == 1
