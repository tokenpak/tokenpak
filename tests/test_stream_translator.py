"""Tests for tokenpak.proxy.providers.stream_translator module."""

from tokenpak.proxy.providers.stream_translator import StreamingTranslator


class TestStreamingTranslator:
    def test_translator_init(self):
        translator = StreamingTranslator("anthropic", "openai")
        assert translator is not None
        assert translator.source == "anthropic"
        assert translator.target == "openai"

    def test_translator_is_class(self):
        assert StreamingTranslator is not None

    def test_translator_callable(self):
        translator = StreamingTranslator("openai", "anthropic")
        assert hasattr(translator, "__class__")

    def test_translator_methods_exist(self):
        translator = StreamingTranslator("anthropic", "openai")
        assert hasattr(translator, "translate_chunk")

    def test_multiple_translators(self):
        t1 = StreamingTranslator("anthropic", "openai")
        t2 = StreamingTranslator("openai", "anthropic")
        assert t1 is not None
        assert t2 is not None
        assert t1.source != t2.source
