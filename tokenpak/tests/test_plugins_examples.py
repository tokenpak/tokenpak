"""Unit tests for tokenpak/plugins/examples/passthrough.py — PassthroughPlugin."""


from tokenpak.plugins.base import CompressorPlugin
from tokenpak.plugins.examples.passthrough import PassthroughPlugin


class TestPassthroughPlugin:
    def test_is_compressor_plugin_subclass(self):
        assert issubclass(PassthroughPlugin, CompressorPlugin)

    def test_instantiates(self):
        p = PassthroughPlugin()
        assert isinstance(p, PassthroughPlugin)

    def test_name_is_passthrough(self):
        p = PassthroughPlugin()
        assert p.name == "passthrough"

    def test_default_priority(self):
        p = PassthroughPlugin()
        assert p.priority() == 50

    def test_compress_returns_dict(self):
        p = PassthroughPlugin()
        result = p.compress("hello world", {})
        assert isinstance(result, dict)

    def test_compress_text_unchanged(self):
        p = PassthroughPlugin()
        text = "This is a test sentence."
        result = p.compress(text, {})
        assert result["text"] == text

    def test_compress_has_metadata(self):
        p = PassthroughPlugin()
        result = p.compress("x", {})
        assert "metadata" in result

    def test_compress_metadata_plugin_field(self):
        p = PassthroughPlugin()
        result = p.compress("x", {})
        assert result["metadata"]["plugin"] == "passthrough"

    def test_compress_metadata_changed_is_false(self):
        p = PassthroughPlugin()
        result = p.compress("x", {})
        assert result["metadata"]["changed"] is False

    def test_compress_empty_text(self):
        p = PassthroughPlugin()
        result = p.compress("", {})
        assert result["text"] == ""
        assert result["metadata"]["changed"] is False

    def test_compress_with_context(self):
        p = PassthroughPlugin()
        ctx = {"model": "claude-3-5-sonnet", "mode": "aggressive"}
        result = p.compress("hello", ctx)
        assert result["text"] == "hello"

    def test_compress_multiline_text(self):
        p = PassthroughPlugin()
        text = "line one\nline two\nline three"
        result = p.compress(text, {})
        assert result["text"] == text

    def test_compress_unicode_text(self):
        p = PassthroughPlugin()
        text = "こんにちは世界"
        result = p.compress(text, {})
        assert result["text"] == text

    def test_compress_large_text(self):
        p = PassthroughPlugin()
        text = "a" * 100_000
        result = p.compress(text, {})
        assert result["text"] == text
        assert len(result["text"]) == 100_000
