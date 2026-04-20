"""
Config validator for TokenPak proxy startup.

Validates proxy config on boot:
- JSON schema (types, required fields)
- Port range (1024-65535)
- Provider URLs (valid format)
- API key presence per provider
- Cache TTL (positive integer)
- Rate limit values (positive integers)
- File paths exist (log dir, cache dir)

Usage:
    from tokenpak.config_validator import ConfigValidator

    validator = ConfigValidator()
    errors = validator.validate(config_dict)
    if errors:
        for error in errors:
            print(f"ERROR: {error['field']} — {error['message']}")
            print(f"FIX: {error['suggestion']}")
        sys.exit(1)
"""

import os
from typing import Any, Dict, List
from urllib.parse import urlparse


class ConfigValidationError:
    """Represents a single config validation error."""

    def __init__(
        self,
        field: str,
        expected: str,
        actual: Any,
        message: str,
        suggestion: str,
    ):
        self.field = field
        self.expected = expected
        self.actual = actual
        self.message = message
        self.suggestion = suggestion

    def __str__(self) -> str:
        return (
            f"Field: {self.field}\n"
            f"  Expected: {self.expected}\n"
            f"  Actual: {self.actual}\n"
            f"  Message: {self.message}\n"
            f"  Fix: {self.suggestion}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "suggestion": self.suggestion,
        }


class ConfigValidator:
    """Validates TokenPak proxy configuration."""

    REQUIRED_FIELDS = ["api_keys"]
    OPTIONAL_FIELDS = [
        "port",
        "log_dir",
        "cache_dir",
        "cache_ttl",
        "rate_limit_requests",
        "rate_limit_window",
        "provider_urls",
    ]

    def __init__(self):
        self.errors: List[ConfigValidationError] = []

    def validate(self, config: Dict[str, Any]) -> List[ConfigValidationError]:
        """
        Validate config dict. Returns list of errors (empty if valid).
        """
        self.errors = []

        # Check required fields
        self._validate_required_fields(config)

        # Check field types and values
        self._validate_types(config)
        self._validate_values(config)
        self._validate_paths(config)

        return self.errors

    def _validate_required_fields(self, config: Dict[str, Any]) -> None:
        """Check that all required fields are present."""
        for field in self.REQUIRED_FIELDS:
            if field not in config:
                self.errors.append(
                    ConfigValidationError(
                        field=field,
                        expected="present",
                        actual="missing",
                        message="Required field missing",
                        suggestion=f'Add "{field}" to config (required for proxy operation)',
                    )
                )

    def _validate_types(self, config: Dict[str, Any]) -> None:
        """Validate field types."""
        # api_keys should be dict
        if "api_keys" in config:
            if not isinstance(config["api_keys"], dict):
                self.errors.append(
                    ConfigValidationError(
                        field="api_keys",
                        expected="dict (provider → key mapping)",
                        actual=type(config["api_keys"]).__name__,
                        message="api_keys must be a dict",
                        suggestion='Change api_keys to: {"anthropic": "sk-...", "openai": "sk-..."}',
                    )
                )

        # port should be int
        if "port" in config:
            if not isinstance(config["port"], int):
                self.errors.append(
                    ConfigValidationError(
                        field="port",
                        expected="integer",
                        actual=type(config["port"]).__name__,
                        message="port must be an integer",
                        suggestion="Change port to an integer (e.g., 8766)",
                    )
                )

        # cache_ttl should be int
        if "cache_ttl" in config:
            if not isinstance(config["cache_ttl"], int):
                self.errors.append(
                    ConfigValidationError(
                        field="cache_ttl",
                        expected="integer (seconds)",
                        actual=type(config["cache_ttl"]).__name__,
                        message="cache_ttl must be an integer",
                        suggestion="Change cache_ttl to integer seconds (e.g., 3600)",
                    )
                )

        # rate_limit values should be int
        for field in ["rate_limit_requests", "rate_limit_window"]:
            if field in config:
                if not isinstance(config[field], int):
                    self.errors.append(
                        ConfigValidationError(
                            field=field,
                            expected="integer",
                            actual=type(config[field]).__name__,
                            message=f"{field} must be an integer",
                            suggestion=f"Change {field} to integer (e.g., 100)",
                        )
                    )

    def _validate_values(self, config: Dict[str, Any]) -> None:
        """Validate field value ranges and formats."""
        # Port range (only if it's an int — type check already done)
        if "port" in config and isinstance(config["port"], int):
            port = config["port"]
            if not (1024 <= port <= 65535):
                self.errors.append(
                    ConfigValidationError(
                        field="port",
                        expected="1024-65535",
                        actual=port,
                        message="Port must be in range 1024-65535",
                        suggestion=f"Change port to a free port in range 1024-65535.\n  Try: netstat -tuln | grep {port} (if in use, pick another)\n  Example: port: 8767",
                    )
                )

        # Cache TTL (only if it's an int)
        if "cache_ttl" in config and isinstance(config["cache_ttl"], int):
            ttl = config["cache_ttl"]
            if ttl <= 0:
                self.errors.append(
                    ConfigValidationError(
                        field="cache_ttl",
                        expected="positive integer (seconds)",
                        actual=ttl,
                        message="Cache TTL must be positive",
                        suggestion="Set cache_ttl to a positive number of seconds.\n  Common values: 3600 (1 hour), 86400 (24 hours)\n  Example: cache_ttl: 3600",
                    )
                )

        # Rate limit values (only if they're ints)
        if "rate_limit_requests" in config and isinstance(config["rate_limit_requests"], int):
            val = config["rate_limit_requests"]
            if val <= 0:
                self.errors.append(
                    ConfigValidationError(
                        field="rate_limit_requests",
                        expected="positive integer (requests)",
                        actual=val,
                        message="Rate limit requests must be positive",
                        suggestion="Set rate_limit_requests to requests per window (e.g., 100 requests).\n  Example: rate_limit_requests: 100",
                    )
                )

        if "rate_limit_window" in config and isinstance(config["rate_limit_window"], int):
            val = config["rate_limit_window"]
            if val <= 0:
                self.errors.append(
                    ConfigValidationError(
                        field="rate_limit_window",
                        expected="positive integer (seconds)",
                        actual=val,
                        message="Rate limit window must be positive",
                        suggestion="Set rate_limit_window to seconds (e.g., 60 for per-minute).\n  Example: rate_limit_window: 60",
                    )
                )

        # Provider URLs format
        if "provider_urls" in config:
            urls = config["provider_urls"]
            if isinstance(urls, dict):
                for provider, url in urls.items():
                    if not self._is_valid_url(str(url)):
                        valid_url_example = (
                            "https://api.openrouter.ai/v1"
                            if "openrouter" in str(provider).lower()
                            else "https://api.provider.com"
                        )
                        self.errors.append(
                            ConfigValidationError(
                                field=f"provider_urls.{provider}",
                                expected="valid HTTPS URL (scheme://host)",
                                actual=url,
                                message=f"Invalid URL for provider {provider}",
                                suggestion=f'Use a valid HTTPS URL: "{valid_url_example}"\n  Must include: scheme (https://) and host (e.g., api.example.com)\n  Example: provider_urls:\n    {provider}: "{valid_url_example}"',
                            )
                        )

    def _validate_paths(self, config: Dict[str, Any]) -> None:
        """Validate that file paths exist."""
        for field in ["log_dir", "cache_dir"]:
            if field in config:
                path = config[field]
                if not os.path.exists(path):
                    self.errors.append(
                        ConfigValidationError(
                            field=field,
                            expected="existing directory",
                            actual=path,
                            message=f"Directory does not exist: {path}",
                            suggestion=f'Create directory: mkdir -p "{path}"',
                        )
                    )

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """Check if string is a valid URL."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    def is_valid(self, config: Dict[str, Any]) -> bool:
        """Check if config is valid (no errors)."""
        return len(self.validate(config)) == 0

    def validate_file(self, filepath: str) -> bool:
        """Load and validate config file. Returns True if valid."""
        import json

        try:
            with open(filepath, "r") as f:
                config = json.load(f)
        except FileNotFoundError:
            print(f"ERROR: Config file not found: {filepath}")
            return False
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in {filepath}: {e}")
            return False

        errors = self.validate(config)
        if errors:
            print(f"Config validation failed ({len(errors)} error(s)):")
            for error in errors:
                print(f"\n  {error}")
            return False

        print(f"Config is valid: {filepath}")
        return True
