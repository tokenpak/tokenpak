"""Tests for TokenPak exception hierarchy and logging config."""

import json
import logging
import os
from io import StringIO
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------

class TestTokenPakError:
    def test_base_exception(self):
        from tokenpak.infrastructure.error_handling import TokenPakError
        e = TokenPakError("something failed")
        assert str(e) == "something failed"
        assert e.message == "something failed"
        assert e.error_type == "TokenPakError"

    def test_to_dict(self):
        from tokenpak.infrastructure.error_handling import TokenPakError
        e = TokenPakError("bad thing", detail={"code": 42})
        d = e.to_dict()
        assert d["error"]["type"] == "TokenPakError"
        assert d["error"]["message"] == "bad thing"
        assert d["error"]["detail"] == {"code": 42}

    def test_custom_error_type(self):
        from tokenpak.infrastructure.error_handling import TokenPakError
        e = TokenPakError("msg", error_type="custom_type")
        assert e.error_type == "custom_type"


class TestProxyError:
    def test_is_tokenpak_error(self):
        from tokenpak.infrastructure.error_handling import ProxyError, TokenPakError
        e = ProxyError("proxy failed")
        assert isinstance(e, TokenPakError)

    def test_upstream_error(self):
        from tokenpak.infrastructure.error_handling import ProxyError, TokenPakError, UpstreamError
        e = UpstreamError("upstream 429", status_code=429, provider="anthropic")
        assert isinstance(e, ProxyError)
        assert isinstance(e, TokenPakError)
        assert e.status_code == 429
        assert e.provider == "anthropic"
        d = e.to_dict()
        assert d["error"]["status_code"] == 429
        assert d["error"]["provider"] == "anthropic"

    def test_circuit_open_error(self):
        from tokenpak.infrastructure.error_handling import CircuitOpenError, ProxyError
        e = CircuitOpenError("anthropic", retry_after=30.0)
        assert isinstance(e, ProxyError)
        assert e.provider == "anthropic"
        assert e.retry_after == 30.0
        assert "30" in str(e)

    def test_circuit_open_no_retry(self):
        from tokenpak.infrastructure.error_handling import CircuitOpenError
        e = CircuitOpenError("openai")
        assert "openai" in str(e)
        assert e.retry_after is None


class TestSpecificErrors:
    def test_compression_error(self):
        from tokenpak.infrastructure.error_handling import CompressionError, TokenPakError
        e = CompressionError("failed to compress")
        assert isinstance(e, TokenPakError)

    def test_config_error(self):
        from tokenpak.infrastructure.error_handling import ConfigError
        e = ConfigError("bad config", config_path="/etc/tokenpak.json")
        assert e.config_path == "/etc/tokenpak.json"

    def test_auth_error(self):
        from tokenpak.infrastructure.error_handling import AuthError, TokenPakError
        e = AuthError("invalid key")
        assert isinstance(e, TokenPakError)

    def test_rate_limit_error(self):
        from tokenpak.infrastructure.error_handling import RateLimitError
        e = RateLimitError("too fast", retry_after=5.0, provider="openai")
        assert e.retry_after == 5.0
        assert e.provider == "openai"

    def test_cache_error(self):
        from tokenpak.infrastructure.error_handling import CacheError, TokenPakError
        e = CacheError("cache miss")
        assert isinstance(e, TokenPakError)

    def test_validation_error(self):
        from tokenpak.infrastructure.error_handling import ValidationError
        e = ValidationError("bad input", field="model")
        assert e.field == "model"

    def test_license_error(self):
        from tokenpak.infrastructure.error_handling import LicenseError
        e = LicenseError("upgrade required", required_tier="pro", current_tier="oss")
        assert e.required_tier == "pro"
        assert e.current_tier == "oss"


class TestExceptionCatch:
    def test_catch_all_with_base(self):
        from tokenpak.infrastructure.error_handling import (
            AuthError,
            CacheError,
            CircuitOpenError,
            CompressionError,
            ConfigError,
            LicenseError,
            ProxyError,
            RateLimitError,
            TokenPakError,
            UpstreamError,
            ValidationError,
        )
        subtypes = [
            ProxyError("p"), UpstreamError("u"), CircuitOpenError("prov"),
            CompressionError("c"), ConfigError("cfg"), AuthError("a"),
            RateLimitError("r"), CacheError("ca"), ValidationError("v"),
            LicenseError("l"),
        ]
        for exc in subtypes:
            try:
                raise exc
            except TokenPakError:
                pass  # all should be caught here


# ---------------------------------------------------------------------------
# Logging config tests
# ---------------------------------------------------------------------------

class TestLoggingConfig:
    def setup_method(self):
        """Reset _CONFIGURED flag before each test."""
        import tokenpak.logging_config as lc
        lc._CONFIGURED = False
        # Remove any existing handlers from the tokenpak logger
        logger = logging.getLogger("tokenpak")
        for h in list(logger.handlers):
            logger.removeHandler(h)

    def test_configure_default(self):
        from tokenpak.logging_config import TPK_LOGGER_NAME, configure_logging
        configure_logging()
        logger = logging.getLogger(TPK_LOGGER_NAME)
        assert logger.level == logging.INFO
        assert len(logger.handlers) >= 1

    def test_configure_debug_level(self):
        from tokenpak.logging_config import TPK_LOGGER_NAME, configure_logging
        configure_logging(level="DEBUG")
        logger = logging.getLogger(TPK_LOGGER_NAME)
        assert logger.level == logging.DEBUG

    def test_env_var_level(self):
        import tokenpak.logging_config as lc
        with patch.dict(os.environ, {"TPK_LOG_LEVEL": "ERROR"}):
            configure_logging = lc.configure_logging
            configure_logging()
        logger = logging.getLogger("tokenpak")
        assert logger.level == logging.ERROR

    def test_json_format(self):
        from tokenpak.logging_config import TPK_LOGGER_NAME, configure_logging
        stream = StringIO()
        configure_logging(fmt="json")
        logger = logging.getLogger(TPK_LOGGER_NAME)
        # Replace stderr handler's stream with our StringIO
        for h in logger.handlers:
            if hasattr(h, 'stream'):
                h.stream = stream
        logger.warning("test json log")
        output = stream.getvalue()
        if output.strip():
            data = json.loads(output.strip())
            assert data["level"] == "WARNING"
            assert data["message"] == "test json log"

    def test_idempotent(self):
        from tokenpak.logging_config import TPK_LOGGER_NAME, configure_logging
        configure_logging(level="INFO")
        configure_logging(level="DEBUG")  # second call is no-op
        logger = logging.getLogger(TPK_LOGGER_NAME)
        assert logger.level == logging.INFO  # first call wins

    def test_get_logger(self):
        from tokenpak.logging_config import get_logger
        logger = get_logger("tokenpak.foo.bar")
        assert logger.name == "tokenpak.foo.bar"
        logger2 = get_logger("mymodule")
        assert logger2.name == "tokenpak.mymodule"


# ---------------------------------------------------------------------------
# Bare except audit
# ---------------------------------------------------------------------------

class TestBareExceptAudit:
    def test_no_bare_except_in_runtime_proxy(self):
        """Verify runtime/proxy.py has zero bare except: clauses."""
        from pathlib import Path
        proxy_path = Path(__file__).parent.parent / "tokenpak" / "runtime" / "proxy.py"
        if not proxy_path.exists():
            pytest.skip("runtime/proxy.py not found")
        content = proxy_path.read_text()
        bare = [
            (i+1, line) for i, line in enumerate(content.splitlines())
            if line.strip() == "except:"
        ]
        assert bare == [], f"Found bare except: at lines: {[ln for ln, _ in bare]}"
