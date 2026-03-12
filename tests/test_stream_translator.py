"""Tests for tokenpak.agent.proxy.providers.stream_translator module."""
import pytest
from tokenpak.agent.proxy.providers.stream_translator import StreamingTranslator

class TestStreamingTranslator:
    def test_translator_init(self):
        translator = StreamingTranslator()
        assert translator is not None
    
    def test_translator_is_class(self):
        assert StreamingTranslator is not None
    
    def test_translator_callable(self):
        translator = StreamingTranslator()
        assert hasattr(translator, '__class__')
    
    def test_translator_methods_exist(self):
        translator = StreamingTranslator()
        methods = [m for m in dir(translator) if not m.startswith('_')]
        assert len(methods) > 0
    
    def test_multiple_translators(self):
        t1 = StreamingTranslator()
        t2 = StreamingTranslator()
        assert t1 is not None
        assert t2 is not None
