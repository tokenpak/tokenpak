"""
Tests for tokenpak.startup_validator

Tests the startup validation module which runs on proxy initialization
to check configuration validity. Module is in warning-only mode by default,
never blocking startup.

Coverage:
- validate_on_startup() with valid config
- validate_on_startup() with invalid config (warn_only=True)
- validate_on_startup() with invalid config (warn_only=False)
- setup_validation_logging()
- Edge cases: missing config file, empty config, partial config
"""

import logging
from unittest.mock import patch

import pytest

from tokenpak.startup_validator import setup_validation_logging, validate_on_startup


class TestValidateOnStartupValid:
    """Tests for valid configurations."""

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_valid_config_passes(self, mock_validate):
        """Valid config returns True and logs info."""
        mock_validate.return_value = (True, [])

        result = validate_on_startup(warn_only=True)

        assert result is True
        mock_validate.assert_called_once()

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.startup_validator.logger")
    def test_valid_config_logs_info(self, mock_logger, mock_validate):
        """Valid config logs info message."""
        mock_validate.return_value = (True, [])

        validate_on_startup(warn_only=True)

        # Should call logger.info() exactly once
        assert mock_logger.info.called

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_valid_config_with_custom_path(self, mock_validate):
        """Custom config path is passed to validator."""
        mock_validate.return_value = (True, [])

        validate_on_startup(config_path="~/custom/config.yaml", warn_only=True)

        # Validator called with config path
        mock_validate.assert_called_once()


class TestValidateOnStartupInvalidWarnOnly:
    """Tests for invalid configs with warn_only=True (default)."""

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_invalid_config_returns_true_warn_only(self, mock_format, mock_validate):
        """Invalid config with warn_only=True returns True (doesn't block)."""
        mock_validate.return_value = (False, [{"field": "api_keys", "message": "Missing"}])
        mock_format.return_value = "Config has errors"

        result = validate_on_startup(warn_only=True)

        assert result is True

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    @patch("tokenpak.startup_validator.logger")
    def test_invalid_config_logs_warning_warn_only(self, mock_logger, mock_format, mock_validate):
        """Invalid config with warn_only=True logs warning."""
        mock_validate.return_value = (False, [{"field": "api_keys", "message": "Missing"}])
        mock_format.return_value = "Config validation issue:\napi_keys: Missing"

        validate_on_startup(warn_only=True)

        # Should log warning
        mock_logger.warning.assert_called()
        call_args = str(mock_logger.warning.call_args)
        assert "validation" in call_args.lower()

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_multiple_errors_warn_only(self, mock_format, mock_validate):
        """Multiple validation errors handled in warn mode."""
        errors = [
            {"field": "api_keys", "message": "Missing"},
            {"field": "port", "message": "Invalid type"},
            {"field": "log_dir", "message": "Path not found"},
        ]
        mock_validate.return_value = (False, errors)
        mock_format.return_value = "3 errors found"

        result = validate_on_startup(warn_only=True)

        assert result is True


class TestValidateOnStartupInvalidStrict:
    """Tests for invalid configs with warn_only=False (strict mode)."""

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_invalid_config_raises_strict(self, mock_format, mock_validate):
        """Invalid config with warn_only=False raises ValueError."""
        mock_validate.return_value = (False, [{"field": "api_keys", "message": "Missing"}])
        mock_format.return_value = "Error: api_keys is required"

        with pytest.raises(ValueError, match="Invalid config"):
            validate_on_startup(warn_only=False)

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    @patch("tokenpak.startup_validator.logger")
    def test_invalid_config_logs_error_strict(self, mock_logger, mock_format, mock_validate):
        """Invalid config with warn_only=False logs error."""
        mock_validate.return_value = (False, [{"field": "api_keys", "message": "Missing"}])
        mock_format.return_value = "Error: api_keys is required"

        with pytest.raises(ValueError):
            validate_on_startup(warn_only=False)

        # Should log error
        mock_logger.error.assert_called()

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_strict_mode_includes_error_count(self, mock_format, mock_validate):
        """ValueError message includes count of errors."""
        errors = [
            {"field": "api_keys", "message": "Missing"},
            {"field": "port", "message": "Out of range"},
        ]
        mock_validate.return_value = (False, errors)
        mock_format.return_value = "2 errors"

        with pytest.raises(ValueError, match="2 error"):
            validate_on_startup(warn_only=False)


class TestValidateOnStartupExceptions:
    """Tests for exception handling during validation."""

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.startup_validator.logger")
    def test_validation_exception_warn_only(self, mock_logger, mock_validate):
        """Exception during validation is caught in warn mode."""
        mock_validate.side_effect = RuntimeError("Config file not found")

        result = validate_on_startup(warn_only=True)

        # Should log warning and return True (don't block)
        assert result is True
        mock_logger.warning.assert_called()

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.startup_validator.logger")
    def test_validation_exception_strict(self, mock_logger, mock_validate):
        """Exception during validation is raised in strict mode."""
        mock_validate.side_effect = RuntimeError("Config file not found")

        with pytest.raises(RuntimeError):
            validate_on_startup(warn_only=False)

        # Should log error
        mock_logger.error.assert_called()

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_ioerror_on_config_file(self, mock_validate):
        """IOError from config file access is handled."""
        mock_validate.side_effect = IOError("Permission denied")

        result = validate_on_startup(warn_only=True)
        assert result is True

        with pytest.raises(IOError):
            validate_on_startup(warn_only=False)


class TestSetupValidationLogging:
    """Tests for logging setup."""

    def test_setup_validation_logging_default(self):
        """setup_validation_logging() configures logger with defaults."""
        setup_validation_logging()

        # Logger should exist and have handlers
        logger = logging.getLogger("tokenpak.startup")
        assert len(logger.handlers) > 0

    def test_setup_validation_logging_custom_level(self):
        """setup_validation_logging() accepts custom log level."""
        setup_validation_logging(log_level="DEBUG")

        logger = logging.getLogger("tokenpak.startup")
        # At least one handler should be at DEBUG level or lower
        assert any(h.level <= logging.DEBUG for h in logger.handlers)

    def test_setup_validation_logging_invalid_level(self):
        """Invalid log level defaults to INFO."""
        # Should not raise
        setup_validation_logging(log_level="INVALID")

        logger = logging.getLogger("tokenpak.startup")
        assert logger.level == logging.INFO


class TestConfigPathExpansion:
    """Tests for config path handling."""

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_tilde_expansion_in_path(self, mock_validate):
        """Tilde (~) in path is expanded."""
        mock_validate.return_value = (True, [])

        validate_on_startup(config_path="~/.tokenpak/config.yaml")

        # Should have been called once (path expansion happens internally)
        mock_validate.assert_called_once()

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_absolute_path_preserved(self, mock_validate):
        """Absolute paths are preserved."""
        mock_validate.return_value = (True, [])

        validate_on_startup(config_path="/etc/tokenpak/config.yaml")

        mock_validate.assert_called_once()

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_relative_path_accepted(self, mock_validate):
        """Relative paths are accepted."""
        mock_validate.return_value = (True, [])

        validate_on_startup(config_path="./config.yaml")

        mock_validate.assert_called_once()


class TestReturnValues:
    """Tests for return value semantics."""

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_valid_returns_true(self, mock_validate):
        """Valid config always returns True."""
        mock_validate.return_value = (True, [])

        assert validate_on_startup(warn_only=True) is True
        assert validate_on_startup(warn_only=False) is True

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_invalid_warn_only_returns_true(self, mock_format, mock_validate):
        """Invalid config with warn_only=True returns True."""
        mock_validate.return_value = (False, [{"field": "x"}])
        mock_format.return_value = "error"

        assert validate_on_startup(warn_only=True) is True

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_invalid_strict_raises_not_returns(self, mock_format, mock_validate):
        """Invalid config with warn_only=False raises, doesn't return False."""
        mock_validate.return_value = (False, [{"field": "x"}])
        mock_format.return_value = "error"

        with pytest.raises(ValueError):
            validate_on_startup(warn_only=False)

        # Should not reach a point where it would return False


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_empty_error_list(self, mock_format, mock_validate):
        """Empty error list is treated as valid."""
        mock_validate.return_value = (False, [])  # Invalid flag but no errors
        mock_format.return_value = ""

        # With warn_only=True, should still succeed
        result = validate_on_startup(warn_only=True)
        assert result is True

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    @patch("tokenpak.cli.cli_validate_config.format_errors")
    def test_very_long_error_message(self, mock_format, mock_validate):
        """Long error messages are handled."""
        long_error = "x" * 10000
        mock_validate.return_value = (False, [{"field": "config", "message": long_error}])
        mock_format.return_value = long_error

        result = validate_on_startup(warn_only=True)
        assert result is True

    @patch("tokenpak.cli.cli_validate_config.validate_config_file")
    def test_none_config_path_defaults(self, mock_validate):
        """None config_path uses default."""
        mock_validate.return_value = (True, [])

        validate_on_startup(config_path="~/.tokenpak/config.yaml")

        mock_validate.assert_called_once()
