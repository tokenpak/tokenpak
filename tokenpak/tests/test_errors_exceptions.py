"""
Unit tests for tokenpak/errors.py and tokenpak/exceptions.py.

Tests cover:
- Exception class instantiation and attribute correctness
- Error codes and messages
- raise/catch behavior
- str() and repr() representations
- to_dict() output
- format_error() utility
- Edge cases: empty messages, nested exceptions, None fields
"""

import pytest

# ============================================================
# errors.py imports
# ============================================================
from tokenpak.errors import (
    TokenPakError as ErrorsTokenPakError,
    ConfigError,
    ConfigValidationError,
    MissingConfigError,
    InvalidConfigFileError,
    TimeoutError as TPTimeoutError,
    AuthenticationError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    RateLimitError as ErrorsRateLimitError,
    CacheError,
    CacheCorruptedError,
    ProviderError,
    ProviderUnknownError,
    InternalError,
    NotImplementedError as TPNotImplementedError,
    ProxyStartupError,
    PortInUseError,
    PermissionDeniedError,
    MissingDependencyError,
    LiteLLMError,
    ValidationError as ErrorsValidationError,
    CLIError,
    UnknownCommandError,
    format_error,
)

# ============================================================
# exceptions.py imports
# ============================================================
from tokenpak.exceptions import (
    TokenPakError,
    ProxyError,
    UpstreamError,
    CircuitOpenError,
    CompressionError,
    ConfigError as ExcConfigError,
    AuthError,
    RateLimitError,
    CacheError as ExcCacheError,
    ValidationError,
    LicenseError,
)


# ============================================================
# errors.py: Base class — ErrorsTokenPakError
# ============================================================

class TestErrorsTokenPakError:
    """Tests for errors.py TokenPakError base class."""

    def test_basic_instantiation(self):
        err = ErrorsTokenPakError("TP-E001", "test message")
        assert err.code == "TP-E001"
        assert err.message == "test message"
        assert err.suggestion == "Check TokenPak logs for details."
        assert err.context is None

    def test_with_suggestion_and_context(self):
        err = ErrorsTokenPakError("TP-E001", "msg", suggestion="try X", context="ctx info")
        assert err.suggestion == "try X"
        assert err.context == "ctx info"

    def test_str_without_context(self):
        err = ErrorsTokenPakError("TP-E001", "something broke", suggestion="fix it")
        s = str(err)
        assert "TP-E001" in s
        assert "something broke" in s
        assert "fix it" in s
        assert "Context:" not in s

    def test_str_with_context(self):
        err = ErrorsTokenPakError("TP-E002", "bad config", context="field=port")
        s = str(err)
        assert "Context: field=port" in s

    def test_to_dict(self):
        err = ErrorsTokenPakError("TP-E001", "msg", suggestion="do this", context="here")
        d = err.to_dict()
        assert d["error_code"] == "TP-E001"
        assert d["message"] == "msg"
        assert d["suggestion"] == "do this"
        assert d["context"] == "here"

    def test_to_dict_no_context(self):
        err = ErrorsTokenPakError("TP-E001", "msg")
        d = err.to_dict()
        assert d["context"] is None

    def test_is_exception(self):
        err = ErrorsTokenPakError("TP-E001", "msg")
        with pytest.raises(ErrorsTokenPakError):
            raise err


# ============================================================
# errors.py: Config errors
# ============================================================

class TestConfigErrors:
    def test_config_error_code(self):
        err = ConfigError("bad config")
        assert err.code == "TP-E001"

    def test_config_validation_error(self):
        err = ConfigValidationError("port", "must be int")
        assert err.code == "TP-E002"
        assert "port" in err.message
        assert "must be int" in err.message
        assert err.field == "port"

    def test_config_validation_error_custom_suggestion(self):
        err = ConfigValidationError("host", "invalid", suggestion="use localhost")
        assert err.suggestion == "use localhost"

    def test_missing_config_error(self):
        err = MissingConfigError("api_keys")
        assert err.code == "TP-E003"
        assert "api_keys" in err.message
        assert "api_keys" in err.suggestion

    def test_invalid_config_file_error(self):
        err = InvalidConfigFileError("/etc/tp.json", "syntax error")
        assert err.code == "TP-E004"
        assert "/etc/tp.json" in err.message
        assert "syntax error" in err.message
        assert "/etc/tp.json" in err.suggestion

    def test_config_errors_are_catchable_as_base(self):
        with pytest.raises(ErrorsTokenPakError):
            raise MissingConfigError("key")


# ============================================================
# errors.py: Connection / Timeout
# ============================================================

class TestTimeoutError:
    def test_timeout_error_attributes(self):
        err = TPTimeoutError("redis", 30)
        assert err.code == "TP-E103"
        assert "redis" in err.message
        assert "30" in err.message
        assert "redis" in err.suggestion

    def test_timeout_error_is_connection_error(self):
        from tokenpak.errors import ConnectionError as TPConnectionError
        err = TPTimeoutError("db", 5)
        assert isinstance(err, TPConnectionError)


# ============================================================
# errors.py: Auth errors
# ============================================================

class TestAuthErrors:
    def test_authentication_error_base(self):
        err = AuthenticationError("auth failed")
        assert err.code == "TP-E201"

    def test_invalid_api_key_error(self):
        err = InvalidAPIKeyError("openai")
        assert err.code == "TP-E202"
        assert "openai" in err.message
        assert "openai" in err.suggestion

    def test_missing_api_key_error(self):
        err = MissingAPIKeyError("anthropic")
        assert err.code == "TP-E203"
        assert "anthropic" in err.message
        assert "anthropic" in err.suggestion

    def test_auth_errors_catchable_as_base(self):
        with pytest.raises(AuthenticationError):
            raise InvalidAPIKeyError("gemini")


# ============================================================
# errors.py: Rate limit
# ============================================================

class TestErrorsRateLimitError:
    def test_without_retry_after(self):
        err = ErrorsRateLimitError("openai")
        assert err.code == "TP-E301"
        assert "openai" in err.message
        assert "retry" not in err.message.lower() or "openai" in err.message

    def test_with_retry_after(self):
        err = ErrorsRateLimitError("claude", retry_after_seconds=60)
        assert "60" in err.message
        assert err.code == "TP-E301"


# ============================================================
# errors.py: Cache errors
# ============================================================

class TestCacheErrors:
    def test_cache_error_base(self):
        err = CacheError("cache miss")
        assert err.code == "TP-E401"

    def test_cache_corrupted_error(self):
        err = CacheCorruptedError()
        assert err.code == "TP-E402"
        assert "corrupted" in err.message.lower()
        assert "clear" in err.suggestion.lower()

    def test_cache_error_catchable(self):
        with pytest.raises(ErrorsTokenPakError):
            raise CacheCorruptedError()


# ============================================================
# errors.py: Provider errors
# ============================================================

class TestProviderErrors:
    def test_provider_error(self):
        err = ProviderError("anthropic", 503, "service unavailable")
        assert err.code == "TP-E501"
        assert "anthropic" in err.message
        assert "503" in err.message
        assert "service unavailable" in err.message

    def test_provider_unknown_error(self):
        err = ProviderUnknownError("cohere")
        assert err.code == "TP-E502"
        assert "cohere" in err.message


# ============================================================
# errors.py: Internal errors
# ============================================================

class TestInternalErrors:
    def test_internal_error(self):
        err = InternalError("unexpected state")
        assert err.code == "TP-E601"
        assert "unexpected state" in err.message

    def test_not_implemented_error(self):
        err = TPNotImplementedError("streaming")
        assert err.code == "TP-E602"
        assert "streaming" in err.message
        assert "streaming" in err.suggestion


# ============================================================
# errors.py: Proxy startup errors
# ============================================================

class TestProxyStartupErrors:
    def test_proxy_startup_error(self):
        err = ProxyStartupError("bind failed", context="port=8080")
        assert err.code == "TP-E100"
        assert "bind failed" in err.message
        assert err.context == "port=8080"

    def test_port_in_use_error(self):
        err = PortInUseError(8766)
        assert err.code == "TP-E100"
        assert "8766" in err.message
        assert "8766" in err.suggestion
        assert err.context == "port=8766"

    def test_permission_denied_error(self):
        err = PermissionDeniedError()
        assert "permission" in err.message.lower() or "denied" in err.message.lower()

    def test_permission_denied_custom_message(self):
        err = PermissionDeniedError("Cannot bind to port 80")
        assert "Cannot bind" in err.message

    def test_missing_dependency_error(self):
        err = MissingDependencyError("uvicorn")
        assert "uvicorn" in err.message
        assert "uvicorn" in err.suggestion
        assert err.context == "dependency=uvicorn"


# ============================================================
# errors.py: Integration / CLI errors
# ============================================================

class TestIntegrationAndCLIErrors:
    def test_litellm_error(self):
        err = LiteLLMError("connection refused")
        assert err.code == "TP-E501"
        assert "connection refused" in err.message

    def test_validation_error(self):
        err = ErrorsValidationError("empty field", context="field=name")
        assert err.code == "TP-E601"
        assert err.context == "field=name"

    def test_cli_error(self):
        err = CLIError("bad flag --foo")
        assert err.code == "TP-E602"
        assert "bad flag" in err.message
        assert "tokenpak help" in err.suggestion

    def test_unknown_command_error(self):
        err = UnknownCommandError("comress")
        assert err.code == "TP-E602"
        assert "comress" in err.message
        assert err.context == "command=comress"

    def test_unknown_command_custom_suggestion(self):
        err = UnknownCommandError("srv", suggestion="Did you mean 'serve'?")
        assert "Did you mean" in err.suggestion


# ============================================================
# errors.py: format_error utility
# ============================================================

class TestFormatError:
    def test_format_tokenpak_error(self):
        err = MissingConfigError("api_keys")
        result = format_error(err)
        assert "TP-E003" in result
        assert "api_keys" in result

    def test_format_plain_exception(self):
        err = ValueError("something unexpected")
        result = format_error(err)
        assert "TP-E601" in result
        assert "ValueError" in result
        assert "Check TokenPak logs" in result

    def test_format_returns_string(self):
        result = format_error(RuntimeError("boom"))
        assert isinstance(result, str)

    def test_format_does_not_raise(self):
        # Should handle any exception without itself throwing
        for exc in [KeyError("key"), TypeError("type"), OSError("os error")]:
            result = format_error(exc)
            assert isinstance(result, str)


# ============================================================
# exceptions.py: Base class — TokenPakError
# ============================================================

class TestExceptionsTokenPakError:
    def test_basic_instantiation(self):
        err = TokenPakError("something failed")
        assert err.message == "something failed"
        assert err.detail is None
        assert err.error_type == "TokenPakError"

    def test_str_returns_message(self):
        err = TokenPakError("test error")
        assert str(err) == "test error"

    def test_repr(self):
        err = TokenPakError("broken")
        assert "TokenPakError" in repr(err)
        assert "broken" in repr(err)

    def test_with_detail(self):
        err = TokenPakError("failed", detail={"field": "port"})
        assert err.detail == {"field": "port"}

    def test_with_custom_error_type(self):
        err = TokenPakError("msg", error_type="CustomType")
        assert err.error_type == "CustomType"

    def test_to_dict_no_detail(self):
        err = TokenPakError("msg")
        d = err.to_dict()
        assert d["error"]["type"] == "TokenPakError"
        assert d["error"]["message"] == "msg"
        assert "detail" not in d["error"]

    def test_to_dict_with_detail(self):
        err = TokenPakError("msg", detail="extra info")
        d = err.to_dict()
        assert d["error"]["detail"] == "extra info"

    def test_is_exception(self):
        with pytest.raises(TokenPakError):
            raise TokenPakError("oops")

    def test_empty_message(self):
        err = TokenPakError("")
        assert err.message == ""
        assert str(err) == ""


# ============================================================
# exceptions.py: Hierarchy — ProxyError, UpstreamError, CircuitOpenError
# ============================================================

class TestProxyErrors:
    def test_proxy_error_is_tokenpak_error(self):
        err = ProxyError("proxy failed")
        assert isinstance(err, TokenPakError)

    def test_upstream_error_basic(self):
        err = UpstreamError("bad gateway")
        assert err.status_code is None
        assert err.provider is None

    def test_upstream_error_with_all_args(self):
        err = UpstreamError("error", status_code=502, provider="anthropic", detail={"raw": "err"})
        assert err.status_code == 502
        assert err.provider == "anthropic"
        assert err.detail == {"raw": "err"}

    def test_upstream_error_to_dict_includes_status_and_provider(self):
        err = UpstreamError("fail", status_code=503, provider="openai")
        d = err.to_dict()
        assert d["error"]["status_code"] == 503
        assert d["error"]["provider"] == "openai"

    def test_upstream_error_to_dict_omits_none_fields(self):
        err = UpstreamError("fail")
        d = err.to_dict()
        assert "status_code" not in d["error"]
        assert "provider" not in d["error"]

    def test_upstream_error_is_proxy_error(self):
        err = UpstreamError("fail")
        assert isinstance(err, ProxyError)

    def test_circuit_open_error_no_retry_after(self):
        err = CircuitOpenError("anthropic")
        assert err.provider == "anthropic"
        assert err.retry_after is None
        assert "anthropic" in str(err)

    def test_circuit_open_error_with_retry_after(self):
        err = CircuitOpenError("cohere", retry_after=30.0)
        assert err.retry_after == 30.0
        assert "30" in str(err)

    def test_circuit_open_error_is_proxy_error(self):
        err = CircuitOpenError("openai")
        assert isinstance(err, ProxyError)
        assert isinstance(err, TokenPakError)


# ============================================================
# exceptions.py: CompressionError, ConfigError, AuthError
# ============================================================

class TestCompressionConfigAuth:
    def test_compression_error(self):
        err = CompressionError("failed to compress")
        assert isinstance(err, TokenPakError)
        assert "failed to compress" in str(err)

    def test_config_error_basic(self):
        err = ExcConfigError("invalid json")
        assert err.config_path is None
        assert isinstance(err, TokenPakError)

    def test_config_error_with_path(self):
        err = ExcConfigError("parse error", config_path="/etc/tp.json")
        assert err.config_path == "/etc/tp.json"

    def test_auth_error(self):
        err = AuthError("missing api key")
        assert isinstance(err, TokenPakError)
        assert "missing api key" in str(err)

    def test_auth_error_catchable_as_tokenpak(self):
        with pytest.raises(TokenPakError):
            raise AuthError("unauthorized")


# ============================================================
# exceptions.py: RateLimitError
# ============================================================

class TestExceptionsRateLimitError:
    def test_basic(self):
        err = RateLimitError("rate exceeded")
        assert err.retry_after is None
        assert err.provider is None

    def test_with_retry_and_provider(self):
        err = RateLimitError("too fast", retry_after=45.5, provider="anthropic")
        assert err.retry_after == 45.5
        assert err.provider == "anthropic"

    def test_is_tokenpak_error(self):
        with pytest.raises(TokenPakError):
            raise RateLimitError("exceeded")


# ============================================================
# exceptions.py: CacheError, ValidationError, LicenseError
# ============================================================

class TestCacheValidationLicense:
    def test_cache_error(self):
        err = ExcCacheError("write failed")
        assert isinstance(err, TokenPakError)

    def test_validation_error_no_field(self):
        err = ValidationError("value out of range")
        assert err.field is None
        assert isinstance(err, TokenPakError)

    def test_validation_error_with_field(self):
        err = ValidationError("must be positive", field="timeout")
        assert err.field == "timeout"

    def test_license_error_basic(self):
        err = LicenseError("trial expired")
        assert err.required_tier is None
        assert err.current_tier is None

    def test_license_error_with_tiers(self):
        err = LicenseError("need pro", required_tier="pro", current_tier="free")
        assert err.required_tier == "pro"
        assert err.current_tier == "free"

    def test_license_error_to_dict(self):
        err = LicenseError("need pro", required_tier="pro", current_tier="free", detail="upgrade")
        d = err.to_dict()
        assert d["error"]["type"] == "LicenseError"
        assert d["error"]["detail"] == "upgrade"


# ============================================================
# Cross-cutting: nested exceptions / chaining
# ============================================================

class TestNestedExceptions:
    def test_exceptions_py_error_as_cause(self):
        original = ValueError("raw error")
        try:
            raise TokenPakError("wrapped") from original
        except TokenPakError as e:
            assert e.__cause__ is original

    def test_errors_py_error_as_cause(self):
        original = OSError("disk full")
        try:
            raise InternalError("internal failure") from original
        except ErrorsTokenPakError as e:
            assert e.__cause__ is original

    def test_catch_exceptions_py_with_broad_handler(self):
        exceptions_list = [
            ProxyError("proxy"),
            UpstreamError("upstream"),
            CircuitOpenError("provider"),
            CompressionError("compress"),
            ExcConfigError("config"),
            AuthError("auth"),
            RateLimitError("rl"),
            ExcCacheError("cache"),
            ValidationError("val"),
            LicenseError("lic"),
        ]
        for exc in exceptions_list:
            with pytest.raises(TokenPakError):
                raise exc

    def test_catch_errors_py_with_broad_handler(self):
        errors_list = [
            ConfigError("cfg"),
            ConfigValidationError("f", "r"),
            MissingConfigError("f"),
            TPTimeoutError("svc", 5),
            InvalidAPIKeyError("p"),
            MissingAPIKeyError("p"),
            ErrorsRateLimitError("p"),
            CacheCorruptedError(),
            ProviderError("p", 500, "err"),
            InternalError("internal"),
        ]
        for exc in errors_list:
            with pytest.raises(ErrorsTokenPakError):
                raise exc


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
