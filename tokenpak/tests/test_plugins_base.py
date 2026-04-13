"""Unit tests for tokenpak/plugins/base.py — CompressorPlugin ABC."""

import pytest

from tokenpak.plugins.base import CompressorPlugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MinimalPlugin(CompressorPlugin):
    """Concrete subclass with minimal implementation."""
    name = "minimal"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text, "metadata": {}}


class HighPriorityPlugin(CompressorPlugin):
    """Concrete subclass that overrides priority."""
    name = "high_priority"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text.upper(), "metadata": {"changed": True}}

    def priority(self) -> int:
        return 99


class NoNamePlugin(CompressorPlugin):
    """Concrete subclass that leaves name as empty string."""

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text, "metadata": {}}


# ---------------------------------------------------------------------------
# Abstract enforcement
# ---------------------------------------------------------------------------

class TestCompressorPluginABC:
    def test_cannot_instantiate_abc_directly(self):
        """CompressorPlugin is abstract — direct instantiation must fail."""
        with pytest.raises(TypeError):
            CompressorPlugin()  # type: ignore[abstract]

    def test_must_implement_compress(self):
        """Subclass without compress() cannot be instantiated."""
        class IncompletePlugin(CompressorPlugin):
            name = "incomplete"
            # compress() not implemented

        with pytest.raises(TypeError):
            IncompletePlugin()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self):
        p = MinimalPlugin()
        assert isinstance(p, CompressorPlugin)


# ---------------------------------------------------------------------------
# name attribute
# ---------------------------------------------------------------------------

class TestPluginName:
    def test_name_attribute_set(self):
        p = MinimalPlugin()
        assert p.name == "minimal"

    def test_name_default_empty_string(self):
        """Default name is empty string (class attribute)."""
        p = NoNamePlugin()
        assert p.name == ""

    def test_name_class_attribute(self):
        """name is accessible on both class and instance."""
        assert MinimalPlugin.name == "minimal"
        assert MinimalPlugin().name == "minimal"


# ---------------------------------------------------------------------------
# priority()
# ---------------------------------------------------------------------------

class TestPluginPriority:
    def test_default_priority(self):
        p = MinimalPlugin()
        assert p.priority() == 50

    def test_custom_priority(self):
        p = HighPriorityPlugin()
        assert p.priority() == 99

    def test_priority_returns_int(self):
        p = MinimalPlugin()
        assert isinstance(p.priority(), int)


# ---------------------------------------------------------------------------
# compress() contract
# ---------------------------------------------------------------------------

class TestPluginCompress:
    def test_compress_returns_dict(self):
        p = MinimalPlugin()
        result = p.compress("hello", {})
        assert isinstance(result, dict)

    def test_compress_result_has_text_key(self):
        p = MinimalPlugin()
        result = p.compress("hello", {})
        assert "text" in result

    def test_compress_result_has_metadata_key(self):
        p = MinimalPlugin()
        result = p.compress("hello", {})
        assert "metadata" in result

    def test_compress_passes_context(self):
        """context dict is received by compress — no crash."""
        p = MinimalPlugin()
        ctx = {"model": "claude", "request_id": "abc-123"}
        result = p.compress("some text", ctx)
        assert result["text"] == "some text"

    def test_compress_empty_string(self):
        p = MinimalPlugin()
        result = p.compress("", {})
        assert result["text"] == ""

    def test_compress_high_priority_modifies_text(self):
        p = HighPriorityPlugin()
        result = p.compress("hello", {})
        assert result["text"] == "HELLO"
        assert result["metadata"]["changed"] is True
