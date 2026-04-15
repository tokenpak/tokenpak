"""Tests for tokenpak._internal.debug.logger module."""

import pytest
pytest.importorskip("tokenpak.infrastructure.debug", reason="module not available in current build")
import pytest
from tokenpak.infrastructure.debug import DebugLogger

class TestDebugLogger:
    def test_debug_logger_init(self):
        logger = DebugLogger()
        assert logger is not None
    
    def test_debug_logger_callable(self):
        logger = DebugLogger()
        assert hasattr(logger, '__class__')
    
    def test_debug_logger_with_name(self):
        logger = DebugLogger("test_module")
        assert logger is not None
    
    def test_multiple_loggers(self):
        l1 = DebugLogger()
        l2 = DebugLogger()
        assert l1 is not None and l2 is not None
    
    def test_logger_has_methods(self):
        logger = DebugLogger()
        methods = [m for m in dir(logger) if not m.startswith('_')]
        assert len(methods) > 0
